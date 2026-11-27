"""Async RSS/Atom deal feed fetcher and source-specific parsers."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from bf_deal_radar.models import RawDeal, SourceConfig
from bf_deal_radar.parser import (
    extract_asin,
    extract_coupon_code,
    extract_price_info,
    extract_retailer,
    strip_html,
)
from bf_deal_radar.security import is_safe_http_url
from bf_deal_radar.sources.korean import parse_korean_board_html

logger = logging.getLogger(__name__)
_KOREAN_HTML_TYPES = {"ruliweb", "clien", "dealbada"}

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_SD_THUMB_RE = re.compile(r"Thumb\s+Score:\s*([+-]?\d+)", re.IGNORECASE)
_DN_SUMMARY_TITLE_RE = re.compile(
    r'<div[^>]+class=["\']snippet summary["\'][^>]+title=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_AMAZON_HREF_RE = re.compile(
    r'href=["\'](https?://(?:www\.)?amazon\.[a-z.]{2,6}/[^"\']+)["\']',
    re.IGNORECASE,
)


def _child_map(elem: ET.Element) -> dict[str, list[ET.Element]]:
    """Map local XML tag names (without namespace) to child elements."""
    mapping: dict[str, list[ET.Element]] = {}
    for child in elem:
        local = child.tag.split("}")[-1].lower()
        mapping.setdefault(local, []).append(child)
    return mapping


def _first_text(cmap: dict[str, list[ET.Element]], *names: str) -> str:
    for name in names:
        elems = cmap.get(name.lower())
        if elems:
            text = (elems[0].text or "").strip()
            if text:
                return text
    return ""


def _parse_datetime(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    # Try RFC 2822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Try ISO 8601 (Atom published/updated)
    try:
        iso = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _extract_atom_link(cmap: dict[str, list[ET.Element]]) -> str:
    for link_el in cmap.get("link", []):
        rel = link_el.attrib.get("rel", "alternate")
        href = (link_el.attrib.get("href") or "").strip()
        if href and rel == "alternate":
            return href
    for link_el in cmap.get("link", []):
        href = (link_el.attrib.get("href") or (link_el.text or "")).strip()
        if href:
            return href
    return ""


def _extract_image_url(cmap: dict[str, list[ET.Element]], raw_html: str) -> str | None:
    # TechBargains explicit imagelink
    img_link = _first_text(cmap, "imagelink")
    if img_link:
        return img_link
    # media:content
    for el in cmap.get("content", []):
        if el.attrib.get("medium") == "image" and el.attrib.get("url"):
            return el.attrib["url"]
    # First <img> in HTML
    if raw_html:
        m = _IMG_SRC_RE.search(raw_html)
        if m:
            return html.unescape(m.group(1))
    return None


def parse_feed_xml(xml_bytes: bytes, source: SourceConfig) -> list[RawDeal]:
    """Parse RSS 2.0 or Atom XML bytes into structured RawDeal objects."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("Failed to parse XML from %s: %s", source.name, exc)
        return []

    is_atom = root.tag.endswith("}feed") or root.tag == "feed"
    items = root.findall(f".//{_ATOM_NS}entry") if is_atom else root.findall(".//item")
    if not items and is_atom:
        items = root.findall(".//entry")

    deals: list[RawDeal] = []
    for item in items:
        cmap = _child_map(item)
        title = html.unescape(_first_text(cmap, "title"))
        if not title:
            continue

        url = _extract_atom_link(cmap) if is_atom else _first_text(cmap, "link")
        if not url:
            continue

        ext_id = _first_text(cmap, "guid", "id") or url
        pub_raw = _first_text(cmap, "pubdate", "published", "updated")
        published_at = _parse_datetime(pub_raw)

        desc_raw = _first_text(cmap, "description", "summary")
        encoded_raw = _first_text(cmap, "encoded", "content")
        raw_html = encoded_raw or desc_raw

        # Source-specific rich metadata extraction
        thumb_score: int | None = None
        staff_pick = False
        explicit_price: float | None = None
        explicit_retailer: str | None = None
        category: str | None = None

        if source.type == "slickdeals":
            if encoded_raw:
                m = _SD_THUMB_RE.search(encoded_raw)
                if m:
                    thumb_score = int(m.group(1))
            plain_desc = strip_html(desc_raw or encoded_raw)

        elif source.type == "dealnews":
            # Skip generic store-wide sale banners ("Up to 90% off") and keep individual product/deal items
            deal_type = _first_text(cmap, "dealtype").lower()
            if deal_type == "sale":
                continue
            explicit_retailer = _first_text(cmap, "retailer") or None
            price_str = _first_text(cmap, "price")
            if price_str:
                try:
                    val = float(price_str)
                    if val > 0:
                        explicit_price = val
                except ValueError:
                    pass
            staff_pick = _first_text(cmap, "staffpick").lower() == "true"
            category = _first_text(cmap, "category") or None
            # DealNews puts complete un-truncated editor note in <div class="snippet summary" title="...">
            dn_match = _DN_SUMMARY_TITLE_RE.search(desc_raw)
            if dn_match:
                plain_desc = html.unescape(dn_match.group(1)).strip()
            else:
                plain_desc = strip_html(desc_raw)

        elif source.type == "techbargains":
            explicit_retailer = _first_text(cmap, "vendorname") or None
            category = _first_text(cmap, "category") or None
            plain_desc = strip_html(desc_raw)

        else:
            cats = [
                (el.attrib.get("term") or (el.text or "")).strip()
                for el in cmap.get("category", [])
            ]
            cats = [c for c in cats if c]
            category = ", ".join(cats[:3]) if cats else None
            # Blog feeds (9to5Toys, Verge) append unrelated "More Deals" footers; keep only the lead snippet
            plain_desc = strip_html(desc_raw or encoded_raw)[:380]

        # Extract embedded direct Amazon link if feed links to an article/aggregator
        amazon_href = ""
        if raw_html:
            am = _AMAZON_HREF_RE.search(raw_html[:3000])
            if am:
                amazon_href = html.unescape(am.group(1))

        asin = extract_asin(url, amazon_href, raw_html[:3000], desc_raw[:1500])
        price_info = extract_price_info(title, plain_desc, explicit_price=explicit_price)
        coupon_code = extract_coupon_code(raw_html, f"{title} {plain_desc}")
        retailer = extract_retailer(
            title,
            plain_desc,
            amazon_href or url,
            explicit=explicit_retailer,
        )
        image_url = _extract_image_url(cmap, raw_html)

        deals.append(
            RawDeal(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                source_base_score=source.base_score,
                category_hint=source.category_hint,
                external_id=ext_id,
                title=title.strip(),
                url=url.strip(),
                description=plain_desc[:600],
                published_at=published_at,
                retailer=retailer,
                price_usd=price_info.price_usd,
                original_price_usd=price_info.original_price_usd,
                discount_pct=price_info.discount_pct,
                savings_usd=price_info.savings_usd,
                thumb_score=thumb_score,
                staff_pick=staff_pick,
                coupon_code=coupon_code,
                asin=asin,
                image_url=image_url,
                category=category,
                raw_html=raw_html[:1500],
            )
        )

    return deals


