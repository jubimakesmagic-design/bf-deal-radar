"""Optional Naver Shopping Search API market-price verifier for Korean domestic deals."""

from __future__ import annotations

import logging
import statistics

import httpx

from bf_deal_radar.models import NaverConfig, NaverMarketCheck, ScoredDeal
from bf_deal_radar.parser import clean_kr_product_query
from bf_deal_radar.security import redact_secrets, register_secrets

logger = logging.getLogger(__name__)

NAVER_SHOP_API_URL = "https://openapi.naver.com/v1/search/shop.json"


def _query_naver_market(
    client: httpx.Client,
    query: str,
    config: NaverConfig,
) -> tuple[int, int, int] | None:
    """Return (lowest_krw, median_krw, sample_count) from Naver Shopping search results."""
    resp = client.get(
        NAVER_SHOP_API_URL,
        params={"query": query, "display": 15, "sort": "sim"},
        headers={
            "X-Naver-Client-Id": config.client_id,
            "X-Naver-Client-Secret": config.client_secret,
        },
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    prices: list[int] = []
    for item in items:
        try:
            lp = int(item.get("lprice", 0))
            if lp >= 1000:
                prices.append(lp)
        except (ValueError, TypeError):
            continue

    if len(prices) < 3:
        return None
    prices.sort()
    # Trim top/bottom extreme outlier if we have enough samples
    trimmed = prices[1:-1] if len(prices) >= 6 else prices
    return prices[0], int(round(statistics.median(trimmed) / 100.0) * 100), len(prices)


def enrich_kr_deals_with_naver(deals: list[ScoredDeal], config: NaverConfig) -> None:
    """Optionally compare Korean deal prices against live Naver Shopping market prices."""
    if not config.is_configured or not deals:
        return

    register_secrets(config.client_id, config.client_secret)
    kr_targets = [
        d for d in deals if d.is_domestic_kr and d.primary.price_krw is not None
    ][: config.max_calls_per_run]
    if not kr_targets:
        return

    with httpx.Client(timeout=config.timeout_seconds) as client:
        for deal in kr_targets:
            query = clean_kr_product_query(deal.primary.title)
            if len(query) < 3:
                continue
            try:
                res = _query_naver_market(client, query, config)
                if not res:
                    continue
                lowest_krw, median_krw, sample_count = res
                deal_price = deal.primary.price_krw
                assert deal_price is not None
                if median_krw <= 0:
                    continue

                savings_pct = round((1.0 - (deal_price / median_krw)) * 100.0, 1)
                if savings_pct >= 25.0:
                    adj = 20
                    verdict = f"📊 네이버 시세(약 {median_krw:,}원) 대비 {savings_pct:.0f}% 저렴 (찐특가!)"
                elif savings_pct >= 15.0:
                    adj = 12
                    verdict = f"📊 네이버 시세(약 {median_krw:,}원) 대비 {savings_pct:.0f}% 저렴"
                elif savings_pct >= 5.0:
                    adj = 4
                    verdict = f"📊 네이버 최저가({lowest_krw:,}원) 수준 / 평균 대비 {savings_pct:.0f}% 저렴"
                elif savings_pct <= -10.0:
                    adj = -18
                    verdict = f"⚠️ 네이버 평균 시세(약 {median_krw:,}원)보다 비쌈"
                else:
                    adj = 0
                    verdict = f"📊 네이버 시세(약 {median_krw:,}원)와 비슷"

                deal.naver_market = NaverMarketCheck(
                    market_lowest_krw=lowest_krw,
                    market_median_krw=median_krw,
                    savings_vs_median_pct=savings_pct,
                    sample_count=sample_count,
                    verdict_label=verdict,
                )
                deal.score = max(0, min(100, deal.score + adj))
                deal.reasons.insert(0, verdict)
            except Exception as exc:
                safe_err = redact_secrets(str(exc), (config.client_id, config.client_secret))
                logger.warning(
                    "Naver market check skipped for '%s' (%s): %s",
                    deal.primary.title[:35],
                    type(exc).__name__,
                    safe_err,
                )
