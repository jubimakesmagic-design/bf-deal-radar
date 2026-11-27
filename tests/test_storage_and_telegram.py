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


def test_storage_skips_redundant_writes_and_price_history_duplicates(tmp_path: Path):
    import hashlib

    db_file = tmp_path / "compact.db"
    deal = _sample_deal(price=100.0)

    with DealStorage(db_file) as store:
        store.record_deal(deal, notified=False)
        store.update_feed_cache("dealnews_elec", '"etag-1"', "Fri, 27 Nov 2026 05:00:00 GMT")
    hash_before = hashlib.sha256(db_file.read_bytes()).hexdigest()

    # Re-recording the exact same deal & cache headers must NOT bloat price_history or dirty the DB file
    with DealStorage(db_file) as store:
        store.record_deal(_sample_deal(price=100.0), notified=False)
        store.update_feed_cache("dealnews_elec", '"etag-1"', "Fri, 27 Nov 2026 05:00:00 GMT")
        hist_cnt = store._conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        assert hist_cnt == 1
    hash_after = hashlib.sha256(db_file.read_bytes()).hexdigest()
    assert hash_after == hash_before

    # Actual price drop DOES insert a new price_history row
    with DealStorage(db_file) as store:
        store.record_deal(_sample_deal(price=85.0), notified=True)
        hist_cnt = store._conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        assert hist_cnt == 2
        assert store.get_previous_low_price("asin:B091FXQQMQ") == 85.0


def test_strict_uniform_cutoff_and_clean_telegram_labels(tmp_path: Path, monkeypatch):
    from bf_deal_radar.verifier import classify_korean_category

    # Korean fresh fruit/produce categorization
    assert classify_korean_category("[네이버] 사과 부사 고당도 꿀사과 2kg 4,900원", "사과") == "🍔 식품/간식"

    # Duplicate platform variants collapse cleanly in Telegram message
    kr_deal = _sample_deal(price=28.0)
    kr_deal.merged_sources = ["다사자 네이버 핫딜", "다사자 BEST 핫딜", "어미새 인기정보", "핫구사·어미새"]
    html_text = format_telegram_html(kr_deal)
    assert "📡 다사자 네이버 핫딜, 어미새 인기정보" in html_text
    assert "다사자 BEST 핫딜" not in html_text

    # Watch-keyword deal below 70 must NOT bypass min_score=70
    sub70_watch = RawDeal(
        source_id="dealnews_elec",
        source_name="DealNews Electronics",
        source_type="dealnews",
        source_base_score=14,
        category_hint="IT/가전",
        external_id="w-1",
        title="Apple AirPods 4 for $119",
        url="https://www.amazon.com/dp/B0SUB70TEST",
        description="30% off at Amazon",
        published_at=datetime.now(timezone.utc),
        retailer="Amazon",
        price_usd=119.0,
        discount_pct=30.0,
        asin="B0SUB70TEST",
    )

    async def _fake_fetch(_sources):
        return [sub70_watch]

    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_all_sources", _fake_fetch)
    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_usd_krw_rate", lambda **_: 1350.0)

    cfg = AppConfig(
        min_score=70,
        db_path=str(tmp_path / "strict.db"),
        sources=[SourceConfig(id="dealnews_elec", name="DealNews Electronics", type="dealnews", url="https://x")],
        filters=FilterConfig(watch_keywords=["airpods"]),
    )
    report = run_once(cfg, dry_run=True)
    assert report.qualified_count == 0
    assert report.notified_count == 0


