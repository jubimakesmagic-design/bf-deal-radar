"""Unit tests for SQLite persistence, Telegram formatting, and pipeline orchestration."""

from datetime import datetime, timezone
from pathlib import Path

from bf_deal_radar.exchange import estimate_customs
from bf_deal_radar.models import AppConfig, FilterConfig, RawDeal, ScoredDeal, SourceConfig
from bf_deal_radar.notifier.telegram import build_telegram_payload, format_telegram_html
from bf_deal_radar.pipeline import run_once
from bf_deal_radar.storage import DealStorage


def _sample_deal(price: float = 28.0, asin: str | None = "B091FXQQMQ") -> ScoredDeal:
    p = RawDeal(
        source_id="dealnews_elec",
        source_name="DealNews Electronics",
        source_type="dealnews",
        source_base_score=14,
        category_hint="IT/가전",
        external_id="dn-1",
        title="Kasa Smart Plug Mini 4-Pack <Special> & More",
        url="https://www.amazon.com/dp/B091FXQQMQ",
        published_at=datetime.now(timezone.utc),
        retailer="Amazon",
        price_usd=price,
        original_price_usd=44.99,
        discount_pct=37.0,
        coupon_code="KASA10",
        asin=asin,
    )
    return ScoredDeal(
        canonical_key="asin:B091FXQQMQ",
        primary=p,
        merged_sources=["DealNews Electronics", "Slickdeals Frontpage"],
        score=88,
        is_tech_category=True,
        is_historical_low=True,
        reasons=["🏆 역대 최저가(All-Time Low) 언급", "💥 37% 대폭 할인"],
        customs=estimate_customs(price, usd_krw_rate=1350.0, is_tech=True),
    )


def test_storage_dedup_and_price_drop_renotify(tmp_path: Path):
    db_file = tmp_path / "radar.db"
    deal = _sample_deal(price=100.0)

    with DealStorage(db_file) as store:
        assert store.is_empty() is True
        assert store.should_notify(deal) is True

        store.record_deal(deal, notified=True)
        assert store.is_empty() is False
        # Same price -> do not notify again
        assert store.should_notify(_sample_deal(price=100.0)) is False
        # Small drop (5%) -> do not spam
        assert store.should_notify(_sample_deal(price=95.0)) is False
        # Significant drop (>= 10%) -> notify again!
        assert store.should_notify(_sample_deal(price=85.0)) is True
        assert store.get_previous_low_price("asin:B091FXQQMQ") == 100.0


def test_telegram_html_escaping_and_buttons():
    deal = _sample_deal(price=28.0, asin="B091FXQQMQ")
    text = format_telegram_html(deal)
    assert "&lt;Special&gt; &amp; More" in text
    assert "$28.00" in text
    assert "37% OFF" in text
    assert "<code>KASA10</code>" in text

    payload = build_telegram_payload("99999", deal)
    assert payload["parse_mode"] == "HTML"
    buttons = payload["reply_markup"]["inline_keyboard"]
    assert buttons[0][0]["url"] == "https://www.amazon.com/dp/B091FXQQMQ"
    assert buttons[1][0]["url"] == "https://camelcamelcamel.com/product/B091FXQQMQ"
    assert buttons[1][1]["url"] == "https://keepa.com/#!product/1-B091FXQQMQ"

    # Non-Amazon deal falls back to CCC search button
    deal_no_asin = _sample_deal(price=28.0, asin=None)
    payload2 = build_telegram_payload("99999", deal_no_asin)
    assert "camelcamelcamel.com/search?sq=" in payload2["reply_markup"]["inline_keyboard"][1][0]["url"]


def test_pipeline_end_to_end_mocked(tmp_path: Path, monkeypatch):
    fake_deal = RawDeal(
        source_id="slickdeals_fp",
        source_name="Slickdeals Frontpage",
        source_type="slickdeals",
        source_base_score=24,
        category_hint="인기 핫딜",
        external_id="sd-1",
        title="Apple AirPods Pro 3 $149 (was $249) All-Time Low",
        url="https://www.amazon.com/dp/B0TESTAIRP",
        description="40% off at Amazon, all-time low!",
        published_at=datetime.now(timezone.utc),
        retailer="Amazon",
        price_usd=149.0,
        original_price_usd=249.0,
        discount_pct=40.0,
        thumb_score=65,
        asin="B0TESTAIRP",
    )

    async def _fake_fetch(_sources):
        return [fake_deal]

    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_all_sources", _fake_fetch)
    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_usd_krw_rate", lambda **_: 1350.0)

    cfg = AppConfig(
        min_score=60,
        db_path=str(tmp_path / "test.db"),
        sources=[SourceConfig(id="slickdeals_fp", name="Slickdeals Frontpage", type="slickdeals", url="https://x")],
        filters=FilterConfig(watch_keywords=["airpods"]),
    )

    report = run_once(cfg, dry_run=True)
    assert report.raw_count == 1
    assert report.qualified_count == 1
    assert report.notified_count == 1
    assert 80 <= report.notified_deals[0].score < 90
