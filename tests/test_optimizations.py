"""Unit tests for v0.3 optimizations: accessory guard, clean CCC queries, ETag 304 caching, and warnings."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from bf_deal_radar.models import AppConfig, FilterConfig, RawDeal, ScoredDeal, SourceConfig
from bf_deal_radar.notifier.telegram import format_telegram_html
from bf_deal_radar.parser import clean_product_query, extract_price_info
from bf_deal_radar.sources.rss import fetch_all_sources_with_cache
from bf_deal_radar.storage import DealStorage
from bf_deal_radar.verifier import classify_korean_category, verify_and_score


def test_accessory_guard_blocks_false_watch_matches():
    now = datetime.now(timezone.utc)
    cfg = AppConfig(
        min_score=66,
        max_age_hours=24,
        filters=FilterConfig(watch_keywords=["macbook", "apple watch"]),
    )

    hub = ScoredDeal(
        canonical_key="url:hub",
        primary=RawDeal(
            source_id="dealnews_comp",
            source_name="DealNews Computers",
            source_type="dealnews",
            source_base_score=14,
            category_hint="IT/PC",
            external_id="1",
            title="Anker USB-C Hub for MacBook for $30 + free shipping",
            url="https://example.com/hub",
            published_at=now,
            price_usd=30.0,
            discount_pct=45.0,
        ),
        merged_sources=["DealNews Computers"],
    )
    verify_and_score(hub, cfg, usd_krw_rate=1350.0, now=now)
    assert hub.is_watch_match is False

    band = ScoredDeal(
        canonical_key="url:band",
        primary=RawDeal(
            source_id="9to5toys",
            source_name="9to5Toys",
            source_type="9to5toys",
            source_base_score=14,
            category_hint="IT/가전",
            external_id="2",
            title="Pitaka Carbon Fiber Apple Watch band at $63",
            url="https://example.com/band",
            published_at=now,
            price_usd=63.0,
            discount_pct=20.0,
        ),
        merged_sources=["9to5Toys"],
    )
    verify_and_score(band, cfg, usd_krw_rate=1350.0, now=now)
    assert band.is_watch_match is False

    real_macbook = ScoredDeal(
        canonical_key="url:mac",
        primary=RawDeal(
            source_id="slickdeals_fp",
            source_name="Slickdeals Frontpage",
            source_type="slickdeals",
            source_base_score=24,
            category_hint="IT/PC",
            external_id="3",
            title='15.3" Apple MacBook Air M5 16GB/512GB $999',
            url="https://example.com/mac",
            published_at=now,
            price_usd=999.0,
            savings_usd=400.0,
        ),
        merged_sources=["Slickdeals Frontpage"],
    )
    verify_and_score(real_macbook, cfg, usd_krw_rate=1350.0, now=now)
    assert real_macbook.is_watch_match is True


def test_clean_product_query_strips_price_and_shipping_noise():
    assert (
        clean_product_query("Garmin Instinct 3 AMOLED 45mm GPS Smartwatch for $250 + free shipping")
        == "Garmin Instinct 3 AMOLED 45mm GPS Smartwatch"
    )
    assert (
        clean_product_query("Prime Members: Kasa Smart Plug Mini 4-Pack $28 + Free S&H")
        == "Kasa Smart Plug Mini 4-Pack"
    )


def test_ignores_tiny_card_promos_and_blog_bleed():
    # 5% RedCard promo should not be counted as a real discount
    redcard = extract_price_info("Apple Watch $400", "Save 5% off with Target RedCard.")
    assert redcard.discount_pct is None

    # Huge unrelated savings in description ($900 off on a $200 item -> > 2x price) should not bleed
    bleed = extract_price_info("Budget Tablet down at $200", "Earlier we saw a laptop at $900 off!")
    assert bleed.price_usd == 200.0
    assert bleed.savings_usd is None


def test_bulky_and_membership_warnings_and_krw_savings_in_telegram():
    now = datetime.now(timezone.utc)
    cfg = AppConfig(min_score=60, max_age_hours=24, filters=FilterConfig())

    tv = ScoredDeal(
        canonical_key="url:tv",
        primary=RawDeal(
            source_id="slickdeals_fp",
            source_name="Slickdeals Frontpage",
            source_type="slickdeals",
            source_base_score=24,
            category_hint="IT/가전",
            external_id="tv1",
            title='Costco Members: 65" LG C6 4K OLED TV for $1,076 + Free S&H',
            url="https://example.com/tv",
            published_at=now,
            price_usd=1076.0,
            discount_pct=40.0,
            savings_usd=700.0,
        ),
        merged_sources=["Slickdeals Frontpage"],
    )
    verify_and_score(tv, cfg, usd_krw_rate=1350.0, now=now)
    assert tv.bulky_warning is not None
    assert tv.membership_warning is not None
    assert tv.voltage_warning is not None
    assert tv.category_label_ko == "🖥️ 모니터/TV"

    msg = format_telegram_html(tv)
    assert "대형/고중량" in msg
    assert "미국 회원제" in msg
    assert "절약" in msg


def test_korean_category_classification():
    assert classify_korean_category("AMD Ryzen 7 9800X3D Desktop Processor", "IT/PC") == "💾 SSD/PC부품"
    assert classify_korean_category("Bose QuietComfort Headphones", "IT/가전") == "🎧 음향/오디오"
    assert classify_korean_category("Garmin Instinct 3 AMOLED Smartwatch", "IT/가전") == "⌚ 스마트워치/웨어러블"
    assert classify_korean_category("Lenovo Legion 5i Gaming Laptop", "IT/PC") == "💻 노트북/PC"


def test_feed_cache_and_http_304_handling(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "cache_test.db"
    with DealStorage(db_path) as store:
        store.update_feed_cache("src1", 'W/"abc"', "Fri, 27 Nov 2026 01:00:00 GMT")
        cache = store.get_feed_cache()
        assert cache["src1"] == ('W/"abc"', "Fri, 27 Nov 2026 01:00:00 GMT")

    seen_headers: dict[str, str] = {}

    async def _fake_get(self, url, headers=None, follow_redirects=True):
        seen_headers.update(headers or {})
        req = httpx.Request("GET", url)
        return httpx.Response(304, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    src = SourceConfig(id="src1", name="Test Feed", type="generic", url="https://example.com/rss.xml")
    deals, updates = asyncio.run(
        fetch_all_sources_with_cache([src], feed_cache={"src1": ('W/"abc"', "Fri, 27 Nov 2026 01:00:00 GMT")})
    )
    assert deals == []
    assert updates == []
    assert seen_headers.get("If-None-Match") == 'W/"abc"'
    assert seen_headers.get("If-Modified-Since") == "Fri, 27 Nov 2026 01:00:00 GMT"


def test_per_host_concurrency_limit_and_timeout_retry(monkeypatch):
    active = 0
    peak = 0
    attempts: dict[str, int] = {}

    rss_body = (
        b'<?xml version="1.0"?><rss><channel><item>'
        b"<title>Test Deal $20</title><link>https://example.com/d/1</link>"
        b"</item></channel></rss>"
    )

    async def _fake_get(self, url, headers=None, follow_redirects=True):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.02)
            attempts[url] = attempts.get(url, 0) + 1
            if url.endswith("/flaky") and attempts[url] == 1:
                raise httpx.ConnectTimeout("transient timeout")
            req = httpx.Request("GET", url)
            return httpx.Response(200, request=req, content=rss_body)
        finally:
            active -= 1

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    sources = [
        SourceConfig(id=f"s{i}", name=f"Src {i}", type="generic", url=f"https://samehost.example/feed/{i}")
        for i in range(5)
    ] + [
        SourceConfig(id="flaky", name="Flaky", type="generic", url="https://samehost.example/flaky"),
    ]
    deals, _ = asyncio.run(fetch_all_sources_with_cache(sources, max_per_host=2))
    assert peak <= 2
    assert len(deals) == 6
    assert attempts["https://samehost.example/flaky"] == 2

