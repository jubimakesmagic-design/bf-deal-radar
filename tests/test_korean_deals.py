"""Unit tests for Korean domestic hot deal collection, parsing, scoring, Naver check, and Telegram output."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from bf_deal_radar.dedup import cluster_deals
from bf_deal_radar.models import AppConfig, FilterConfig, NaverConfig, RawDeal, ScoredDeal, SourceConfig
from bf_deal_radar.naver import enrich_kr_deals_with_naver
from bf_deal_radar.notifier.telegram import build_telegram_payload, format_telegram_html
from bf_deal_radar.parser import (
    clean_kr_product_query,
    extract_kr_discount_pct,
    extract_kr_retailer,
    extract_kr_shipping,
    extract_krw_price,
    is_kr_sold_out,
)
from bf_deal_radar.sources.korean import (
    KST,
    extract_buy_url_from_post_html,
    parse_clien_html,
    parse_dealbada_html,
    parse_ruliweb_html,
    unwrap_redirect_url,
)
from bf_deal_radar.storage import DealStorage
from bf_deal_radar.verifier import verify_and_score

RULIWEB_HTML = """
<tr class="table_body blocktarget">
  <td class="id">109421</td>
  <td class="divsn text_over"><a href="#">음식</a></td>
  <td class="subject"><div class="relative">
    <a class="subject_link deco" href="https://bbs.ruliweb.com/market/board/1020/read/109421?">
      [쿠팡와우] 오레오 웨하스 스틱 초코 510g (10,720원/무료)
      <span class="num_reply"> (14)</span>
    </a>
  </div></td>
  <td class="recomd">18</td>
  <td class="hit">5200</td>
  <td class="time">07:55</td>
</tr>
"""

CLIEN_HTML = """
<div class="list_item symph_row jirum " data-role="list-row" data-board-sn=19298969 data-comment-count=12>
  <span class="list_subject" title="[하이마트] 앤커 파워뱅크 20000mAh 87W A1118 43,610원/무배">
    <a href="/service/board/jirum/19298969" data-role="list-title-text">[하이마트] 앤커 파워뱅크 20000mAh 87W A1118 43,610원/무배</a>
  </span>
  <div class="keyword">
    <span class="list_votes"><i class="fa fa-heart"></i> 9</span>
    <a class="icon_keyword">상품정보</a>
  </div>
  <span class="hit">3852</span>
  <span class="timestamp">2026-11-27 08:01:32</span>
</div>
<div class="list_item symph_row jirum sold_out" data-role="list-row" data-board-sn=19298977 data-comment-count=2>
  <span class="list_subject" title="[쿠팡] 품절된 상품 15,000원">
    <a href="/service/board/jirum/19298977" data-role="list-title-text">[쿠팡] 품절된 상품 15,000원</a>
  </span>
  <span class="icon_info">품절</span>
  <span class="timestamp">2026-11-27 07:00:00</span>
</div>
"""

DEALBADA_HTML = """
<tr class="">
  <td class="td_num">33422</td>
  <td class="td_cate"><a class="bo_cate_link">식품/건강</a></td>
  <td class="td_subject">
    <a href="//www.dealbada.com/bbs/board.php?bo_table=deal_domestic&amp;wr_id=5999&amp;page=1">
      [네이버] 냉동 무첨가 아보카도 250g x 8팩 (7,990원/무료)
      <span class="sound_only">댓글</span><span class="cnt_cmt">8</span><span class="sound_only">개</span>
    </a>
  </td>
  <td class="td_date">09:17</td>
  <td class="td_num">2400</td>
  <td class="td_num_g"><span style="color:#ac3131">11</span> / <span style="color:#1f00b5">0</span></td>
