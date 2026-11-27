"""End-to-end deal radar pipeline orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bf_deal_radar.dedup import cluster_deals
from bf_deal_radar.exchange import fetch_usd_krw_rate
from bf_deal_radar.llm import enrich_deals_with_llm
from bf_deal_radar.models import AppConfig, ScoredDeal
from bf_deal_radar.notifier.console import ConsoleNotifier
from bf_deal_radar.notifier.telegram import TelegramNotifier
from bf_deal_radar.sources.rss import fetch_all_sources
from bf_deal_radar.storage import DealStorage
from bf_deal_radar.verifier import verify_and_score

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunReport:
    usd_krw_rate: float
    raw_count: int = 0
    clustered_count: int = 0
    excluded_count: int = 0
    qualified_count: int = 0
    notified_count: int = 0
    already_seen_count: int = 0
    notified_deals: list[ScoredDeal] = field(default_factory=list)
    all_qualified_deals: list[ScoredDeal] = field(default_factory=list)


def run_once(
    config: AppConfig,
    *,
    dry_run: bool = False,
    seed_only: bool = False,
    min_score_override: int | None = None,
    limit_override: int | None = None,
) -> RunReport:
    """Execute one full collection -> verification -> notification cycle."""
    min_score = min_score_override if min_score_override is not None else config.min_score
    max_alerts = limit_override if limit_override is not None else config.max_alerts_per_run

    usd_krw_rate = fetch_usd_krw_rate(
        fallback=config.fallback_usd_krw,
        override=config.override_usd_krw,
    )
    report = RunReport(usd_krw_rate=usd_krw_rate)

    raw_deals = asyncio.run(fetch_all_sources(config.sources))
    report.raw_count = len(raw_deals)
    if not raw_deals:
        logger.warning("No deals fetched from any enabled source.")
        return report

    clustered = cluster_deals(raw_deals)
    report.clustered_count = len(clustered)

    now = datetime.now(timezone.utc)
    notifier = (
        ConsoleNotifier()
        if (dry_run or seed_only)
        else TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    )

    with DealStorage(config.db_path) as storage:
        is_first_run = storage.is_empty()
        candidates: list[ScoredDeal] = []
        all_qualified: list[ScoredDeal] = []

        for deal in clustered:
            prev_low = storage.get_previous_low_price(deal.canonical_key)
            verify_and_score(
                deal,
                config=config,
                usd_krw_rate=usd_krw_rate,
                now=now,
                previous_low_usd=prev_low,
            )

            if deal.excluded_reason:
                report.excluded_count += 1
                continue

            # VIP watch-keyword matches pass with a lower threshold so you never miss them
            effective_min = max(40, min_score - 15) if deal.is_watch_match else min_score
            if deal.score < effective_min:
                if not dry_run:
                    storage.record_deal(deal, notified=False)
                continue

            report.qualified_count += 1
            all_qualified.append(deal)
            if not dry_run and not storage.should_notify(deal):
                report.already_seen_count += 1
                continue

            candidates.append(deal)

        # Rank highest-value deals first (VIP watch matches first, then score, then freshness)
        rank_key = lambda d: (
            1 if d.is_watch_match else 0,
            d.score,
            d.primary.published_at.timestamp() if d.primary.published_at else 0.0,
        )
        all_qualified.sort(key=rank_key, reverse=True)
        candidates.sort(key=rank_key, reverse=True)
        report.all_qualified_deals = all_qualified

        # Avoid first-run notification flood while still showing top deals immediately
        effective_limit = min(max_alerts, 5) if (is_first_run and not dry_run and limit_override is None) else max_alerts
        to_send = candidates[:effective_limit]
        overflow = candidates[effective_limit:]

        if to_send and not seed_only and config.llm.is_configured:
            enrich_deals_with_llm(to_send, config.llm)
            # Re-sort after AI score adjustment
            to_send.sort(key=lambda d: (1 if d.is_watch_match else 0, d.score), reverse=True)

        for deal in to_send:
            if seed_only:
                storage.record_deal(deal, notified=True)
                continue

            ok = notifier.send_deal(deal)
            if ok:
                report.notified_count += 1
                report.notified_deals.append(deal)
                if not dry_run:
                    storage.record_deal(deal, notified=True)
                    # Gentle spacing for Telegram rate limits
                    if isinstance(notifier, TelegramNotifier):
                        time.sleep(0.4)

        # On first run, mark overflow deals as seen so subsequent 5-min crons only alert NEW deals
        if not dry_run:
            for deal in overflow:
                storage.record_deal(deal, notified=is_first_run or seed_only)
            storage.prune_old(days=30)

    return report
