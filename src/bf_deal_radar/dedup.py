"""Canonical URL/ASIN normalization and cross-source deal clustering."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bf_deal_radar.models import RawDeal, ScoredDeal
from bf_deal_radar.parser import normalize_title_tokens

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "iref",
    "ref",
    "tag",
    "smid",
    "psc",
    "th",
    "linkcode",
    "camp",
    "creative",
    "fbclid",
    "gclid",
}

_SD_THREAD_RE = re.compile(r"slickdeals\.net/f/(\d+)", re.IGNORECASE)
_DN_ID_RE = re.compile(r"dealnews\.com/(?:products/.+/)?(\d+)\.html", re.IGNORECASE)


def canonicalize_url(url: str) -> str:
    """Normalize a deal URL by removing tracking query params and normalizing Amazon/SD/DN links."""
    if not url:
        return ""
    url = url.strip()

    sd_match = _SD_THREAD_RE.search(url)
    if sd_match:
        return f"slickdeals://thread/{sd_match.group(1)}"

    dn_match = _DN_ID_RE.search(url)
    if dn_match:
        return f"dealnews://deal/{dn_match.group(1)}"

    parsed = urlparse(url)
    clean_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    path = parsed.path.rstrip("/")
    return urlunparse((
        parsed.scheme.lower() or "https",
        parsed.netloc.lower().removeprefix("www."),
        path,
        "",
        urlencode(sorted(clean_query)),
        "",
    ))


def make_url_key(url: str) -> str | None:
    """Produce a stable URL-based deduplication key for a single deal URL."""
    canon_url = canonicalize_url(url)
    if not canon_url:
        return None
    digest = hashlib.sha1(canon_url.encode("utf-8")).hexdigest()[:16]
    return f"url:{digest}"


def make_canonical_key(deal: RawDeal) -> str:
    """Produce a stable deduplication key for a single deal."""
    if deal.asin:
        return f"asin:{deal.asin.upper()}"

    url_key = make_url_key(deal.url)
    if url_key:
        return url_key

    tokens = sorted(normalize_title_tokens(deal.title))
    raw = " ".join(tokens[:10])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"title:{digest}"


def _prices_compatible(p1: float | None, p2: float | None) -> bool:
    # For title-similarity merges (without shared ASIN/URL), require both deals to have compatible prices
    # so priceless roundup articles don't merge into concrete single-product deals.
    if p1 is None and p2 is None:
        return True
    if p1 is None or p2 is None:
        return False
    hi = max(p1, p2)
    if hi <= 0:
        return True
    return abs(p1 - p2) / hi <= 0.12


def _is_same_deal(a: RawDeal, b: RawDeal, tokens_a: set[str], tokens_b: set[str]) -> bool:
    if a.region != b.region:
        return False

    if a.asin and b.asin and a.asin == b.asin:
        return True

    if canonicalize_url(a.url) == canonicalize_url(b.url):
        return True

    if not tokens_a or not tokens_b:
        return False

    overlap = tokens_a & tokens_b
    if len(overlap) < 3:
        return False

    union = tokens_a | tokens_b
    jaccard = len(overlap) / len(union)
    containment = len(overlap) / min(len(tokens_a), len(tokens_b))

    price_ok = (
        _prices_compatible(
            float(a.price_krw) if a.price_krw is not None else None,
            float(b.price_krw) if b.price_krw is not None else None,
        )
        if a.region == "KR"
        else _prices_compatible(a.price_usd, b.price_usd)
    )
    if (jaccard >= 0.55 or (containment >= 0.8 and len(overlap) >= 4)) and price_ok:
        return True
    return False


def _merge_cluster(cluster: list[RawDeal]) -> ScoredDeal:
    """Merge duplicate/cross-source deals into a single enriched ScoredDeal."""
    def _rank(d: RawDeal) -> tuple[int, int, int, int, float]:
        has_price = 1 if (d.price_krw is not None or d.price_usd is not None) else 0
        return (
            1 if d.asin else 0,
            d.source_base_score + (10 if d.staff_pick else 0) + min(20, d.thumb_score or 0),
            has_price,
            1 if d.discount_pct is not None else 0,
            d.published_at.timestamp() if d.published_at else 0.0,
        )

    ordered = sorted(cluster, key=_rank, reverse=True)
    primary = ordered[0]

    merged_sources: list[str] = []
    merged_urls: dict[str, str] = {}
    seen_source_names: set[str] = set()

    for item in ordered:
        if item.source_name not in seen_source_names:
            seen_source_names.add(item.source_name)
            merged_sources.append(item.source_name)
            merged_urls[item.source_name] = item.url

        if primary.asin is None and item.asin:
            primary.asin = item.asin
        if primary.price_usd is None and item.price_usd is not None:
            primary.price_usd = item.price_usd
        if primary.original_price_usd is None and item.original_price_usd is not None:
            primary.original_price_usd = item.original_price_usd
        if primary.price_krw is None and item.price_krw is not None:
            primary.price_krw = item.price_krw
        if primary.shipping_label is None and item.shipping_label:
            primary.shipping_label = item.shipping_label
        if item.discount_pct is not None and (
            primary.discount_pct is None or item.discount_pct > primary.discount_pct
        ):
            primary.discount_pct = item.discount_pct
        if item.savings_usd is not None and (
            primary.savings_usd is None or item.savings_usd > primary.savings_usd
        ):
            primary.savings_usd = item.savings_usd
        if item.thumb_score is not None and (
            primary.thumb_score is None or item.thumb_score > primary.thumb_score
        ):
            primary.thumb_score = item.thumb_score
        if item.downvote_count is not None and (
            primary.downvote_count is None or item.downvote_count > primary.downvote_count
        ):
            primary.downvote_count = item.downvote_count
        if item.comment_count is not None and (
            primary.comment_count is None or item.comment_count > primary.comment_count
        ):
            primary.comment_count = item.comment_count
        if item.view_count is not None and (
            primary.view_count is None or item.view_count > primary.view_count
        ):
            primary.view_count = item.view_count
        if item.is_sold_out:
            primary.is_sold_out = True
        if item.staff_pick:
            primary.staff_pick = True
        if primary.buy_url is None and item.buy_url:
            primary.buy_url = item.buy_url
        if primary.coupon_code is None and item.coupon_code:
            primary.coupon_code = item.coupon_code
        if primary.retailer is None and item.retailer:
            primary.retailer = item.retailer
        if primary.image_url is None and item.image_url:
            primary.image_url = item.image_url
        if len(item.description) > len(primary.description):
            primary.description = item.description

    canonical_key = make_canonical_key(primary)
    return ScoredDeal(
        canonical_key=canonical_key,
        primary=primary,
        merged_sources=merged_sources,
        merged_urls=merged_urls,
    )


def cluster_deals(raw_deals: list[RawDeal]) -> list[ScoredDeal]:
    """Group duplicate and cross-source sightings of the same deal."""
    clusters: list[tuple[list[RawDeal], set[str]]] = []

    for deal in raw_deals:
        tokens = normalize_title_tokens(deal.title)
        matched = False
        for group, group_tokens in clusters:
            if _is_same_deal(group[0], deal, group_tokens, tokens):
                group.append(deal)
                group_tokens.update(tokens)
                matched = True
                break
        if not matched:
            clusters.append(([deal], set(tokens)))

    return [_merge_cluster(group) for group, _ in clusters]