</tr>
"""


def test_korean_price_retailer_shipping_and_query_parsers():
    assert extract_krw_price("[쿠팡와우] 오레오 스틱 (10,720원/무료)") == 10720
    assert extract_krw_price("[하이마트] 앤커 파워뱅크 43,610/무배") == 43610
    assert extract_krw_price("[네이버] 삼성 SSD 12.9만원") == 129000
    assert extract_krw_price("[네이버페이] 라방 랜덤 5원") is None
    assert extract_kr_discount_pct("스타듀밸리 50% 할인 2,950원") == 50.0
    assert extract_kr_retailer("[쿠팡와우] 오레오") == "쿠팡와우"
    assert extract_kr_retailer("[지마켓] 맥심 커피") == "G마켓"
    assert extract_kr_shipping("43,610원/무배") == "무료배송"
    assert is_kr_sold_out("[종료] 쿠팡 특가") is True
    assert clean_kr_product_query("[쿠팡와우] 오레오 웨하스 스틱 초코 510g (10,720원/무료)") == "오레오 웨하스 스틱 초코 510g"


def test_parse_ruliweb_clien_and_dealbada_html():
    now_kst = datetime(2026, 11, 27, 10, 30, tzinfo=KST)

    r_src = SourceConfig(id="ruliweb", name="루리웹 핫딜", type="ruliweb", region="KR", url="https://bbs.ruliweb.com/market/board/1020", base_score=22)
    r_deals = parse_ruliweb_html(RULIWEB_HTML, r_src, now_kst=now_kst)
    assert len(r_deals) == 1
    assert r_deals[0].price_krw == 10720
    assert r_deals[0].thumb_score == 18
    assert r_deals[0].comment_count == 14
    assert r_deals[0].retailer == "쿠팡와우"

    c_src = SourceConfig(id="clien", name="클리앙 알뜰구매", type="clien", region="KR", url="https://www.clien.net/service/board/jirum", base_score=20)
    c_deals = parse_clien_html(CLIEN_HTML, c_src, now_kst=now_kst)
    assert len(c_deals) == 2
    assert c_deals[0].is_sold_out is False
    assert c_deals[0].price_krw == 43610
    assert c_deals[0].thumb_score == 9
    assert c_deals[1].is_sold_out is True

    d_src = SourceConfig(id="dealbada", name="딜바다 국내핫딜", type="dealbada", region="KR", url="https://www.dealbada.com/bbs/board.php?bo_table=deal_domestic", base_score=18)
    d_deals = parse_dealbada_html(DEALBADA_HTML, d_src, now_kst=now_kst)
    assert len(d_deals) == 1
    assert d_deals[0].price_krw == 7990
    assert d_deals[0].thumb_score == 11
    assert d_deals[0].downvote_count == 0
    assert d_deals[0].comment_count == 8


def test_korean_scoring_cross_community_boost_and_spam_exclusion():
    now = datetime.now(timezone.utc)
    cfg = AppConfig(
        kr_min_score=62,
        min_price_krw=3000,
        filters=FilterConfig(kr_exclude_keywords=["라방", "랜덤"]),
    )

    # Cross-community Korean deal (Ruliweb + Clien)
    d1 = RawDeal(
        source_id="ruliweb",
        source_name="루리웹 핫딜",
        source_type="ruliweb",
        source_base_score=22,
        category_hint="음식",
        external_id="r1",
        title="[쿠팡] 코카콜라 제로 355ml 24캔 (14,900원/무료) 역대가",
        url="https://bbs.ruliweb.com/market/board/1020/read/1",
        region="KR",
        published_at=now,
        retailer="쿠팡",
        price_krw=14900,
        shipping_label="무료배송",
        thumb_score=16,
        comment_count=15,
        view_count=5500,
    )
    d2 = RawDeal(
        source_id="clien",
        source_name="클리앙 알뜰구매",
        source_type="clien",
        source_base_score=20,
        category_hint="상품정보",
        external_id="c1",
        title="[쿠팡와우] 코카콜라 제로 355ml 24캔 14,900원/무배",
        url="https://www.clien.net/service/board/jirum/1",
        region="KR",
        published_at=now,
        retailer="쿠팡와우",
        price_krw=14900,
        shipping_label="무료배송",
        thumb_score=8,
        comment_count=6,
    )
    clustered = cluster_deals([d1, d2])
    assert len(clustered) == 1
    scored = verify_and_score(clustered[0], cfg, usd_krw_rate=1350.0, now=now)
    assert scored.excluded_reason is None
    assert scored.score >= 85
    assert any("교차 검증" in r for r in scored.reasons)

    # Telegram output has 🇰🇷 badge and Naver/Danawa comparison buttons
    html_msg = format_telegram_html(scored)
    assert "🇰🇷 국내" in html_msg
    assert "14,900원" in html_msg
    payload = build_telegram_payload("12345", scored)
    btn_urls = [b["url"] for row in payload["reply_markup"]["inline_keyboard"] for b in row]
    assert any("search.shopping.naver.com" in u for u in btn_urls)
    assert any("search.danawa.com" in u for u in btn_urls)

    # Spam click-point deal excluded
    spam = ScoredDeal(
        canonical_key="url:spam",
        primary=RawDeal(
            source_id="ruliweb",
            source_name="루리웹 핫딜",
            source_type="ruliweb",
            source_base_score=22,
            category_hint="상품권",
            external_id="r2",
            title="[네이버페이] 라방 랜덤 5원",
            url="https://bbs.ruliweb.com/market/board/1020/read/2",
            region="KR",
            published_at=now,
        ),
        merged_sources=["루리웹 핫딜"],
    )
    verify_and_score(spam, cfg, usd_krw_rate=1350.0, now=now)
    assert spam.excluded_reason is not None


def test_naver_market_enrichment_boosts_genuine_discount_and_hides_secrets(monkeypatch):
    def _fake_get(self, url, params=None, headers=None):
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "items": [
                    {"lprice": "58000"},
                    {"lprice": "62000"},
                    {"lprice": "64000"},
                    {"lprice": "65000"},
                    {"lprice": "69000"},
                    {"lprice": "72000"},
                ]
            },
        )

    monkeypatch.setattr(httpx.Client, "get", _fake_get)
    deal = ScoredDeal(
        canonical_key="url:anker",
        primary=RawDeal(
            source_id="clien",
            source_name="클리앙 알뜰구매",
            source_type="clien",
            source_base_score=20,
            category_hint="상품정보",
            external_id="c9",
            title="[하이마트] 앤커 파워뱅크 20000mAh 87W 43,610원/무배",
            url="https://www.clien.net/service/board/jirum/9",
            region="KR",
            price_krw=43610,
        ),
        score=65,
    )
    cfg = NaverConfig(enabled=True, client_id="SecretNaverId123", client_secret="SecretNaverPass456")
    assert "SecretNaverId123" not in repr(cfg)
    assert "SecretNaverPass456" not in repr(cfg)

    enrich_kr_deals_with_naver([deal], cfg)
    assert deal.naver_market is not None
    assert deal.naver_market.savings_vs_median_pct >= 30.0
    assert deal.score == 85


def test_storage_krw_price_drop_renotify(tmp_path: Path):
    db_file = tmp_path / "kr.db"
    d1 = ScoredDeal(
        canonical_key="url:kr1",
        primary=RawDeal(
            source_id="ruliweb",
            source_name="루리웹 핫딜",
            source_type="ruliweb",
            source_base_score=22,
            category_hint="음식",
            external_id="1",
            title="[쿠팡] 코카콜라 20,000원",
            url="https://bbs.ruliweb.com/1",
            region="KR",
            price_krw=20000,
        ),
        score=70,
    )
    with DealStorage(db_file) as store:
        assert store.should_notify(d1) is True
        store.record_deal(d1, notified=True)
        assert store.should_notify(d1) is False
        # Drops 15% (20,000 -> 17,000) -> re-notify!
        d1.primary.price_krw = 17000
        assert store.should_notify(d1) is True


def test_extract_direct_buy_url_from_ruliweb_and_clien_posts():
    ruliweb_post = (
        '<div class="source_url box_line_with_shadow"><span class="text_bar">출처 : </span>'
        '<a href="https://web.ruliweb.com/link.php?ol=https%3A%2F%2Fwww.coupang.com%2Fvp%2Fproducts%2F877132&amp;bbs=1020">link</a></div>'
    )
    assert (
        extract_buy_url_from_post_html(ruliweb_post, "ruliweb")
        == "https://www.coupang.com/vp/products/877132"
    )

    clien_post = (
        '<div class="outlink"><a href="https://click.linkprice.com/click.php?m=himart&amp;tu='
        'https%3A%2F%2Fm.e-himart.co.kr%2Fapp%2Fgoods%2FgoodsDetail%3FgoodsNo%3D0071730449">go</a></div>'
    )
    assert (
        extract_buy_url_from_post_html(clien_post, "clien")
        == "https://m.e-himart.co.kr/app/goods/goodsDetail?goodsNo=0071730449"
    )

    # Unsafe javascript: inside redirector is ignored
    assert unwrap_redirect_url("https://web.ruliweb.com/link.php?ol=javascript%3Aalert(1)").startswith("https://")

    # Telegram payload includes both Direct Buy + Original Post buttons when buy_url is set
    deal = ScoredDeal(
        canonical_key="url:kr-buy",
        primary=RawDeal(
            source_id="ruliweb",
            source_name="루리웹 핫딜",
            source_type="ruliweb",
            source_base_score=22,
            category_hint="음식",
            external_id="1",
            title="[쿠팡] 오레오 스틱 (10,720원/무료)",
            url="https://bbs.ruliweb.com/market/board/1020/read/109421",
            buy_url="https://www.coupang.com/vp/products/877132",
            region="KR",
            price_krw=10720,
        ),
        score=75,
    )
    payload = build_telegram_payload("12345", deal)
    row1 = payload["reply_markup"]["inline_keyboard"][0]
    assert row1[0]["text"] == "🛒 바로 구매하기"
    assert row1[0]["url"] == "https://www.coupang.com/vp/products/877132"
    assert row1[1]["text"] == "💬 원글/댓글"
    assert row1[1]["url"] == "https://bbs.ruliweb.com/market/board/1020/read/109421"
