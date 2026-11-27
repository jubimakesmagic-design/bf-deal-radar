"""Unit tests for cross-source clustering, real-discount scoring, and customs calculation."""

from datetime import datetime, timezone

from bf_deal_radar.dedup import cluster_deals
from bf_deal_radar.exchange import estimate_customs
from bf_deal_radar.models import AppConfig, FilterConfig, RawDeal, ScoredDeal
from bf_deal_radar.verifier import verify_and_score


def _make_config() -> AppConfig:
    return AppConfig(
        min_score=60,
        max_age_hours=24,
        min_price_usd=5.0,
        us_customs_threshold_usd=200.0,
        filters=FilterConfig(
            watch_keywords=["airpods", "wh-1000xm", "4tb"],
            boost_keywords=["ssd", "headphone", "monitor", "sony"],
            exclude_keywords=["doordash", "verizon", "in-store only"],
            condition_penalty_keywords=["refurbished", "open box"],
        ),
    )


def test_cross_source_clustering_merges_and_boosts_score():
    now = datetime.now(timezone.utc)
    sd = RawDeal(
        source_id="slickdeals_fp",
        source_name="Slickdeals Frontpage",
        source_type="slickdeals",
        source_base_score=24,
        category_hint="인기 핫딜",
        external_id="sd-1",
        title="Sony WH-1000XM5 Wireless Noise Canceling Headphones $228 + Free S&H",
        url="https://slickdeals.net/f/12345",
        description="43% off at Amazon, all-time low!",
        published_at=now,
        retailer="Amazon",
        price_usd=228.0,
        discount_pct=43.0,
        thumb_score=75,
    )
    tb = RawDeal(
        source_id="techbargains",
        source_name="TechBargains",
        source_type="techbargains",
        source_base_score=14,
        category_hint="IT/PC",
        external_id="tb-1",
        title="Sony WH-1000XM5 Wireless Headphones $228",
        url="https://www.amazon.com/gp/product/B09XS7JWHH",
        published_at=now,
        retailer="Amazon",
        price_usd=228.0,
        asin="B09XS7JWHH",
    )

    clustered = cluster_deals([sd, tb])
    assert len(clustered) == 1
    merged = clustered[0]
    assert merged.primary.asin == "B09XS7JWHH"
    assert merged.primary.thumb_score == 75
    assert len(merged.merged_sources) == 2

    scored = verify_and_score(merged, _make_config(), usd_krw_rate=1350.0, now=now)
    assert scored.excluded_reason is None
    assert scored.score >= 90
    assert scored.is_watch_match is True
    assert scored.is_historical_low is True
    assert scored.customs is not None
    assert scored.customs.is_duty_free is False  # $228 > $200
    assert any("교차 검증" in r for r in scored.reasons)


def test_excludes_us_only_services():
    now = datetime.now(timezone.utc)
    deal = ScoredDeal(
        canonical_key="url:abc",
        primary=RawDeal(
            source_id="slickdeals_fp",
            source_name="Slickdeals Frontpage",
            source_type="slickdeals",
            source_base_score=24,
            category_hint="인기 핫딜",
            external_id="1",
            title="DoorDash 50% off pickup orders",
            url="https://slickdeals.net/f/999",
            published_at=now,
            price_usd=15.0,
            discount_pct=50.0,
        ),
        merged_sources=["Slickdeals Frontpage"],
    )
    scored = verify_and_score(deal, _make_config(), usd_krw_rate=1350.0, now=now)
    assert scored.excluded_reason is not None
    assert "doordash" in scored.excluded_reason


def test_roundup_article_gets_penalized_below_threshold():
    now = datetime.now(timezone.utc)
    deal = ScoredDeal(
        canonical_key="url:roundup",
        primary=RawDeal(
            source_id="verge_deals",
            source_name="The Verge Deals",
            source_type="verge",
            source_base_score=14,
            category_hint="IT/가전",
            external_id="v1",
            title="The best Black Friday deals we've already found",
            url="https://www.theverge.com/roundup",
            description="Lots of early deals are live now.",
            published_at=now,
        ),
        merged_sources=["The Verge Deals"],
    )
    scored = verify_and_score(deal, _make_config(), usd_krw_rate=1350.0, now=now)
    assert scored.score < 30


def test_customs_threshold_calculation():
    duty_free = estimate_customs(189.99, usd_krw_rate=1400.0, threshold_usd=200.0, is_tech=True)
    assert duty_free.is_duty_free is True
    assert "면세" in duty_free.label

    taxable_tech = estimate_customs(299.0, usd_krw_rate=1400.0, threshold_usd=200.0, is_tech=True)
    assert taxable_tech.is_duty_free is False
    assert taxable_tech.estimated_tax_rate_low == 0.10
    assert taxable_tech.estimated_total_krw_low > taxable_tech.price_krw


