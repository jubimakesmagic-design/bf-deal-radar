"""Tests for KRW 60-day baseline price history, product signatures, and Naver Shopping live market comparison."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from bf_deal_radar.config import load_config
from bf_deal_radar.dashboard import build_dashboard_payload, render_dashboard_html
from bf_deal_radar.dedup import make_product_signature
from bf_deal_radar.llm import _build_user_prompt
from bf_deal_radar.market import (
    build_naver_baseline,
    evaluate_naver_items,
    fetch_naver_market_baselines,
)
from bf_deal_radar.models import (
    AppConfig,
    MarketBaseline,
    NaverShoppingConfig,
    RawDeal,
    ScoredDeal,
    SourceConfig,
)
from bf_deal_radar.notifier.console import ConsoleNotifier
from bf_deal_radar.notifier.telegram import format_telegram_html
from bf_deal_radar.parser import build_naver_search_query, extract_product_specs
from bf_deal_radar.pipeline import run_once
from bf_deal_radar.security import clear_registered_secrets
from bf_deal_radar.storage import DealStorage
from bf_deal_radar.verifier import verify_and_score


def _kr_raw(
    *,
    title: str,
    price_krw: int,
    url: str,
    retailer: str = "쿠팡",
    shipping_label: str | None = "무료배송",
    discount_pct: float | None = None,
    thumb_score: int = 0,
    comment_count: int = 0,
    source_id: str = "dasaja_coupang",
    source_name: str = "다사자 쿠팡 핫딜",
    base_score: int = 22,
    now: datetime | None = None,
) -> RawDeal:
    return RawDeal(
        source_id=source_id,
        source_name=source_name,
        source_type="dasaja",
        source_base_score=base_score,
        category_hint="국내핫딜",
        external_id=url,
        title=title,
        url=url,
        region="KR",
        published_at=now or datetime.now(timezone.utc),
        retailer=retailer,
        price_krw=price_krw,
        shipping_label=shipping_label,
        discount_pct=discount_pct,
        thumb_score=thumb_score,
        comment_count=comment_count,
    )


def test_product_signature_matches_same_product_and_separates_variants():
    # Same SSD across different malls, spacing, and promo noise -> identical signature
    s1 = make_product_signature("[쿠팡] 삼성전자 990 PRO NVMe SSD 2TB (229,000원/무배)", "KR")
    s2 = make_product_signature("[네이버] 공식정품 삼성 990 PRO NVMe SSD 2 TB 체감가 169,000원 무료배송", "KR")
    assert s1 is not None and s1 == s2

    # Different storage capacity must NEVER collide
    s_1tb = make_product_signature("[쿠팡] 삼성전자 990 PRO NVMe SSD 1TB 119,000원", "KR")
    assert s_1tb is not None and s_1tb != s1

    # Brand alias normalization (소니 == Sony)
    sony_kr = make_product_signature("[쿠팡] 소니 WH-1000XM5 헤드폰 299,000원", "KR")
    sony_en = make_product_signature("[네이버] Sony WH-1000XM5 헤드폰 389,000원", "KR")
    assert sony_kr is not None and sony_kr == sony_en

    # Accessory vs main product must NOT collide
    sony_case = make_product_signature("[쿠팡] 소니 WH-1000XM5 케이스 15,000원", "KR")
    assert sony_case is not None and sony_case != sony_kr

    # Multi-pack equivalence (24캔 == x 24) vs different count (12캔)
    coke_24a = make_product_signature("[쿠팡] 코카콜라 제로 355ml 24캔 14,900원", "KR")
    coke_24b = make_product_signature("[네이버] 코카콜라 제로 355ml x 24 19,900원", "KR")
    coke_12 = make_product_signature("[쿠팡] 코카콜라 제로 355ml 12캔 9,900원", "KR")
    assert coke_24a is not None and coke_24a == coke_24b
    assert coke_12 is not None and coke_12 != coke_24a

    # Multi-item promo ranges return None (never pollute single-product baselines)
    assert make_product_signature("[오늘의집] 우드레이 BEST상품 모음전 8,900원~ (74%)", "KR") is None

    # Clean Naver search query strips 만원/체감가/할인 noise cleanly
    q = build_naver_search_query("[쿠팡] 소니 WH-1000XM5 헤드폰 체감가 29.9만원 (와우무료)")
    assert "소니" in q and "WH-1000XM5" in q
    assert "29.9" not in q and "체감가" not in q


def test_krw_price_history_accumulation_baseline_and_retention(tmp_path: Path):
    db_file = tmp_path / "krw_hist.db"
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    title_a = "[쿠팡] 삼성전자 990 PRO NVMe SSD 2TB 229,000원"
    sig = make_product_signature(title_a, "KR")
    assert sig is not None

    with DealStorage(db_file) as store:
        # Record 3 sightings across separate days (different post URLs, same product signature)
        for idx, (days_ago, price) in enumerate([(30, 229000), (15, 225000), (5, 219000)], start=1):
            d = ScoredDeal(
                canonical_key=f"url:ssd-{idx}",
                product_signature=sig,
                primary=_kr_raw(title=title_a, price_krw=price, url=f"https://dasaja.co.kr/saja_korea/{idx}"),
            )
            assert store.record_krw_price_observation(d, now=now - timedelta(days=days_ago)) is True

        # Re-recording the exact same post/price must NOT write duplicate rows or change DB hash
        dup = ScoredDeal(
            canonical_key="url:ssd-3",
            product_signature=sig,
            primary=_kr_raw(title=title_a, price_krw=219000, url="https://dasaja.co.kr/saja_korea/3"),
        )
        hash_before = hashlib.sha256(db_file.read_bytes()).hexdigest()
        assert store.record_krw_price_observation(dup, now=now - timedelta(days=5) + timedelta(minutes=10)) is False
        assert hashlib.sha256(db_file.read_bytes()).hexdigest() == hash_before

        # Same product at same price on a different community within 12h does NOT inflate sample_count
        same_day_cross = ScoredDeal(
            canonical_key="url:ssd-3-mirror",
            product_signature=sig,
            primary=_kr_raw(title=title_a, price_krw=219000, url="https://bbs.ruliweb.com/read/3"),
        )
        assert store.record_krw_price_observation(same_day_cross, now=now - timedelta(days=5) + timedelta(hours=2)) is False

        # Brand-new drop post at 139,000원 sees 3 prior sightings -> median 225,000원 (-38.2%)
        bl = store.get_krw_price_baseline(sig, "url:ssd-drop", current_price_krw=139000, lookback_days=60, now=now)
        assert bl is not None
        assert bl.source == "price_history"
        assert bl.sample_count == 3
        assert bl.reference_price_krw == 225000
        assert bl.lowest_price_krw == 219000
        assert bl.drop_pct == 38.2

        # Self-exclusion: a single sighting does NOT compare against itself
        solo_title = "[쿠팡] 레고 42172 맥라렌 P1 499,000원"
        solo_sig = make_product_signature(solo_title, "KR")
        solo = ScoredDeal(
            canonical_key="url:lego-1",
            product_signature=solo_sig or "",
            primary=_kr_raw(title=solo_title, price_krw=499000, url="https://dasaja.co.kr/saja_korea/99"),
        )
        store.record_krw_price_observation(solo, now=now)
        assert store.get_krw_price_baseline(solo_sig, "url:lego-1", current_price_krw=499000, now=now) is None

        # Retention: prune_old(days=14, history_days=60) removes 20d seen_deals & 70d history, keeps 30d history
        old_seen = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        old_hist = (datetime.now(timezone.utc) - timedelta(days=70)).isoformat()
        recent_hist = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with store._conn:
            store._conn.execute(
                """
                INSERT INTO seen_deals (canonical_key, title, url, score, notified, first_seen_at, last_seen_at)
                VALUES ('url:old', 'old', 'https://x', 70, 1, ?, ?)
                """,
                (old_seen, old_seen),
            )
            store._conn.execute(
                """
                INSERT INTO krw_price_history (product_sig, canonical_key, title, price_krw, source_id, recorded_at)
                VALUES ('sig:kr:ancient', 'url:anc', 'ancient', 50000, 's', ?),
                       ('sig:kr:mid', 'url:mid', 'mid', 60000, 's', ?)
                """,
                (old_hist, recent_hist),
            )
        pruned_seen = store.prune_old(days=14, history_days=60)
        assert pruned_seen == 1
        sigs_left = {
            r["product_sig"]
            for r in store._conn.execute("SELECT product_sig FROM krw_price_history").fetchall()
        }
        assert "sig:kr:ancient" not in sigs_left
        assert "sig:kr:mid" in sigs_left


def test_naver_evaluate_items_filters_used_accessories_specs_and_outliers():
    # 1. Headphone deal: filters out used (productType=4), discontinued (7), accessories (케이스/이어패드),
    #    older model (WH-1000XM4), and extreme bait outlier (19,000원)
    items = [
        {"title": "<b>소니</b> <b>WH-1000XM5</b> 무선 헤드폰 정품", "lprice": "389000", "productType": "1", "brand": "소니"},
        {"title": "Sony <b>WH-1000XM5</b> 노이즈캔슬링 헤드폰", "lprice": "399000", "productType": "2", "brand": "소니"},
        {"title": "소니 <b>WH-1000XM5</b> 블루투스 헤드폰 블랙", "lprice": "410000", "productType": "2", "brand": "소니"},
        {"title": "소니 <b>WH-1000XM5</b> 헤드폰 실버", "lprice": "420000", "productType": "2", "brand": "소니"},
        # Noise that MUST be rejected:
        {"title": "소니 WH-1000XM5 헤드폰 전용 케이스 파우치", "lprice": "15000", "productType": "2"},
        {"title": "소니 WH-1000XM5 교체용 이어패드", "lprice": "12000", "productType": "2"},
        {"title": "소니 WH-1000XM5 헤드폰 중고 S급", "lprice": "210000", "productType": "4"},
        {"title": "소니 WH-1000XM5 헤드폰 단종", "lprice": "180000", "productType": "7"},
        {"title": "소니 WH-1000XM4 무선 헤드폰", "lprice": "269000", "productType": "1"},
        {"title": "소니 WH-1000XM5 헤드폰 미끼옵션", "lprice": "19000", "productType": "2"},
    ]
    res = evaluate_naver_items("[쿠팡] 소니 WH-1000XM5 헤드폰 229,000원", "소니 WH-1000XM5 헤드폰", items)
    assert res is not None
    ref_krw, low_krw, cnt = res
    assert cnt == 4
    assert low_krw == 389000
    assert ref_krw == 389000  # Capped by official catalog productType=1 price

    # 2. Storage & RAM tier matching: deal specifies 256GB, Naver includes "16GB 256GB" (valid),
    #    rejects "16GB 512GB" (wrong SSD) and "256GB 512GB" (multi-option listing)
    mac_items = [
        {"title": "애플 맥북 에어 M4 13인치 16GB 256GB", "lprice": "1490000", "productType": "1", "brand": "Apple"},
        {"title": "Apple 맥북 에어 M4 16GB 256GB 정품", "lprice": "1520000", "productType": "2", "brand": "Apple"},
        {"title": "맥북 에어 M4 16GB 256GB 실버", "lprice": "1550000", "productType": "2", "brand": "Apple"},
        {"title": "애플 맥북 에어 M4 16GB 512GB", "lprice": "1850000", "productType": "2", "brand": "Apple"},
        {"title": "애플 맥북 에어 M4 256GB 512GB 선택", "lprice": "1390000", "productType": "2", "brand": "Apple"},
    ]
    mac_res = evaluate_naver_items("[쿠팡] 애플 맥북 에어 M4 256GB 1,090,000원", "애플 맥북 에어 M4 256GB", mac_items)
    assert mac_res is not None
    assert mac_res[2] == 3
    assert mac_res[1] == 1490000

    # 3. Bidirectional count guard: single-unit deal rejects 24-pack / 10-pack bundles
    single_items = [
        {"title": "농심 신라면 멀티팩 20개입 박스", "lprice": "19900", "productType": "2"},
        {"title": "농심 신라면 40개입 라면 박스", "lprice": "36900", "productType": "2"},
        {"title": "농심 신라면 10개입", "lprice": "10900", "productType": "2"},
    ]
    assert evaluate_naver_items("[쿠팡] 농심 신라면 3,900원", "농심 신라면", single_items) is None

    # 4. Pack-count deal only matches matching count (24캔)
    coke_specs = extract_product_specs("코카콜라 제로 355ml 24캔")
    assert coke_specs["volume"] == {"355"} and coke_specs["count"] == {"24"}
    coke_items = [
        {"title": "코카콜라 제로 355ml 24캔", "lprice": "22900", "productType": "2"},
        {"title": "코카콜라 제로 355ml x 24", "lprice": "23500", "productType": "2"},
        {"title": "코카콜라 제로 355ml 24입", "lprice": "24000", "productType": "2"},
        {"title": "코카콜라 제로 355ml 48캔", "lprice": "44000", "productType": "2"},
        {"title": "코카콜라 제로 355ml 12캔", "lprice": "12500", "productType": "2"},
    ]
    coke_res = evaluate_naver_items("[쿠팡] 코카콜라 제로 355ml 24캔 14,900원", "코카콜라 제로 355ml 24캔", coke_items)
    assert coke_res is not None
    assert coke_res[2] == 3
    assert coke_res[1] == 22900


def test_verifier_baseline_drop_scoring_fake_discount_penalty_and_renderers(capsys):
    now = datetime.now(timezone.utc)
    cfg = AppConfig(min_score=70, kr_min_score=70, min_price_krw=3000)

    # 1. Brand-new 0-upvote deal with NO % in title, but Naver live baseline shows 41% drop -> crosses 70+!
    deal = ScoredDeal(
        canonical_key="url:sony-drop",
        primary=_kr_raw(
            title="[쿠팡] 소니 WH-1000XM5 헤드폰 229,000원 무료배송",
            price_krw=229000,
            url="https://dasaja.co.kr/saja_korea/555001",
            now=now,
        ),
        merged_sources=["다사자 쿠팡 핫딜"],
    )
    bl = build_naver_baseline(229000, reference_krw=389000, lowest_krw=385000, sample_count=5, query="소니 WH-1000XM5 헤드폰")
    assert bl is not None
    verify_and_score(deal, cfg, usd_krw_rate=1400.0, now=now, kr_market_baseline=bl)
    assert deal.is_price_drop is True
    assert deal.is_historical_low is True
    assert deal.score >= 70
    assert any("네이버 시세(389,000원, 5건) 대비 41% 급락" in r for r in deal.reasons)

    # Telegram, Console, Dashboard, and LLM prompt all include baseline drop info cleanly
    tg = format_telegram_html(deal)
    assert "기준가 대비 급락 포착" in tg
    assert "<s>기준가 389,000원</s>" in tg
    assert "-41% 급락" in tg

    ConsoleNotifier().send_deal(deal)
    stdout = capsys.readouterr().out
    assert "[📉급락]" in stdout
    assert "기준가: 389,000원" in stdout

    payload = build_dashboard_payload([deal], usd_krw_rate=1400.0)
    d0 = payload["deals"][0]
    assert d0["is_price_drop"] is True
    assert d0["baseline_price_krw"] == 389000
    assert d0["baseline_drop_pct"] == 41.1
    html_out = render_dashboard_html(payload)
    assert 'data-filter="drop"' in html_out

    llm_prompt = _build_user_prompt(deal)
    assert "Market Baseline (Naver Shopping, 5 samples): 389,000원" in llm_prompt

    # 2. Extreme 55% Naver crash with 4+ samples -> suspected price-error urgency tier
    crash = ScoredDeal(
        canonical_key="url:sony-crash",
        primary=_kr_raw(
            title="[네이버] 소니 WH-1000XM5 헤드폰 175,000원",
            price_krw=175000,
            url="https://dasaja.co.kr/saja_korea/555002",
            retailer="네이버",
            shipping_label=None,
            source_id="dasaja_naver",
            source_name="다사자 네이버 핫딜",
            base_score=18,
            now=now,
        ),
        merged_sources=["다사자 네이버 핫딜"],
    )
    bl_crash = build_naver_baseline(175000, reference_krw=389000, lowest_krw=385000, sample_count=4, query="소니 WH-1000XM5 헤드폰")
    verify_and_score(crash, cfg, usd_krw_rate=1400.0, now=now, kr_market_baseline=bl_crash)
    assert crash.is_price_drop is True
    assert crash.score >= 70
    assert "가격오류 의심" in crash.reasons[0]
    tg_crash = format_telegram_html(crash)
    assert "🚨🚨" in tg_crash
    assert "기준가 급락 · 가격오류 의심" in tg_crash

    # 3. Overpriced fake "50% 할인" deal (more expensive than Naver baseline) gets penalized below 70!
    fake = ScoredDeal(
        canonical_key="url:fake-50",
        primary=_kr_raw(
            title="[쿠팡] 가짜정가 블렌더 50% 할인 99,000원 무료배송",
            price_krw=99000,
            url="https://dasaja.co.kr/saja_korea/555003",
            discount_pct=50.0,
            thumb_score=15,
            comment_count=8,
            now=now,
        ),
        merged_sources=["다사자 쿠팡 핫딜"],
    )
    # Without market baseline, 50% title + 15 upvotes + 8 comments would score >= 70
    verify_and_score(fake, cfg, usd_krw_rate=1400.0, now=now)
    assert fake.score >= 70
    # With Naver baseline showing normal market price is only 85,000원 -> penalized below 70!
    bl_over = build_naver_baseline(99000, reference_krw=85000, lowest_krw=82000, sample_count=5, query="가짜정가 블렌더")
    verify_and_score(fake, cfg, usd_krw_rate=1400.0, now=now, kr_market_baseline=bl_over)
    assert fake.is_price_drop is False
    assert fake.score < 70
    assert any("메리트 낮음" in r for r in fake.reasons)

    # 4. Internal 60-day history baseline (3+ samples, -38% drop) crosses 70+ even WITHOUT Naver API
    hist_deal = ScoredDeal(
        canonical_key="url:hist-drop",
        primary=_kr_raw(
            title="[쿠팡] 삼성전자 990 PRO NVMe SSD 2TB 139,000원 무료배송",
            price_krw=139000,
            url="https://dasaja.co.kr/saja_korea/555004",
            now=now,
        ),
        merged_sources=["다사자 쿠팡 핫딜"],
    )
    hb = MarketBaseline(
        source="price_history",
        reference_price_krw=225000,
        lowest_price_krw=219000,
        sample_count=3,
        drop_pct=38.2,
    )
    verify_and_score(hist_deal, cfg, usd_krw_rate=1400.0, now=now, kr_history_baseline=hb)
    assert hist_deal.is_price_drop is True
    assert hist_deal.score >= 70
    assert any("최근 60일 시세(225,000원, 3건) 대비 38% 급락" in r for r in hist_deal.reasons)


def test_naver_api_client_caching_and_error_redaction(tmp_path: Path, monkeypatch, caplog):
    clear_registered_secrets()
    db_file = tmp_path / "cache.db"
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    secret_id = "FakeNaverClientId12345"
    secret_pw = "FakeNaverSecretVal98765"

    call_count = 0

    async def _mock_get(self, url, headers=None, params=None):
        nonlocal call_count
        call_count += 1
        assert headers["X-Naver-Client-Id"] == secret_id
        assert headers["X-Naver-Client-Secret"] == secret_pw
        return httpx.Response(
            200,
            json={
                "items": [
                    {"title": "소니 WH-1000XM5 헤드폰", "lprice": "389000", "productType": "1", "brand": "소니"},
                    {"title": "소니 WH-1000XM5 헤드폰 정품", "lprice": "399000", "productType": "2", "brand": "소니"},
                    {"title": "소니 WH-1000XM5 무선 헤드폰", "lprice": "409000", "productType": "2", "brand": "소니"},
                ]
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get)

    deal = ScoredDeal(
        canonical_key="url:cache-1",
        product_signature=make_product_signature("[쿠팡] 소니 WH-1000XM5 헤드폰 239,000원", "KR") or "",
        primary=_kr_raw(
            title="[쿠팡] 소니 WH-1000XM5 헤드폰 239,000원",
            price_krw=239000,
            url="https://dasaja.co.kr/saja_korea/7771",
            now=now,
        ),
    )
    naver_cfg = NaverShoppingConfig(enabled=True, client_id=secret_id, client_secret=secret_pw)

    with DealStorage(db_file) as store:
        res1 = asyncio.run(fetch_naver_market_baselines([deal], naver_cfg, storage=store, now=now))
        assert call_count == 1
        assert "url:cache-1" in res1
        assert res1["url:cache-1"].reference_price_krw == 389000

        # Second call 1 hour later for a different deal of the SAME product hits SQLite cache (0 HTTP calls!)
        deal2 = ScoredDeal(
            canonical_key="url:cache-2",
            product_signature=deal.product_signature,
            primary=_kr_raw(
                title="[네이버] 소니 WH-1000XM5 헤드폰 199,000원",
                price_krw=199000,
                url="https://dasaja.co.kr/saja_korea/7772",
                now=now + timedelta(hours=1),
            ),
        )
        res2 = asyncio.run(
            fetch_naver_market_baselines([deal2], naver_cfg, storage=store, now=now + timedelta(hours=1))
        )
        assert call_count == 1  # Still 1 — served purely from SQLite cache!
        assert res2["url:cache-2"].drop_pct > res1["url:cache-1"].drop_pct

    # Error redaction: transport error containing secret must NEVER leak into logs
    async def _boom_get(self, url, headers=None, params=None):
        raise httpx.ConnectTimeout(f"Failed with {secret_id} and {secret_pw}")

    monkeypatch.setattr(httpx.AsyncClient, "get", _boom_get)
    with caplog.at_level(logging.WARNING, logger="bf_deal_radar.market"):
        res_err = asyncio.run(fetch_naver_market_baselines([deal], naver_cfg, storage=None, now=now))
    assert res_err == {}
    assert secret_id not in caplog.text
    assert secret_pw not in caplog.text
    assert "[REDACTED]" in caplog.text
    clear_registered_secrets()


def test_pipeline_end_to_end_history_and_naver_live_drop(tmp_path: Path, monkeypatch):
    # Scenario A: WITHOUT Naver API — internal 60-day KRW history silently accumulates and catches a sudden drop
    db_path = tmp_path / "e2e_hist.db"
    title = "[쿠팡] 삼성전자 990 PRO NVMe SSD 2TB 무료배송"
    sig = make_product_signature(title, "KR") or ""

    # Pre-seed 3 prior sightings in krw_price_history (simulating previous days' radar runs)
    with DealStorage(db_path) as store:
        base_now = datetime.now(timezone.utc)
        for idx, (d_ago, pr) in enumerate([(25, 229000), (14, 225000), (4, 219000)], start=1):
            d = ScoredDeal(
                canonical_key=f"url:seed-{idx}",
                product_signature=sig,
                primary=_kr_raw(title=title, price_krw=pr, url=f"https://dasaja.co.kr/saja_korea/{idx}"),
            )
            store.record_krw_price_observation(d, now=base_now - timedelta(days=d_ago))

    drop_raw = _kr_raw(
        title=f"{title} 139,000원",
        price_krw=139000,
        url="https://dasaja.co.kr/saja_korea/9001",
    )

    async def _fake_fetch(_sources):
        return [drop_raw]

    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_all_sources", _fake_fetch)
    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_usd_krw_rate", lambda **_: 1400.0)

    cfg_hist = AppConfig(
        kr_min_score=70,
        db_path=str(db_path),
        sources=[
            SourceConfig(
                id="dasaja_coupang",
                name="다사자 쿠팡 핫딜",
                type="dasaja",
                region="KR",
                url="https://dasaja.co.kr/saja_korea",
                base_score=22,
            )
        ],
    )
    report = run_once(cfg_hist, dry_run=True)
    assert report.qualified_count == 1
    assert report.notified_count == 1
    assert report.notified_deals[0].is_price_drop is True
    assert report.notified_deals[0].market_baseline is not None
    assert report.notified_deals[0].market_baseline.source == "price_history"

    # Scenario B: WITH Naver API configured — brand-new unseen item drops 41% vs Naver live market -> immediately notified!
    unseen_raw = _kr_raw(
        title="[쿠팡] 소니 WH-1000XM5 헤드폰 229,000원 무료배송",
        price_krw=229000,
        url="https://dasaja.co.kr/saja_korea/9002",
    )

    async def _fake_fetch_b(_sources):
        return [unseen_raw]

    async def _fake_naver_get(self, url, headers=None, params=None):
        return httpx.Response(
            200,
            json={
                "items": [
                    {"title": "소니 WH-1000XM5 헤드폰", "lprice": "389000", "productType": "1", "brand": "소니"},
                    {"title": "소니 WH-1000XM5 헤드폰 블랙", "lprice": "399000", "productType": "2", "brand": "소니"},
                    {"title": "소니 WH-1000XM5 무선 헤드폰", "lprice": "409000", "productType": "2", "brand": "소니"},
                    {"title": "소니 WH-1000XM5 헤드폰 실버", "lprice": "415000", "productType": "2", "brand": "소니"},
                ]
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("bf_deal_radar.pipeline.fetch_all_sources", _fake_fetch_b)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_naver_get)

    cfg_naver = AppConfig(
        kr_min_score=70,
        db_path=str(tmp_path / "e2e_naver.db"),
        sources=cfg_hist.sources,
        naver=NaverShoppingConfig(enabled=True, client_id="test-id-1234", client_secret="test-sec-5678"),
    )
    report_b = run_once(cfg_naver, dry_run=True)
    assert report_b.qualified_count == 1
    assert report_b.notified_count == 1
    top = report_b.notified_deals[0]
    assert top.is_price_drop is True
    assert top.market_baseline is not None
    assert top.market_baseline.source == "naver_shopping"
    assert top.market_baseline.reference_price_krw == 389000
    assert top.score >= 70
    clear_registered_secrets()


def test_load_config_reads_naver_and_history_days(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "env-naver-id-1234")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "env-naver-secret-5678")
    cfg = load_config("config.yaml", tmp_path / "nonexistent.env")
    assert cfg.price_history_days == 60
    assert cfg.naver.is_configured is True
    assert cfg.naver.max_calls_per_run == 20
    assert "env-naver-id-1234" not in repr(cfg)
    assert "env-naver-secret-5678" not in repr(cfg)
    clear_registered_secrets()