def test_storage_cross_source_alias_prevents_duplicate_alerts(tmp_path: Path):
    from bf_deal_radar.dedup import make_url_key

    db_file = tmp_path / "alias.db"
    dasaja_url = "https://dasaja.co.kr/saja_korea/610203"
    hotgusa_url = "https://www.hotgusa.com/product/7_10599250685"

    merged_first = ScoredDeal(
        canonical_key=make_url_key(dasaja_url) or "url:dasaja",
        primary=RawDeal(
            source_id="dasaja_naver",
            source_name="다사자 네이버 핫딜",
            source_type="dasaja",
            source_base_score=18,
            category_hint="국내핫딜",
            external_id="d1",
            title="[네이버] 사과 2kg 4,900원",
            url=dasaja_url,
            region="KR",
            price_krw=4900,
        ),
        merged_sources=["다사자 네이버 핫딜", "핫구사·에펨코리아"],
        merged_urls={
            "다사자 네이버 핫딜": dasaja_url,
            "핫구사·에펨코리아": hotgusa_url,
        },
        score=90,
    )

    hotgusa_only = ScoredDeal(
        canonical_key=make_url_key(hotgusa_url) or "url:hotgusa",
        primary=RawDeal(
            source_id="hotgusa_lowest",
            source_name="핫구사·에펨코리아",
            source_type="hotgusa",
            source_base_score=18,
            category_hint="국내핫딜",
            external_id="h1",
            title="[네이버] 사과 2kg 4,900원",
            url=hotgusa_url,
            region="KR",
            price_krw=4900,
        ),
        merged_sources=["핫구사·에펨코리아"],
        merged_urls={"핫구사·에펨코리아": hotgusa_url},
        score=82,
    )

    with DealStorage(db_file) as store:
        assert store.should_notify(merged_first) is True
        store.record_deal(merged_first, notified=True)
        # Next run: Dasaja timed out or 304'd, only Hotgusa survives -> MUST NOT duplicate alert!
        assert store.should_notify(hotgusa_only) is False
        # Significant >=10% price drop on Hotgusa alias DOES re-notify
        hotgusa_only.primary.price_krw = 4200
        assert store.should_notify(hotgusa_only) is True


def test_llm_downgrade_enforces_cutoff_and_kr_prompt(tmp_path: Path, monkeypatch):
    from bf_deal_radar.llm import _build_user_prompt
    from bf_deal_radar.models import LLMConfig, LLMEnrichment

    # Korean deal prompt uses Region=KR and Price KRW instead of USD
    kr_deal = ScoredDeal(
        canonical_key="url:kr-llm",
        primary=RawDeal(
            source_id="dasaja_coupang",
            source_name="다사자 쿠팡 핫딜",
            source_type="dasaja",
            source_base_score=22,
            category_hint="쿠팡핫딜",
            external_id="k1",
            title="[쿠팡] 테스트 상품 19,900원",
            url="https://dasaja.co.kr/saja_korea/1",
            region="KR",
            retailer="쿠팡",
            price_krw=19900,
            shipping_label="무료배송",
            discount_pct=40.0,
            thumb_score=15,
            comment_count=12,
        ),
        merged_sources=["다사자 쿠팡 핫딜"],
        score=78,
    )
    prompt = _build_user_prompt(kr_deal)
    assert "Region: KR" in prompt
    assert "19,900원" in prompt
    assert "Price USD" not in prompt

    # Pipeline drops a deal if LLM score_adjustment pushes it below min_score
    borderline = RawDeal(
        source_id="dealnews_elec",
        source_name="DealNews Electronics",
        source_type="dealnews",
        source_base_score=14,
        category_hint="IT/가전",
        external_id="llm-drop",
        title="Sony WH-1000XM5 Headphones for $248 All-Time Low",
        url="https://www.amazon.com/dp/B0LLMDROPT",
        description="40% off, all-time low!",
        published_at=datetime.now(timezone.utc),
        retailer="Amazon",
        price_usd=248.0,
        discount_pct=40.0,
        staff_pick=True,
        asin="B0LLMDROPT",
    )

    async def _fake_fetch(_sources):
        return [borderline]

    def _fake_enrich(deals, _llm_cfg):
        for d in deals:
            d.llm = LLMEnrichment(verdict="주의필요", score_adjustment=-15)
            d.score = max(0, d.score - 15)

    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_all_sources", _fake_fetch)
    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_usd_krw_rate", lambda **_: 1350.0)
    monkeypatch.setattr("bf_deal_radar.pipeline.enrich_deals_with_llm", _fake_enrich)

    cfg = AppConfig(
        min_score=70,
        db_path=str(tmp_path / "llm.db"),
        sources=[SourceConfig(id="dealnews_elec", name="DealNews Electronics", type="dealnews", url="https://x")],
        llm=LLMConfig(enabled=True, gemini_api_key="dummy-key-for-test"),
    )
    report = run_once(cfg, dry_run=True)
    assert report.qualified_count == 1
    assert report.notified_count == 0


