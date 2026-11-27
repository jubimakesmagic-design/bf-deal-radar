"""Parsers for open Korean domestic hot deal communities (Ruliweb, Clien, Dealbada, Hotgusa, Dasaja, Eomisae)."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from bf_deal_radar.models import RawDeal, ScoredDeal, SourceConfig
from bf_deal_radar.parser import (
    extract_asin,
    extract_kr_discount_pct,
    extract_kr_retailer,
    extract_kr_shipping,
    extract_krw_price,
    is_kr_sold_out,
    normalize_kr_retailer,
    strip_html,
)
from bf_deal_radar.security import is_safe_http_url

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def _parse_int(val: str | None) -> int | None:
    if not val:
        return None
    cleaned = val.strip().lower().replace(",", "")
    if cleaned.endswith("k"):
        try:
            return int(float(cleaned[:-1].strip()) * 1000)
        except ValueError:
            return None
    m = re.search(r"\d+", cleaned)
    return int(m.group(0)) if m else None


def _parse_kst_time(raw: str, now_kst: datetime | None = None) -> datetime | None:
    """Parse KST/ISO timestamps like '10:23', '11-26', '26.11.27', '26/11/27', or ISO-8601."""
    if not raw:
        return None
    s = raw.strip()
    now_kst = now_kst or datetime.now(KST)

    # ISO 8601 (Hotgusa: 2026-11-26T20:09:16+09:00)
    if "T" in s and ("+" in s or s.endswith("Z")):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    # Full YYYY-MM-DD HH:MM:SS (Clien)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=KST).astimezone(timezone.utc)
        except ValueError:
            pass

    # HH:MM (today in KST, roll back 1 day if slightly in the future)
    m_hm = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m_hm:
        dt = now_kst.replace(hour=int(m_hm.group(1)), minute=int(m_hm.group(2)), second=0, microsecond=0)
        if dt > now_kst + timedelta(minutes=10):
            dt -= timedelta(days=1)
        return dt.astimezone(timezone.utc)

    # YYYY.MM.DD (Ruliweb older posts)
    m_ymd = re.fullmatch(r"(\d{4})[./-](\d{2})[./-](\d{2})", s)
    if m_ymd:
        try:
            dt = datetime(int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3)), 12, 0, tzinfo=KST)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    # YY.MM.DD or YY/MM/DD (Eomisae, Dasaja)
    m_y2 = re.fullmatch(r"(\d{2})[./-](\d{2})[./-](\d{2})", s)
    if m_y2:
        try:
            dt = datetime(2000 + int(m_y2.group(1)), int(m_y2.group(2)), int(m_y2.group(3)), 12, 0, tzinfo=KST)
            # If dated today in KST, use current KST time instead of noon so age checks stay accurate
            if dt.date() == now_kst.date():
                return now_kst.astimezone(timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    # MM-DD (Dealbada older posts)
    m_md = re.fullmatch(r"(\d{2})-(\d{2})", s)
    if m_md:
        year = now_kst.year
        month, day = int(m_md.group(1)), int(m_md.group(2))
        try:
            dt = datetime(year, month, day, 12, 0, tzinfo=KST)
            if dt > now_kst + timedelta(days=2):
                dt = datetime(year - 1, month, day, 12, 0, tzinfo=KST)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    return None


def parse_ruliweb_html(html_text: str, source: SourceConfig, now_kst: datetime | None = None) -> list[RawDeal]:
    """Parse Ruliweb Hot Deal board (bbs.ruliweb.com/market/board/1020)."""
    deals: list[RawDeal] = []
    rows = re.findall(r'<tr[^>]*class="table_body blocktarget"[^>]*>(.*?)</tr>', html_text, re.S)

    for row in rows:
        id_m = re.search(r'<td class="id">\s*(\d+)\s*</td>', row)
        link_m = re.search(
            r'<a[^>]*class="[^"]*subject_link[^"]*"[^>]*href="(https://bbs\.ruliweb\.com/market/board/1020/read/\d+[^"]*)"[^>]*>(.*?)</a>',
            row,
            re.S,
        )
        if not link_m:
            continue

        post_url = html.unescape(link_m.group(1)).rstrip("?")
        raw_link_inner = link_m.group(2)

        # Extract reply count before stripping it out of title text
        reply_m = re.search(r'class="num_reply"[^>]*>.*?\((\d+)\).*?</span>', raw_link_inner, re.S)
        comment_count = int(reply_m.group(1)) if reply_m else 0

        title_no_reply = re.sub(r'<span[^>]*class="num_reply"[^>]*>.*?</span>', "", raw_link_inner, flags=re.S)
        title = strip_html(title_no_reply)
        if not title:
            continue

        cat_m = re.search(r'<td class="divsn[^>]*>(.*?)</td>', row, re.S)
        category = strip_html(cat_m.group(1)) if cat_m else None

        recom_m = re.search(r'<td class="recomd"[^>]*>\s*(\d*)\s*</td>', row)
        upvotes = _parse_int(recom_m.group(1)) if recom_m else 0

        hit_m = re.search(r'<td class="hit"[^>]*>\s*([\d,]+)\s*</td>', row)
        views = _parse_int(hit_m.group(1)) if hit_m else 0

        time_m = re.search(r'<td class="time"[^>]*>\s*([^<]+)\s*</td>', row)
        published_at = _parse_kst_time(time_m.group(1), now_kst=now_kst) if time_m else None

        sold_out = is_kr_sold_out(title) or "line-through" in row.lower()

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=category or source.category_hint,
                external_id=f"ruliweb-{id_m.group(1)}" if id_m else post_url,
                title=title,
                url=post_url,
                region="KR",
                published_at=published_at,
                retailer=extract_kr_retailer(title),
                price_krw=extract_krw_price(title),
                shipping_label=extract_kr_shipping(title),
                discount_pct=extract_kr_discount_pct(title),
                thumb_score=upvotes or 0,
                comment_count=comment_count,
                view_count=views or 0,
                is_sold_out=sold_out,
                category=category,
            )
        )
    return deals


def parse_clien_html(html_text: str, source: SourceConfig, now_kst: datetime | None = None) -> list[RawDeal]:
    """Parse Clien Jirum board (www.clien.net/service/board/jirum)."""
    deals: list[RawDeal] = []
    chunks = html_text.split('<div class="list_item symph_row jirum')[1:]

    for chunk in chunks:
        sn_m = re.search(r"data-board-sn=(\d+)", chunk)
        if not sn_m:
            # Skip pinned announcement / sponsor banners without data-board-sn
            continue

        sn = sn_m.group(1)
        head = chunk[:160]
        sold_out = "sold_out" in head or ">품절<" in chunk

        title_m = re.search(r'class="list_subject"[^>]*title="([^"]+)"', chunk)
        if not title_m:
            title_m = re.search(r'data-role="list-title-text"[^>]*>(.*?)</a>', chunk, re.S)
        if not title_m:
            continue

        title = strip_html(title_m.group(1))
        if not title:
            continue
        sold_out = sold_out or is_kr_sold_out(title)

        url = f"https://www.clien.net/service/board/jirum/{sn}"

        cmt_m = re.search(r"data-comment-count=(\d+)", chunk)
        comment_count = int(cmt_m.group(1)) if cmt_m else 0

        like_m = re.search(r'class="list_votes"[^>]*>.*?</i>\s*(\d+)', chunk, re.S)
        likes = int(like_m.group(1)) if like_m else 0

        hit_m = re.search(r'class="hit"[^>]*>([^<]+)<', chunk)
        views = _parse_int(hit_m.group(1)) if hit_m else 0

        ts_m = re.search(r'class="timestamp"[^>]*>([^<]+)<', chunk)
        published_at = _parse_kst_time(ts_m.group(1), now_kst=now_kst) if ts_m else None

        kw_m = re.search(r'class="icon_keyword"[^>]*>([^<]+)<', chunk)
        category = kw_m.group(1).strip() if kw_m else None

        prev_m = re.search(r'class="list_preview"[^>]*>\s*<pre>(.*?)</pre>', chunk, re.S)
        preview = strip_html(prev_m.group(1))[:300] if prev_m else ""

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=category or source.category_hint,
                external_id=f"clien-{sn}",
                title=title,
                url=url,
                region="KR",
                description=preview,
                published_at=published_at,
                retailer=extract_kr_retailer(title),
                price_krw=extract_krw_price(title, preview),
                shipping_label=extract_kr_shipping(title),
                discount_pct=extract_kr_discount_pct(title, preview),
                thumb_score=likes,
                comment_count=comment_count,
                view_count=views or 0,
                is_sold_out=sold_out,
                category=category,
            )
        )
    return deals


def parse_dealbada_html(html_text: str, source: SourceConfig, now_kst: datetime | None = None) -> list[RawDeal]:
    """Parse Dealbada domestic hot deal board (www.dealbada.com/bbs/board.php?bo_table=deal_domestic)."""
    deals: list[RawDeal] = []
    rows = re.findall(r'<tr class="\s*">(.*?)</tr>', html_text, re.S)

    for row in rows:
        cat_m = re.search(r'class="bo_cate_link"[^>]*>([^<]+)<', row)
        category = html.unescape(cat_m.group(1)).strip() if cat_m else None
        if category == "공지":
            continue

        subj_m = re.search(
            r'<td class="td_subject"[^>]*>.*?<a href="([^"]*wr_id=(\d+)[^"]*)"[^>]*>(.*?)</a>',
            row,
            re.S,
        )
        if not subj_m:
            continue

        raw_href = html.unescape(subj_m.group(1)).strip()
        wr_id = subj_m.group(2)
        if raw_href.startswith("//"):
            post_url = f"https:{raw_href}"
        elif raw_href.startswith("/"):
            post_url = f"https://www.dealbada.com{raw_href}"
        else:
            post_url = raw_href

        # Clean up &page=1 query noise
        post_url = re.sub(r"&page=\d+", "", post_url)

        cmt_m = re.search(r'<span class="cnt_cmt">(\d+)</span>', subj_m.group(3))
        comment_count = int(cmt_m.group(1)) if cmt_m else 0

        raw_title = re.sub(
            r'<span class="sound_only">.*?</span>|<span class="cnt_cmt">.*?</span>',
            "",
            subj_m.group(3),
            flags=re.S,
        )
        title = strip_html(raw_title)
        if not title:
            continue

        dt_m = re.search(r'<td class="td_date"[^>]*>([^<]+)<', row)
        published_at = _parse_kst_time(dt_m.group(1), now_kst=now_kst) if dt_m else None

        # Views live in the second <td class="td_num"> (first is post number)
        num_matches = re.findall(r'<td class="td_num"[^>]*>\s*([\d,]+)\s*</td>', row)
        views = _parse_int(num_matches[-1]) if len(num_matches) >= 2 else 0

        votes_m = re.search(
            r'<td class="td_num_g"[^>]*>.*?<span[^>]*>(\d+)</span>\s*/\s*<span[^>]*>(\d+)</span>',
            row,
            re.S,
        )
        upvotes = int(votes_m.group(1)) if votes_m else 0
        downvotes = int(votes_m.group(2)) if votes_m else 0

        sold_out = is_kr_sold_out(title) or "line-through" in row.lower()

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=category or source.category_hint,
                external_id=f"{source.id}-{wr_id}",
                title=title,
                url=post_url,
                region="KR",
                published_at=published_at,
                retailer=extract_kr_retailer(title),
                price_krw=extract_krw_price(title),
                shipping_label=extract_kr_shipping(title),
                discount_pct=extract_kr_discount_pct(title),
                thumb_score=upvotes,
                downvote_count=downvotes,
                comment_count=comment_count,
                view_count=views or 0,
                is_sold_out=sold_out,
                category=category,
            )
        )
    return deals


_HOTGUSA_COMMUNITIES: dict[int, str] = {
    0: "뽐뿌",
    1: "루리웹",
    2: "쿨엔조이",
    3: "퀘이사존",
    4: "어미새",
    6: "ZOD",
    7: "에펨코리아",
    8: "아카라이브",
}


def _decode_js_str(raw: str) -> str:
    """Safely decode a JS double-quoted string body without eval."""
    try:
        return json.loads('"' + raw.replace("\\'", "'") + '"')
    except Exception:
        return html.unescape(raw)


def _extract_js_objects_from_array(text: str, marker: str) -> list[str]:
    """Extract top-level `{...}` object strings from a JS array starting at `marker` (e.g. 'products:[')."""
    start = text.find(marker)
    if start == -1:
        return []
    pos = start + len(marker)
    n = len(text)
    objs: list[str] = []
    depth = 0
    in_str: str | None = None
    escaped = False
    obj_start = -1

    while pos < n:
        ch = text[pos]
        if in_str is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "{":
                if depth == 0:
                    obj_start = pos
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start != -1:
                    objs.append(text[obj_start : pos + 1])
                    obj_start = -1
            elif ch == "]" and depth == 0:
                break
        pos += 1
    return objs


def _js_str_field(obj_str: str, key: str) -> str:
    m = re.search(rf'(?<![a-zA-Z0-9_]){re.escape(key)}:"((?:\\.|[^"\\])*)"', obj_str)
    return _decode_js_str(m.group(1)).strip() if m else ""


def _js_int_field(obj_str: str, key: str) -> int | None:
    m = re.search(rf'(?<![a-zA-Z0-9_]){re.escape(key)}:(-?\d+)', obj_str)
    return int(m.group(1)) if m else None


def _js_bool_field(obj_str: str, key: str) -> bool:
    return bool(re.search(rf'(?<![a-zA-Z0-9_]){re.escape(key)}:true\b', obj_str))


def parse_hotgusa_html(html_text: str, source: SourceConfig, now_kst: datetime | None = None) -> list[RawDeal]:
    """Parse Hotgusa SSR HTML (www.hotgusa.com / www.hotgusa.com/lowest) aggregating 8 Korean communities."""
    objs = _extract_js_objects_from_array(html_text, "products:[")
    if not objs:
        return []

    deals: list[RawDeal] = []
    seen_ids: set[str] = set()
    for obj in objs:
        pid = _js_str_field(obj, "id")
        title = _js_str_field(obj, "title")
        if not pid or not title or pid in seen_ids:
            continue
        seen_ids.add(pid)

        cid = _js_int_field(obj, "community_id")
        comm_name = _HOTGUSA_COMMUNITIES.get(cid) if cid is not None else None
        source_name = f"핫구사·{comm_name}" if comm_name else source.name

        price_val = _js_int_field(obj, "price")
        currency_code = _js_int_field(obj, "currency_code") or 0
        price_krw = price_val if (currency_code == 0 and price_val and 500 <= price_val <= 50_000_000) else None
        if price_krw is None and currency_code == 0:
            price_krw = extract_krw_price(title)

        delivery_price = _js_int_field(obj, "delivery_price")
        if delivery_price == 0:
            shipping_label: str | None = "무료배송"
        elif delivery_price is not None and delivery_price > 0:
            shipping_label = f"배송비 {delivery_price:,}원"
        else:
            shipping_label = extract_kr_shipping(title)

        mall_raw = _js_str_field(obj, "mall_name")
        retailer = normalize_kr_retailer(mall_raw) or extract_kr_retailer(title)

        is_end = _js_bool_field(obj, "is_end")
        sold_out = is_end or is_kr_sold_out(title)

        upvotes = _js_int_field(obj, "recm_cnt") or 0
        comments = _js_int_field(obj, "cmt_cnt") or 0
        views = _js_int_field(obj, "view_cnt") or 0
        reg_date = _js_str_field(obj, "reg_date")
        published_at = _parse_kst_time(reg_date, now_kst=now_kst) if reg_date else None

        discount_pct = extract_kr_discount_pct(title)
        description = ""
        low_m = re.search(r"lowestLabel:\{([^}]+)\}", obj)
        if low_m:
            low_body = low_m.group(1)
            avg_diff = _js_int_field(low_body, "avgDiffPct")
            months = _js_int_field(low_body, "months") or 12
            cmp_cnt = _js_int_field(low_body, "compareCnt") or 0
            if avg_diff is not None and avg_diff >= 10 and cmp_cnt >= 2:
                discount_pct = max(discount_pct or 0.0, float(avg_diff))
                description = f"최근 {months}개월 평균가 대비 {avg_diff}% 저렴 (역대 최저가, 비교 {cmp_cnt}건)"

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source_name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=source.category_hint,
                external_id=f"hotgusa-{pid}",
                title=title,
                url=f"https://www.hotgusa.com/product/{pid}",
                region="KR",
                description=description,
                published_at=published_at,
                retailer=retailer,
                price_krw=price_krw,
                shipping_label=shipping_label,
                discount_pct=discount_pct,
                thumb_score=upvotes,
                comment_count=comments,
                view_count=views,
                is_sold_out=sold_out,
                category=comm_name,
            )
        )
    return deals


def parse_dasaja_html(html_text: str, source: SourceConfig, now_kst: datetime | None = None) -> list[RawDeal]:
    """Parse Dasaja hot deal boards (dasaja.co.kr/saja_korea, saja_hotdeal, saja_ali, Coupang filter)."""
    deals: list[RawDeal] = []
    rows = re.findall(r'<tr[^>]*class="[^"]*its_write[^"]*"[^>]*>(.*?)</tr>', html_text, re.S)

    for row in rows:
        subj_m = re.search(
            r'<div class="subject_wrapper"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)</div>',
            row,
            re.S,
        )
        if not subj_m:
            continue

        raw_href = html.unescape(subj_m.group(1)).strip()
        if raw_href.startswith("//"):
            post_url = f"https:{raw_href}"
        elif raw_href.startswith("/"):
            post_url = f"https://dasaja.co.kr{raw_href}"
        else:
            post_url = raw_href
        post_url = re.sub(r"[?&]page=\d+", "", post_url)

        # Build stable external ID from board path + post ID
        id_m = re.search(r"/([a-zA-Z0-9_]+)/(\d+)", post_url)
        wr_m = re.search(r"wr_id=(\d+)", post_url)
        if id_m:
            ext_id = f"dasaja-{id_m.group(1)}-{id_m.group(2)}"
        elif wr_m:
            ext_id = f"dasaja-{source.id}-{wr_m.group(1)}"
        else:
            ext_id = post_url

        title = strip_html(subj_m.group(2))
        if not title:
            continue

        # Comment badge if present after title anchor
        cmt_m = re.search(r"\[(\d+)\]|>(\d+)<", subj_m.group(3))
        comment_count = int(cmt_m.group(1) or cmt_m.group(2)) if cmt_m else 0

        # Active fire icon denotes community-verified hot deal
        fire_active = "fire_icon" in row and 'class="inactive"' not in row

        cate_m = re.search(r'<div class="cate"[^>]*>([^<]*)</div>', row)
        category = html.unescape(cate_m.group(1)).strip() if cate_m and cate_m.group(1).strip() else None

        mall_m = re.search(r'wr_1=([^"&\'<>]+)', row)
        mall_raw = unquote(html.unescape(mall_m.group(1))) if mall_m else None
        retailer = normalize_kr_retailer(mall_raw) or extract_kr_retailer(title)

        price_m = re.search(r'<div class="min-price"[^>]*>\s*<span>([\d,]+)</span>', row, re.S)
        price_krw = _parse_int(price_m.group(1)) if price_m else None
        if price_krw is not None and price_krw < 500:
            price_krw = None
        if price_krw is None:
            price_krw = extract_krw_price(title)

        price_cell_m = re.search(r'<div class="double_line_wrapper price"[^>]*>(.*?)</td>', row, re.S)
        price_cell_txt = strip_html(price_cell_m.group(1)) if price_cell_m else ""
        shipping_label = extract_kr_shipping(price_cell_txt) or extract_kr_shipping(title)

        votes_m = re.search(r"<td>\s*(\d+)\s*/\s*(\d+)\s*</td>\s*<td>\s*([\d.,]+k?)\s*</td>", row, re.I)
        upvotes = int(votes_m.group(1)) if votes_m else 0
        downvotes = int(votes_m.group(2)) if votes_m else 0
        views = _parse_int(votes_m.group(3)) if votes_m else 0

        dt_m = re.search(r'<div class="double_line_wrapper write"[^>]*>\s*<div>\s*([^<]+?)\s*</div>', row, re.S)
        published_at = _parse_kst_time(dt_m.group(1), now_kst=now_kst) if dt_m else None

        sold_out = (
            is_kr_sold_out(title)
            or "line-through" in row.lower()
            or "soldout" in row.lower()
            or "sold_out" in row.lower()
        )

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=category or source.category_hint,
                external_id=ext_id,
                title=title,
                url=post_url,
                region="KR",
                published_at=published_at,
                retailer=retailer,
                price_krw=price_krw,
                shipping_label=shipping_label,
                discount_pct=extract_kr_discount_pct(title),
                thumb_score=upvotes,
                downvote_count=downvotes,
                comment_count=comment_count,
                view_count=views or 0,
                is_sold_out=sold_out,
                staff_pick=fire_active,
                category=category,
            )
        )
    return deals


_EOMISAE_SHORTHAND_PRICE_RE = re.compile(
    r"(?<!\d)(\d{1,2}\.\d{1,2})(?=\s*(?:\(|무배|직배|무료|배송|원|발|$))"
)


def _extract_eomisae_price(title: str) -> int | None:
    """Parse standard KRW prices or Eomisae shorthand like '20.9 (무배)' or '14.8발' -> 209,000원 / 148,000원."""
    price = extract_krw_price(title)
    if price is not None:
        return price
    # Eomisae fashion shorthand: "아크테릭스 20.9 (무배)" = 209,000원, "보드숏 0.59" = 5,900원
    m = _EOMISAE_SHORTHAND_PRICE_RE.search(title)
    if m:
        val = int(round(float(m.group(1)) * 10_000))
        if 1_000 <= val <= 10_000_000:
            return val
    return None


def _is_eomisae_locked_title(title: str) -> bool:
    return "전체 공개로 전환" in title or "미달 조건" in title


def parse_eomisae_html(html_text: str, source: SourceConfig, now_kst: datetime | None = None) -> list[RawDeal]:
    """Parse Eomisae fashion/lifestyle hot deal boards (eomisae.co.kr/fs, /os, /rt)."""
    deals: list[RawDeal] = []
    seen_doc_ids: set[str] = set()

    # 1. Popular highlighted table rows (<tr class="is_popular">)
    for tr in re.findall(r'<tr[^>]*class="[^"]*is_popular[^"]*"[^>]*>(.*?)</tr>', html_text, re.S):
        link_m = re.search(r'<a[^>]*class="pjax"[^>]*href="(/(?:fs|os|rt)/(\d+))"[^>]*>(.*?)</a>', tr, re.S)
        if not link_m:
            continue
        path, doc_id, raw_title = link_m.group(1), link_m.group(2), link_m.group(3)
        if doc_id in seen_doc_ids:
            continue
        title = strip_html(raw_title)
        if not title or _is_eomisae_locked_title(title):
            continue
        seen_doc_ids.add(doc_id)

        cate_m = re.search(r'<span class="cate"[^>]*>([^<]+)</span>', tr)
        category = html.unescape(cate_m.group(1)).strip(" ,") if cate_m else None

        cmt_m = re.search(r'class="pjax tt_cm"[^>]*>(\d+)</a>', tr)
        comments = int(cmt_m.group(1)) if cmt_m else 0

        heart_m = re.search(r"ion-ios-heart[^>]*></i>\s*(\d+)", tr)
        likes = int(heart_m.group(1)) if heart_m else 0

        dt_m = re.search(r"<td>\s*(\d{2}\.\d{2}\.\d{2})\s*</td>", tr)
        published_at = _parse_kst_time(dt_m.group(1), now_kst=now_kst) if dt_m else None

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=category or source.category_hint,
                external_id=f"eomisae-{doc_id}",
                title=title,
                url=f"https://eomisae.co.kr{path}",
                region="KR",
                published_at=published_at,
                retailer=extract_kr_retailer(title),
                price_krw=_extract_eomisae_price(title),
                shipping_label=extract_kr_shipping(title),
                discount_pct=extract_kr_discount_pct(title),
                thumb_score=likes,
                comment_count=comments,
                is_sold_out=is_kr_sold_out(title),
                staff_pick=True,
                category=category,
            )
        )

    # 2. Main deal cards (<div class="card_el n_ntc clear">)
    cards = html_text.split('class="card_el n_ntc clear"')[1:]
    for card in cards:
        link_m = re.search(r'<h3[^>]*>.*?<a[^>]*href="(/(?:fs|os|rt)/(\d+))"[^>]*>(.*?)</a>', card, re.S)
        if not link_m:
            continue
        path, doc_id, raw_title = link_m.group(1), link_m.group(2), link_m.group(3)
        if doc_id in seen_doc_ids:
            continue
        title = strip_html(raw_title)
        if not title or _is_eomisae_locked_title(title):
            continue
        seen_doc_ids.add(doc_id)

        cate_m = re.search(r'<span class="cate"[^>]*>([^<]+)</span>', card)
        category = html.unescape(cate_m.group(1)).strip(" ,") if cate_m else None

        dt_m = re.search(r"<span>\s*(\d{2}\.\d{2}\.\d{2})\s*</span>", card)
        published_at = _parse_kst_time(dt_m.group(1), now_kst=now_kst) if dt_m else None

        views_m = re.search(r"ion-ios-eye[^>]*></i>\s*([\d,]+)", card)
        views = _parse_int(views_m.group(1)) if views_m else 0

        cmt_m = re.search(r"ion-ios-chatbubble[^>]*></i>\s*(\d+)", card)
        comments = int(cmt_m.group(1)) if cmt_m else 0

        heart_m = re.search(r"ion-ios-heart[^>]*></i>\s*(\d+)", card)
        likes = int(heart_m.group(1)) if heart_m else 0

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=category or source.category_hint,
                external_id=f"eomisae-{doc_id}",
                title=title,
                url=f"https://eomisae.co.kr{path}",
                region="KR",
                published_at=published_at,
                retailer=extract_kr_retailer(title),
                price_krw=_extract_eomisae_price(title),
                shipping_label=extract_kr_shipping(title),
                discount_pct=extract_kr_discount_pct(title),
                thumb_score=likes,
                comment_count=comments,
                view_count=views or 0,
                is_sold_out=is_kr_sold_out(title),
                category=category,
            )
        )
    return deals


def parse_korean_board_html(
    content: bytes,
    source: SourceConfig,
    now_kst: datetime | None = None,
) -> list[RawDeal]:
    """Dispatch Korean community HTML bytes to the matching board parser."""
    html_text = content.decode("utf-8", errors="replace")
    if source.type == "ruliweb":
        return parse_ruliweb_html(html_text, source, now_kst=now_kst)
    if source.type == "clien":
        return parse_clien_html(html_text, source, now_kst=now_kst)
    if source.type == "dealbada":
        return parse_dealbada_html(html_text, source, now_kst=now_kst)
    if source.type == "hotgusa":
        return parse_hotgusa_html(html_text, source, now_kst=now_kst)
    if source.type == "dasaja":
        return parse_dasaja_html(html_text, source, now_kst=now_kst)
    if source.type == "eomisae":
        return parse_eomisae_html(html_text, source, now_kst=now_kst)
    return []


_IGNORED_BUY_HOSTS = (
    "ruliweb.com",
    "clien.net",
    "dealbada.com",
    "eomisae.co.kr",
    "dasaja.co.kr",
    "hotgusa.com",
    "cloudfront.net",
    "daumcdn.net",
    "imgur.com",
    "w3.org",
    "schema.org",
    "googleapis.com",
    "gstatic.com",
    "googletagmanager.com",
    "googlesyndication.com",
    "doubleclick.net",
    "facebook.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "t.me",
)

_SHOPPING_URL_RE = re.compile(
    r'href=["\'](https?://[^"\'<>\s]+)["\']',
    re.IGNORECASE,
)


def _is_external_shop_url(url: str) -> bool:
    if not is_safe_http_url(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if any(host == dom or host.endswith(f".{dom}") for dom in _IGNORED_BUY_HOSTS):
        return False
    # Skip obvious static image links
    path = urlparse(url).path.lower()
    if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")):
        return False
    return True


def unwrap_redirect_url(url: str) -> str:
    """Unwrap Ruliweb (ol=) and LinkPrice/affiliate (tu=, url=, u=) redirector URLs cleanly."""
    current = html.unescape(url).strip()
    for _ in range(3):
        parsed = urlparse(current)
        qs = parse_qs(parsed.query)
        next_url = None
        for param in ("ol", "tu", "target_url", "redirect_url", "url", "u"):
            vals = qs.get(param)
            if vals:
                cand = unquote(vals[0]).strip()
                if is_safe_http_url(cand):
                    next_url = cand
                    break
        if next_url and next_url != current:
            current = next_url
        else:
            break
    return current


def extract_buy_url_from_post_html(html_text: str, source_type: str) -> str | None:
    """Extract the primary direct shopping URL from a Korean hot deal post page."""
    candidates: list[str] = []

    if source_type == "ruliweb":
        # 1. Ruliweb dedicated '출처 :' box (holds the primary deal URL wrapped in web.ruliweb.com/link.php?ol=...)
        src_box = re.search(r'class="source_url[^"]*"[^>]*>(.*?)</(?:div|td|p|tr)>', html_text, re.S | re.I)
        if src_box:
            candidates.extend(_SHOPPING_URL_RE.findall(src_box.group(1)))
        # 2. Article body fallback
        body = re.search(r'class="view_content[^"]*"[^>]*>(.*?)</article>', html_text, re.S | re.I)
        if body:
            candidates.extend(_SHOPPING_URL_RE.findall(body.group(1)))

    elif source_type == "clien":
        # 1. Clien dedicated outlink block
        outlink = re.search(r'class="outlink"[^>]*>(.*?)</div>', html_text, re.S | re.I)
        if outlink:
            candidates.extend(_SHOPPING_URL_RE.findall(outlink.group(1)))
        # 2. Clien post body fallback
        body = re.search(r'class="post_article"[^>]*>(.*?)</article>', html_text, re.S | re.I)
        if body:
            candidates.extend(_SHOPPING_URL_RE.findall(body.group(1)))

    elif source_type == "dealbada":
        # 1. Dealbada link section & post body
        link_sec = re.search(r'id="bo_v_link"[^>]*>(.*?)</section>', html_text, re.S | re.I)
        if link_sec:
            candidates.extend(_SHOPPING_URL_RE.findall(link_sec.group(1)))
        body = re.search(r'id="bo_v_con"[^>]*>(.*?)</div>', html_text, re.S | re.I)
        if body:
            candidates.extend(_SHOPPING_URL_RE.findall(body.group(1)))

    elif source_type == "eomisae":
        # Eomisae Rhymix/XE article body
        body = re.search(r'class="rd_body[^"]*"[^>]*>(.*?)</article>', html_text, re.S | re.I)
        if not body:
            body = re.search(r"<article[^>]*>(.*?)</article>", html_text, re.S | re.I)
        if body:
            candidates.extend(_SHOPPING_URL_RE.findall(body.group(1)))

    for raw in candidates:
        unwrapped = unwrap_redirect_url(raw)
        if _is_external_shop_url(unwrapped):
            return unwrapped
    return None


_SKIP_BUY_URL_LOOKUP_TYPES = {"hotgusa", "dasaja"}


def _pick_buy_url_lookup_target(deal: ScoredDeal) -> tuple[str, str] | None:
    """Pick a (post_url, source_type) that exposes a direct shopping URL cleanly without violating robots.txt."""
    p = deal.primary
    if p.source_type not in _SKIP_BUY_URL_LOOKUP_TYPES and is_safe_http_url(p.url):
        return (p.url, p.source_type)
    # Cross-community fallback: if merged with Ruliweb/Clien/Dealbada/Eomisae, extract buy_url from there!
    host_to_type = (
        ("ruliweb.com", "ruliweb"),
        ("clien.net", "clien"),
        ("dealbada.com", "dealbada"),
        ("eomisae.co.kr", "eomisae"),
    )
    for url in deal.merged_urls.values():
        if not is_safe_http_url(url):
            continue
        host = (urlparse(url).hostname or "").lower()
        for dom, stype in host_to_type:
            if host == dom or host.endswith(f".{dom}"):
                return (url, stype)
    return None


async def _resolve_single_buy_url(client: httpx.AsyncClient, deal: ScoredDeal) -> None:
    p = deal.primary
    if not deal.is_domestic_kr or p.buy_url:
        return
    target = _pick_buy_url_lookup_target(deal)
    if not target:
        return
    post_url, source_type = target
    try:
        resp = await client.get(
            post_url,
            headers={
                "User-Agent": "BFDealRadar/0.6 (+https://github.com/jubimakesmagic-design/bf-deal-radar)",
                "Accept-Language": "ko-KR,ko;q=0.9",
            },
            follow_redirects=True,
        )
        if resp.status_code != 200 or len(resp.content) > 2 * 1024 * 1024:
            return
        buy_url = extract_buy_url_from_post_html(resp.text, source_type)
        if buy_url:
            p.buy_url = buy_url
            if p.asin is None:
                p.asin = extract_asin(buy_url)
    except Exception as exc:
        logger.debug("Direct buy_url lookup skipped for %s: %s", post_url, type(exc).__name__)


async def resolve_korean_buy_urls(deals: list[ScoredDeal], timeout: float = 6.0) -> None:
    """Concurrently resolve direct shopping URLs for the final set of outgoing Korean deals."""
    targets = [
        d
        for d in deals
        if d.is_domestic_kr and not d.primary.buy_url and _pick_buy_url_lookup_target(d) is not None
    ]
    if not targets:
        return
    async with httpx.AsyncClient(timeout=timeout, max_redirects=4) as client:
        await asyncio.gather(*(_resolve_single_buy_url(client, d) for d in targets))