def test_9to5_sister_sites_collapse_and_pcmag_roundup_penalized():
    now = datetime.now(timezone.utc)
    cfg = _make_config()

    # 9to5Toys + 9to5Mac must NOT earn a cross-site verification boost
    sister = ScoredDeal(
        canonical_key="url:9to5",
        primary=RawDeal(
            source_id="9to5mac",
            source_name="9to5Mac Deals",
            source_type="9to5toys",
            source_base_score=14,
            category_hint="IT/가전",
            external_id="m1",
            title="Apple AirPods Pro 3 drop to $149 at Amazon",
            url="https://9to5mac.com/deals/airpods-pro-3",
            published_at=now,
            price_usd=149.0,
            discount_pct=40.0,
        ),
        merged_sources=["9to5Mac Deals", "9to5Toys"],
    )
    verify_and_score(sister, cfg, usd_krw_rate=1350.0, now=now)
    assert not any("교차 검증" in r for r in sister.reasons)

    # PCMag LIVE roundup & 9to5 Thanksgiving roundup are heavily penalized
    for title in (
        "LIVE: 51+ of the Best Black Friday Tech Deals",
        "Thanksgiving deals: M5 MacBook Air $400 off, AirPods Pro 3 $149",
    ):
        roundup = ScoredDeal(
            canonical_key=f"url:{title[:10]}",
            primary=RawDeal(
                source_id="pcmag_deals",
                source_name="PCMag Deals",
                source_type="generic",
                source_base_score=14,
                category_hint="IT/가전",
                external_id="p1",
                title=title,
                url="https://www.pcmag.com/deals/live-bf",
                description="50% off all-time low deals roundup",
                published_at=now,
                discount_pct=50.0,
            ),
            merged_sources=["PCMag Deals"],
        )
        verify_and_score(roundup, cfg, usd_krw_rate=1350.0, now=now)
        assert roundup.score < 55


def test_cluster_merges_downvotes_and_buy_url_and_config_defaults(tmp_path):
    from bf_deal_radar.config import load_config

    now = datetime.now(timezone.utc)
    d1 = RawDeal(
        source_id="dasaja_coupang",
        source_name="다사자 쿠팡 핫딜",
        source_type="dasaja",
        source_base_score=22,
        category_hint="쿠팡핫딜",
        external_id="d1",
        title="[쿠팡] 테스트 노트북 거치대 알루미늄 15,900원",
        url="https://dasaja.co.kr/saja_korea/999",
        region="KR",
        published_at=now,
        price_krw=15900,
    )
    d2 = RawDeal(
        source_id="dealbada_kr",
        source_name="딜바다 국내핫딜",
        source_type="dealbada",
        source_base_score=18,
        category_hint="국내핫딜",
        external_id="db1",
        title="[쿠팡] 테스트 노트북 거치대 알루미늄 15,900원",
        url="https://www.dealbada.com/bbs/board.php?wr_id=999",
        buy_url="https://www.coupang.com/vp/products/99999",
        region="KR",
        published_at=now,
        price_krw=15900,
        downvote_count=7,
    )
    clustered = cluster_deals([d1, d2])
    assert len(clustered) == 1
    assert clustered[0].primary.buy_url == "https://www.coupang.com/vp/products/99999"
    assert clustered[0].primary.downvote_count == 7

    # Missing YAML falls back to uniform 65/65 thresholds
    cfg = load_config(config_path=tmp_path / "nonexistent.yaml", env_path=tmp_path / "no.env")
    assert cfg.min_score == 65
    assert cfg.kr_min_score == 65


def test_us_price_mistake_fast_track_and_guards():
    now = datetime.now(timezone.utc)
    cfg = _make_config()
    cfg.min_score = 70

    # Brand-new sub-$5 US price mistake immediately passes 70+
    us_err = ScoredDeal(
        canonical_key="url:us-err",
        primary=RawDeal(
            source_id="dansdeals",
            source_name="Dan's Deals",
            source_type="generic",
            source_base_score=14,
            category_hint="인기 핫딜",
            external_id="uspe1",
            title="Price Mistake: Bose Headphones for $2.99 at Amazon",
            url="https://www.dansdeals.com/price-mistake-bose",
            published_at=now,
            retailer="Amazon",
            price_usd=2.99,
        ),
        merged_sources=["Dan's Deals"],
    )
    verify_and_score(us_err, cfg, usd_krw_rate=1350.0, now=now)
    assert us_err.excluded_reason is None
    assert us_err.is_price_error is True
    assert us_err.score >= 70
    assert us_err.reasons[0] == "🚨 가격오류(Price Mistake) 속보"

    # Expired / dead price mistake does NOT fast-track
    dead = ScoredDeal(
        canonical_key="url:us-dead",
        primary=RawDeal(
            source_id="dansdeals",
            source_name="Dan's Deals",
            source_type="generic",
            source_base_score=14,
            category_hint="인기 핫딜",
            external_id="uspe2",
            title="[Dead] Price Mistake: Bose Headphones for $29 at Amazon",
            url="https://www.dansdeals.com/dead-bose",
            published_at=now,
            retailer="Amazon",
            price_usd=29.0,
        ),
        merged_sources=["Dan's Deals"],
    )
    verify_and_score(dead, cfg, usd_krw_rate=1350.0, now=now)
    assert dead.is_price_error is False
    assert dead.score < 70