MAX_FEED_BYTES = 5 * 1024 * 1024  # 5 MB safety cap per RSS/Atom feed


async def _fetch_single_source(
    client: httpx.AsyncClient,
    source: SourceConfig,
    cache_entry: tuple[str | None, str | None] | None = None,
) -> tuple[list[RawDeal], tuple[str, str | None, str | None] | None]:
    if not is_safe_http_url(source.url):
        logger.warning("Skipping source with unsafe URL scheme: %s", source.name)
        return [], None

    headers = {
        "User-Agent": (
            "linux:bf-deal-radar:0.4"
            if source.type == "reddit"
            else "BFDealRadar/0.4 (+https://github.com/jubimakesmagic-design/bf-deal-radar)"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            if source.type in _KOREAN_HTML_TYPES
            else "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8"
        ),
    }
    if source.region == "KR" or source.type in _KOREAN_HTML_TYPES:
        headers["Accept-Language"] = "ko-KR,ko;q=0.9,en;q=0.5"
    if cache_entry:
        etag, last_mod = cache_entry
        if etag:
            headers["If-None-Match"] = etag
        if last_mod:
            headers["If-Modified-Since"] = last_mod

    try:
        resp = await client.get(source.url, headers=headers, follow_redirects=True)
        if resp.status_code == 304:
            logger.info("304 Not Modified (cached): %s", source.name)
            return [], None
        resp.raise_for_status()
        if len(resp.content) > MAX_FEED_BYTES:
            logger.warning("Skipping oversized feed from %s (%d bytes)", source.name, len(resp.content))
            return [], None
        deals = (
            parse_korean_board_html(resp.content, source)
            if source.type in _KOREAN_HTML_TYPES
            else parse_feed_xml(resp.content, source)
        )
        new_etag = resp.headers.get("etag")
        new_lm = resp.headers.get("last-modified")
        cache_update = (source.id, new_etag, new_lm) if (new_etag or new_lm) else None
        logger.info("Fetched %d deals from %s", len(deals), source.name)
        return deals, cache_update
    except Exception as exc:
        logger.warning("Failed to fetch %s (%s): %s", source.name, type(exc).__name__, exc)
        return [], None


async def fetch_all_sources_with_cache(
    sources: list[SourceConfig],
    *,
    feed_cache: dict[str, tuple[str | None, str | None]] | None = None,
    timeout: float = 12.0,
) -> tuple[list[RawDeal], list[tuple[str, str | None, str | None]]]:
    """Fetch all enabled sources concurrently using optional ETag/Last-Modified cache."""
    enabled = [s for s in sources if s.enabled]
    if not enabled:
        return [], []

    cache_map = feed_cache or {}
    limits = httpx.Limits(max_connections=12, max_keepalive_connections=6)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, max_redirects=5) as client:
        results = await asyncio.gather(
            *(_fetch_single_source(client, source, cache_map.get(source.id)) for source in enabled)
        )

    all_deals: list[RawDeal] = []
    cache_updates: list[tuple[str, str | None, str | None]] = []
    for deals, update in results:
        all_deals.extend(deals)
        if update is not None:
            cache_updates.append(update)
    return all_deals, cache_updates


async def fetch_all_sources(
    sources: list[SourceConfig],
    timeout: float = 12.0,
) -> list[RawDeal]:
    """Fetch all enabled sources concurrently without conditional cache."""
    deals, _ = await fetch_all_sources_with_cache(sources, feed_cache=None, timeout=timeout)
    return deals
