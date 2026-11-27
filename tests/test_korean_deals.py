"""Unit tests for Korean domestic hot deal collection, parsing, scoring, Naver check, and Telegram output."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bf_deal_radar.dedup import cluster_deals
from bf_deal_radar.models import AppConfig, FilterConfig, RawDeal, ScoredDeal, SourceConfig
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
    assert 60 <= scored.score < 85
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


def test_parse_hotgusa_dasaja_eomisae_and_cross_community_dedup():
    from bf_deal_radar.sources.korean import (
        _pick_buy_url_lookup_target,
        parse_dasaja_html,
        parse_eomisae_html,
        parse_hotgusa_html,
    )
    from bf_deal_radar.verifier import classify_korean_category

    now_kst = datetime(2026, 11, 27, 11, 30, tzinfo=KST)

    hotgusa_html = (
        '<script>kit.start(app, el, {data:[null,{type:"data",data:{products:['
        '{community_id:7,id:"7_106001",title:"이클립스 페퍼민트향 캔디 34g 10개",price:12600,'
        'delivery_price:0,currency_code:0,reg_date:"2026-11-27T11:10:00+09:00",is_end:false,'
        'view_cnt:1200,cmt_cnt:8,recm_cnt:5,mall_name:"쿠팡",'
        'lowestLabel:{type:"lowest",months:12,avgDiffPct:28,compareCnt:18}},'
        '{community_id:1,id:"1_109421",title:"[쿠팡] 품절 테스트",price:9900,'
        'delivery_price:0,currency_code:0,reg_date:"2026-11-27T10:00:00+09:00",is_end:true,'
        'view_cnt:100,cmt_cnt:0,recm_cnt:0,mall_name:"지마켓"}'
        ']}}]});</script>'
    )
    hg_src = SourceConfig(id="hotgusa_lowest", name="핫구사 12개월 최저가", type="hotgusa", region="KR", url="https://www.hotgusa.com/lowest", base_score=18)
    hg_deals = parse_hotgusa_html(hotgusa_html, hg_src, now_kst=now_kst)
    assert len(hg_deals) == 2
    assert hg_deals[0].source_name == "핫구사·에펨코리아"
    assert hg_deals[0].retailer == "쿠팡"
    assert hg_deals[0].price_krw == 12600
    assert hg_deals[0].discount_pct == 28.0
    assert "12개월 평균가 대비 28%" in hg_deals[0].description
    assert hg_deals[1].source_name == "핫구사·루리웹"
    assert hg_deals[1].retailer == "G마켓"
    assert hg_deals[1].is_sold_out is True

    dasaja_html = """
    <tr class="its_write ">
      <td><img src="/images/dasaja/icon/svg/fire_icon.svg" alt=""></td>
      <td><div class="double_line_wrapper wr_1">
        <div class="cate">사탕</div>
        <div class="mall"><a href="/bbs/board.php?bo_table=saja_korea&wr_1=쿠팡"></a></div>
      </div></td>
      <td><div class="subject_wrapper">
        <a href="/saja_korea/610127">[쿠팡] 이클립스 페퍼민트향 캔디 34g 10개</a>
      </div></td>
      <td><div class="double_line_wrapper price">
        <div class="min-price"><span>12,600</span><span>원</span></div>
        <div>무료배송</div>
      </div></td>
      <td>4/0</td>
      <td>1.5k</td>
      <td><div class="double_line_wrapper write"><div>11:11</div></div></td>
    </tr>
    """
    ds_src = SourceConfig(id="dasaja_coupang", name="다사자 쿠팡 핫딜", type="dasaja", region="KR", url="https://dasaja.co.kr/saja_korea", base_score=22)
    ds_deals = parse_dasaja_html(dasaja_html, ds_src, now_kst=now_kst)
    assert len(ds_deals) == 1
    assert ds_deals[0].retailer == "쿠팡"
    assert ds_deals[0].price_krw == 12600
    assert ds_deals[0].shipping_label == "무료배송"
    assert ds_deals[0].staff_pick is True
    assert ds_deals[0].thumb_score == 4
    assert ds_deals[0].view_count == 1500

    # Cross-community clustering merges Dasaja Coupang + Hotgusa FMKorea
    clustered = cluster_deals([ds_deals[0], hg_deals[0]])
    assert len(clustered) == 1
    cfg = AppConfig(kr_min_score=62, min_price_krw=3000)
    scored = verify_and_score(clustered[0], cfg, usd_krw_rate=1350.0, now=now_kst.astimezone(timezone.utc))
    assert any("교차 검증" in r and "다사자" in r and "에펨코리아" in r for r in scored.reasons)
    assert any("커뮤니티 인기(불꽃)" in r for r in scored.reasons)
    assert 70 <= scored.score < 85

    # Ensure Hotgusa·루리웹 + 루리웹 핫딜 do NOT double-count as 2 distinct communities
    same_comm = ScoredDeal(
        canonical_key="url:ruli-same",
        primary=RawDeal(
            source_id="ruliweb",
            source_name="루리웹 핫딜",
            source_type="ruliweb",
            source_base_score=22,
            category_hint="음식",
            external_id="r1",
            title="[쿠팡] 테스트 과자 10,000원",
            url="https://bbs.ruliweb.com/market/board/1020/read/1",
            region="KR",
            published_at=now_kst.astimezone(timezone.utc),
            price_krw=10000,
        ),
        merged_sources=["루리웹 핫딜", "핫구사·루리웹"],
    )
    verify_and_score(same_comm, cfg, usd_krw_rate=1350.0, now=now_kst.astimezone(timezone.utc))
    assert not any("교차 검증" in r for r in same_comm.reasons)

    # Eomisae parser: popular row + card dedup + shorthand prices + locked-post skip + direct buy URL
    eomisae_html = """
    <tr class="is_popular">
      <td class="title">
        <span class="cate">패션국내</span>
        <a class="pjax" href="/fs/213251940">아크테릭스 버텍스 알파인 GTX 비브람 20.9 (무배)</a>
        <a href="/fs/213251940#C_" class="pjax tt_cm">14</a>
      </td>
      <td>26.11.27</td>
      <td><i class="ion-ios-heart"></i> 37</td>
    </tr>
    <div class="card_el n_ntc clear">
      <span class="cate">패션해외,</span><span>26.11.27</span>
      <h3><a class="pjax" href="/fs/213251999">15분 뒤 전체 공개로 전환 (미달 조건 : 레벨)</a></h3>
    </div>
    <div class="card_el n_ntc clear">
      <span class="cate">패션해외,</span><span>26.11.27</span>
      <h3><a class="pjax" href="/fs/213252001">일마존 F/CE 950 백팩 14.8발</a></h3>
      <span class="fr"><i class="ion-ios-eye"></i>2,100</span>
      <span class="fr"><i class="ion-ios-chatbubble"></i>21</span>
      <span class="fr"><i class="ion-ios-heart"></i>26</span>
    </div>
    <div class="card_el n_ntc clear">
      <span class="cate">패션국내,</span><span>26.11.27</span>
      <h3><a class="pjax" href="/fs/213252002">아디다스 토바코, 컨트리 OG ₩25,990~</a></h3>
      <span class="fr"><i class="ion-ios-heart"></i>44</span>
    </div>
    """
    eo_src = SourceConfig(id="eomisae_fs", name="어미새 인기정보", type="eomisae", region="KR", url="https://eomisae.co.kr/fs", base_score=18)
    eo_deals = parse_eomisae_html(eomisae_html, eo_src, now_kst=now_kst)
    assert len(eo_deals) == 3  # locked post skipped!
    assert eo_deals[0].price_krw == 209000
    assert eo_deals[0].staff_pick is True
    assert eo_deals[1].price_krw == 148000
    assert eo_deals[1].view_count == 2100
    assert eo_deals[2].price_krw == 25990
    assert eo_deals[2].retailer == "아디다스"

    eomisae_post = '<div class="rd_body clear"><article><a href="https://www.zappos.com/p/arcteryx/9929251">buy</a></article></div>'
    assert extract_buy_url_from_post_html(eomisae_post, "eomisae") == "https://www.zappos.com/p/arcteryx/9929251"

    # Cross-source buy URL lookup falls back to merged Ruliweb URL when primary is Dasaja
    merged_dasaja = ScoredDeal(
        canonical_key="url:ds-ruli",
        primary=ds_deals[0],
        merged_sources=["다사자 쿠팡 핫딜", "루리웹 핫딜"],
        merged_urls={
            "다사자 쿠팡 핫딜": "https://dasaja.co.kr/saja_korea/610127",
            "루리웹 핫딜": "https://bbs.ruliweb.com/market/board/1020/read/109421",
        },
    )
    assert _pick_buy_url_lookup_target(merged_dasaja) == (
        "https://bbs.ruliweb.com/market/board/1020/read/109421",
        "ruliweb",
    )

    # Expanded category classification checks
    assert classify_korean_category("조선호텔 포기김치 8kg", "포기김치") == "🍔 식품/간식"
    assert classify_korean_category("얼라이브 멀티 비타 구미", "멀티비타민") == "🧴 뷰티/건강"
    assert classify_korean_category("타원볼 4P+미니볼 세트", "도자기") == "🏠 생활/주방"
    assert classify_korean_category("아크테릭스 버텍스 알파인", "패션국내") == "👕 패션/잡화"


def test_strict_scoring_prevents_inflation_and_blocks_fake_pct():
    now = datetime.now(timezone.utc)
    cfg = AppConfig(min_score=70, kr_min_score=70, min_price_krw=3000)

    # Ingredient percentage ("연육80%") must NEVER be misread as 80% discount
    assert extract_kr_discount_pct("[옥션] 연육80% 부산 봉어묵 2kg") is None
    assert extract_kr_discount_pct("[쿠팡] 순면 100% 호텔 수건 10장") is None

    # Minor 10% off bottled water even on 2 communities + fire badge must stay well below 70
    minor_water = ScoredDeal(
        canonical_key="url:water",
        primary=RawDeal(
            source_id="dasaja_coupang",
            source_name="다사자 쿠팡 핫딜",
            source_type="dasaja",
            source_base_score=22,
            category_hint="국내핫딜",
            external_id="w1",
            title="[쿠팡] 아쿠아시스 무라벨 생수 2L 24개",
            url="https://dasaja.co.kr/saja_korea/1",
            region="KR",
            description="최근 6개월 평균가 대비 10% 저렴 (역대 최저가, 비교 8건)",
            published_at=now,
            retailer="쿠팡",
            price_krw=9980,
            shipping_label="무료배송",
            discount_pct=10.0,
            thumb_score=4,
            staff_pick=True,
        ),
        merged_sources=["다사자 쿠팡 핫딜", "핫구사·에펨코리아"],
    )
    verify_and_score(minor_water, cfg, usd_krw_rate=1350.0, now=now)
    assert minor_water.score < 55

    # Inflated store-banner multi-item range promo ("모음전 8,900원~ (74%)") stays below 70
    banner = ScoredDeal(
        canonical_key="url:banner",
        primary=RawDeal(
            source_id="dasaja_best",
            source_name="다사자 BEST 핫딜",
            source_type="dasaja",
            source_base_score=20,
            category_hint="생활용품",
            external_id="b1",
            title="[오늘의집] 빠른배송! 우드레이 BEST상품 모음전 8,900원~ (74%)",
            url="https://dasaja.co.kr/saja_korea/2",
            region="KR",
            published_at=now,
            retailer="오늘의집",
            price_krw=8900,
            shipping_label="무료배송",
            discount_pct=74.0,
            thumb_score=3,
            staff_pick=True,
        ),
        merged_sources=["다사자 BEST 핫딜"],
    )
    verify_and_score(banner, cfg, usd_krw_rate=1350.0, now=now)
    assert banner.score < 55

    # Single-community 50% viral deal reaches 80-89 (rarely 90+ unless cross-verified!)
    viral_single = ScoredDeal(
        canonical_key="url:br1",
        primary=RawDeal(
            source_id="hotgusa_lowest",
            source_name="핫구사·아카라이브",
            source_type="hotgusa",
            source_base_score=18,
            category_hint="국내핫딜",
            external_id="br1",
            title="배스킨라빈스 쿼터 50%할인",
            url="https://www.hotgusa.com/product/8_190374645",
            region="KR",
            description="최근 12개월 평균가 대비 35% 저렴 (역대 최저가, 비교 16건)",
            published_at=now,
            retailer="토스쇼핑",
            price_krw=9900,
            shipping_label="무료배송",
            discount_pct=50.0,
            thumb_score=23,
            comment_count=57,
            view_count=8158,
        ),
        merged_sources=["핫구사·아카라이브"],
    )
    verify_and_score(viral_single, cfg, usd_krw_rate=1350.0, now=now)
    assert 80 <= viral_single.score < 90

    # Same 50% viral mega-deal confirmed on 2 communities crosses 90+!
    viral_cross = ScoredDeal(
        canonical_key="url:br2",
        primary=viral_single.primary,
        merged_sources=["핫구사·아카라이브", "핫구사·에펨코리아"],
    )
    verify_and_score(viral_cross, cfg, usd_krw_rate=1350.0, now=now)
    assert viral_cross.score >= 90


def test_parse_dogdrip_and_dasaja_iherb_html():
    from bf_deal_radar.sources.korean import parse_dasaja_iherb_html, parse_dogdrip_html

    now_kst = datetime(2026, 11, 27, 14, 0, tzinfo=KST)

    dogdrip_html = """
    <li class="ed flex popular-item">
      <div class="ed flex padding-left-xsmall">
        <div class="ed list-header">
          <span class="ed badge badge-label">인기</span>
          <h5 class="ed title">
            <a href="/hotdeal/736864485?sort_index=popular&amp;page=1" class="ed title-link" data-document-srl="736864485">
              [무신사] 메종미네드 패딩 50%할인 124,500원 (무료)
            </a>
            <span class="ed text-primary text-xxsmall">18</span>
          </h5>
        </div>
        <div class="ed list-meta">
          <span class="ed text-muted">[의류]</span>
          <span class="ed text-primary"><i class="far fa-thumbs-up"></i></span>
          <span class="ed text-primary">24</span>
          <span class="ed text-muted"><i class="far fa-clock"></i> 2 시간 전</span>
        </div>
      </div>
    </li>
    <li class="ed flex">
      <div class="ed flex padding-left-xsmall">
        <div class="ed list-header">
          <h5 class="ed title">
            <a href="https://www.dogdrip.net/736853567" class="ed title-link" data-document-srl="736853567">
              [네이버] 제스프리 썬골드키위 2.3kg 33,900원 (무료)
            </a>
            <span class="ed text-primary text-xxsmall">4</span>
          </h5>
        </div>
        <div class="ed list-meta">
          <span class="ed text-muted">[식품]</span>
          <span class="ed text-primary"><i class="far fa-thumbs-up"></i></span>
          <span class="ed text-primary">6</span>
          <span class="ed text-muted"><i class="far fa-clock"></i> 1 일 전</span>
        </div>
      </div>
    </li>
    <li class="ed flex">
      <div class="ed flex padding-left-xsmall">
        <div class="ed list-header">
          <h5 class="ed title">
            <a href="https://www.dogdrip.net/736864485" class="ed title-link" data-document-srl="736864485">
              [무신사] 메종미네드 패딩 50%할인 124,500원 (무료)
            </a>
          </h5>
        </div>
      </div>
    </li>
    """
    dd_src = SourceConfig(
        id="dogdrip_hotdeal",
        name="개드립 핫딜",
        type="dogdrip",
        region="KR",
        url="https://www.dogdrip.net/hotdeal",
        base_score=18,
    )
    dd_deals = parse_dogdrip_html(dogdrip_html, dd_src, now_kst=now_kst)
    assert len(dd_deals) == 2  # duplicate doc_id deduplicated while preserving 인기 badge
    assert dd_deals[0].url == "https://www.dogdrip.net/hotdeal/736864485"
    assert dd_deals[0].staff_pick is True
    assert dd_deals[0].retailer == "무신사"
    assert dd_deals[0].price_krw == 124500
    assert dd_deals[0].shipping_label == "무료배송"
    assert dd_deals[0].discount_pct == 50.0
    assert dd_deals[0].thumb_score == 24
    assert dd_deals[0].comment_count == 18
    assert dd_deals[0].category == "의류"
    assert dd_deals[0].published_at == datetime(2026, 11, 27, 12, 0, tzinfo=KST).astimezone(timezone.utc)

    assert dd_deals[1].url == "https://www.dogdrip.net/hotdeal/736853567"
    assert dd_deals[1].staff_pick is False
    assert dd_deals[1].retailer == "네이버"
    assert dd_deals[1].price_krw == 33900
    assert dd_deals[1].comment_count == 4
    assert dd_deals[1].published_at == datetime(2026, 11, 26, 14, 0, tzinfo=KST).astimezone(timezone.utc)

    # Dasaja iHerb day-over-day price-drop gallery parser
    iherb_html = """
    <div class="photo_content_box">
      <a href="/saja_iherb/91001" class="photo_title">나우푸드 오메가-3 200정</a>
      <div class="ih_rate"><b>39%</b><span class="ih_vs">내림</span></div>
      <del>30,019원</del>
      <span class="price_display">18,251</span>
      <div class="ih_date">11.27</div>
    </div>
    <div class="photo_content_box">
      <a href="/saja_iherb/91002" class="photo_title">가격 상승 상품</a>
      <div class="ih_rate"><b>25%</b><span class="ih_vs">오름</span></div>
      <del>10,000원</del>
      <span class="price_display">12,500</span>
      <div class="ih_date">11.27</div>
    </div>
    <div class="photo_content_box">
      <a href="/saja_iherb/91003" class="photo_title">소폭 하락 상품</a>
      <div class="ih_rate"><b>10%</b><span class="ih_vs">내림</span></div>
      <del>10,000원</del>
      <span class="price_display">9,000</span>
      <div class="ih_date">11.27</div>
    </div>
    """
    ih_src = SourceConfig(
        id="dasaja_iherb",
        name="다사자 아이허브 특가",
        type="dasaja_iherb",
        region="KR",
        url="https://dasaja.co.kr/saja_iherb",
        base_score=18,
        category_hint="아이허브",
    )
    ih_deals = parse_dasaja_iherb_html(iherb_html, ih_src, now_kst=now_kst)
    assert len(ih_deals) == 1  # skips 오름 and <15% drop
    assert ih_deals[0].title == "[아이허브] 나우푸드 오메가-3 200정"
    assert ih_deals[0].retailer == "아이허브"
    assert ih_deals[0].price_krw == 18251
    assert ih_deals[0].discount_pct == 39.0
    assert "전날(30,019원) 대비 39% 가격 하락" in ih_deals[0].description

    # Verify iHerb day-over-day drop gets statistical historical-low recognition in verifier
    cfg = AppConfig(min_score=70, kr_min_score=70, min_price_krw=3000)
    ih_scored = ScoredDeal(
        canonical_key="url:iherb1",
        primary=ih_deals[0],
        merged_sources=["다사자 아이허브 특가"],
    )
    verify_and_score(ih_scored, cfg, usd_krw_rate=1350.0, now=now_kst.astimezone(timezone.utc))
    assert ih_scored.is_historical_low is True
    assert ih_scored.category_label_ko == "🧴 뷰티/건강"
    assert any("전날(30,019원) 대비 39% 가격 하락" in r for r in ih_scored.reasons)

