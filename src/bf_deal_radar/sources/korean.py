"""Parsers for open Korean domestic hot deal communities (Ruliweb, Clien, Dealbada)."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from bf_deal_radar.models import RawDeal, SourceConfig
from bf_deal_radar.parser import (
    extract_kr_discount_pct,
    extract_kr_retailer,
    extract_kr_shipping,
    extract_krw_price,
    is_kr_sold_out,
    strip_html,
)

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
    """Parse KST timestamps like '10:23', '11-26', '2026.11.26', or '2026-11-27 07:55:45'."""
    if not raw:
        return None
    s = raw.strip()
    now_kst = now_kst or datetime.now(KST)

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
    m_ymd = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", s)
    if m_ymd:
        dt = datetime(int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3)), 12, 0, tzinfo=KST)
        return dt.astimezone(timezone.utc)

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
                external_id=f"dealbada-{wr_id}",
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
    return []
