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
from bf_deal_radar.sources.korean import resolve_korean_buy_urls
from bf_deal_radar.sources.rss import fetch_all_sources, fetch_all_sources_with_cache
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
    max_alerts = limit_override if limit_override is not None else config.max_alerts_per_run

    def _cutoff_for(deal: ScoredDeal) -> int:
        if min_score_override is not None:
            return min_score_override
        return config.kr_min_score if deal.is_domestic_kr else config.min_score

    usd_krw_rate = fetch_usd_krw_rate(
        fallback=config.fallback_usd_krw,
        override=config.override_usd_krw,
    )
    report = RunReport(usd_krw_rate=usd_krw_rate)

    with DealStorage(config.db_path) as storage:
        is_first_run = storage.is_empty()
        cache_updates: list[tuple[str, str | None, str | None]] = []

        if not dry_run:
            # On non-dry runs, send ETag/If-Modified-Since (after first run) and record new cache headers
            cached_headers = None if is_first_run else storage.get_feed_cache()
            raw_deals, cache_updates = asyncio.run(
                fetch_all_sources_with_cache(config.sources, feed_cache=cached_headers)
            )
        else:
            raw_deals = asyncio.run(fetch_all_sources(config.sources))

        for source_id, etag, last_mod in cache_updates:
            storage.update_feed_cache(source_id, etag, last_mod)

        report.raw_count = len(raw_deals)
        if not raw_deals:
            logger.info("No new or modified deals across enabled sources.")
            return report

        clustered = cluster_deals(raw_deals)
        report.clustered_count = len(clustered)

        now = datetime.now(timezone.utc)
        notifier = (
            ConsoleNotifier()
            if (dry_run or seed_only)
            else TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
        )

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

            # Watch keywords already earn +8/+10 inside verifier; keep cutoff strictly uniform
            if deal.score < _cutoff_for(deal):
                # Only persist sub-threshold deals that carry a USD price for price_history tracking
                if not dry_run and deal.primary.price_usd is not None:
                    storage.record_deal(deal, notified=False)
                continue

            report.qualified_count += 1
            all_qualified.append(deal)
            if not dry_run and not storage.should_notify(deal):
                report.already_seen_count += 1
                continue

            candidates.append(deal)

        # Rank live price-error alerts first, then purely by verified deal score
        rank_key = lambda d: (
            1 if d.is_price_error else 0,
            d.score,
            1 if d.is_historical_low else 0,
            d.primary.published_at.timestamp() if d.primary.published_at else 0.0,
        )
        all_qualified.sort(key=rank_key, reverse=True)
        candidates.sort(key=rank_key, reverse=True)
        report.all_qualified_deals = all_qualified

        # Avoid first-run notification flood while still showing top deals immediately
        effective_limit = min(max_alerts, 5) if (is_first_run and not dry_run and limit_override is None) else max_alerts
        to_send = candidates[:effective_limit]
        overflow = candidates[effective_limit:]

        if to_send and not seed_only:
            asyncio.run(resolve_korean_buy_urls(to_send))

        if to_send and not seed_only and config.llm.is_configured:
            enrich_deals_with_llm(to_send, config.llm)
            kept: list[ScoredDeal] = []
            for deal in to_send:
                if deal.score < _cutoff_for(deal):
                    logger.info(
                        "Skipping deal downgraded below cutoff by LLM (%d < %d): %s",
                        deal.score,
                        _cutoff_for(deal),
                        deal.primary.title[:50],
                    )
                    if not dry_run and deal.primary.price_usd is not None:
                        storage.record_deal(deal, notified=False)
                else:
                    kept.append(deal)
            to_send = kept
            to_send.sort(key=rank_key, reverse=True)

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
                    if isinstance(notifier, TelegramNotifier):
                        time.sleep(0.4)

        # On first run, mark overflow deals as seen so subsequent 5-min crons only alert NEW deals
        if not dry_run:
            for deal in overflow:
                if is_first_run or seed_only or deal.primary.price_usd is not None:
                    storage.record_deal(deal, notified=is_first_run or seed_only)
            storage.prune_old(days=14)

    return report
