"""Live Korean market baseline lookup via official Naver Shopping Search Open API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import statistics
from datetime import datetime, timezone
from typing import Any

import httpx

from bf_deal_radar.models import MarketBaseline, NaverShoppingConfig, ScoredDeal
from bf_deal_radar.parser import (
    build_naver_search_query,
    extract_product_specs,
    is_kr_accessory_title,
    is_kr_promo_range_title,
    normalize_brand_token,
    strip_html,
)
from bf_deal_radar.security import is_safe_http_url, redact_secrets, register_secrets
from bf_deal_radar.storage import DealStorage

logger = logging.getLogger(__name__)

NAVER_SHOPPING_API_URL = "https://openapi.naver.com/v1/search/shop.json"
_VALID_PRODUCT_TYPES = {"", "1", "2", "3"}

_UNIT_TOKEN_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:tb|gb|mb|kg|g|ml|l|리터|인치|형|inch|개입|개|입|캔|병|팩|봉지|봉|매|정|캡슐|포|구|박스|box|세트|set|p|ea|롤|장|w|hz|mm|cm)$",
    re.IGNORECASE,
)

_TOKEN_STOPWORDS = {
    "무료",
    "무배",
    "무료배송",
    "할인",
    "특가",
    "최저가",
    "역대가",
    "초특가",
    "쿠폰",
    "정품",
    "공식",
    "국내",
    "정발",
    "단품",
    "본품",
    "새상품",
    "배송",
    "로켓",
    "직구",
    "추천",
    "인기",
    "베스트",
}


def _tokenize_for_match(text: str) -> set[str]:
    raw = re.findall(r"[a-z0-9가-힣]+", (text or "").lower())
    out: set[str] = set()
    for tok in raw:
        norm = normalize_brand_token(tok)
        if len(norm) < 2 or norm in _TOKEN_STOPWORDS:
            continue
        out.add(norm)
    return out


def _extract_model_codes(tokens: set[str]) -> set[str]:
    """Return alphanumeric model tokens (e.g. 1000xm5, m4, f75, s25) excluding pure unit quantities."""
    codes: set[str] = set()
    for tok in tokens:
        if _UNIT_TOKEN_RE.match(tok):
            continue
        has_alpha = any("a" <= c <= "z" for c in tok)
        has_digit = any(c.isdigit() for c in tok)
        if has_alpha and has_digit:
            codes.add(tok)
    return codes


def _numeric_sets_close(set_a: set[str], set_b: set[str], tol: float = 0.05) -> bool:
    """Return True if every numeric value in set_a has a matching value in set_b within tolerance."""
    if not set_a:
        return True
    if not set_b:
        return False
    nums_b = [float(x) for x in set_b]
    for raw_a in set_a:
        val_a = float(raw_a)
        if not any(abs(val_a - val_b) <= max(1.0, val_a * tol) for val_b in nums_b):
            return False
    return True


def _is_ram_tier(tok: str) -> bool:
    if tok.endswith("gb"):
        try:
            return int(tok[:-2]) <= 64
        except ValueError:
            return False
    return False


def _specs_conflict(deal_specs: dict[str, set[str]], item_specs: dict[str, set[str]]) -> bool:
    """Return True if a Naver item's capacity/weight/volume/count/pack conflicts with the deal."""
    # Storage & RAM (e.g., 256GB vs 512GB, 1TB vs 2TB, 16GB vs 24GB; allow item adding RAM when deal gave SSD only)
    if deal_specs["storage"]:
        if not item_specs["storage"] or not deal_specs["storage"].issubset(item_specs["storage"]):
            return True
        extra = item_specs["storage"] - deal_specs["storage"]
        deal_has_disk = any(not _is_ram_tier(t) for t in deal_specs["storage"])
        deal_has_ram = any(_is_ram_tier(t) for t in deal_specs["storage"])
        for tok in extra:
            if not _is_ram_tier(tok) and deal_has_disk:
                return True  # Reject multi-capacity listing (e.g., "1TB 2TB 4TB")
            if _is_ram_tier(tok) and deal_has_ram:
                return True  # Reject conflicting/multi RAM listing
    elif item_specs["storage"]:
        # Deal didn't specify storage, allow single-spec items
        pass

    # Weight & Volume (within ±5% tolerance so 1kg == 1000g)
    if deal_specs["weight"] and not _numeric_sets_close(deal_specs["weight"], item_specs["weight"]):
        return True
    if deal_specs["volume"] and not _numeric_sets_close(deal_specs["volume"], item_specs["volume"]):
        return True

    # Pack counts (bidirectional guard so single-unit deals never compare against multi-packs)
    if deal_specs["count"]:
        if not item_specs["count"] or deal_specs["count"].isdisjoint(item_specs["count"]):
            return True
    else:
        # Deal is single-unit; reject Naver multi-pack bundles (>= 2 units)
        if any(int(c) >= 2 for c in item_specs["count"]):
            return True

    # 1+1 / 2+1 bundles must match symmetrically
    if deal_specs["plus_pack"] != item_specs["plus_pack"]:
        return True

    # Screen size (27" vs 32", 65" vs 75")
    if deal_specs["screen"]:
        if not item_specs["screen"] or deal_specs["screen"] != item_specs["screen"]:
            return True

    return False


def evaluate_naver_items(
    deal_title: str,
    query: str,
    items: list[dict[str, Any]],
) -> tuple[int, int, int] | None:
    """Filter Naver Shopping items and compute (reference_krw, lowest_krw, sample_count).

    Filters out used/discontinued items, accessory mismatches, spec/quantity mismatches,
    low-overlap titles, and extreme bait-price outliers.
    """
    if not items or not query:
        return None

    query_tokens = _tokenize_for_match(query)
    if len(query_tokens) < 2:
        return None

    model_codes = _extract_model_codes(query_tokens)
    deal_specs = extract_product_specs(query)
    deal_is_acc = is_kr_accessory_title(deal_title)

    matched: list[tuple[int, str]] = []  # (lprice, productType)
    for item in items:
        ptype = str(item.get("productType", "") or "").strip()
        if ptype not in _VALID_PRODUCT_TYPES:
            continue

        raw_lprice = item.get("lprice")
        try:
            lprice = int(str(raw_lprice).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if lprice < 500 or lprice > 50_000_000:
            continue

        item_title = strip_html(str(item.get("title", "") or ""))
        if not item_title:
            continue

        # Accessory symmetry: main device deals reject cases/films/earpads, and vice versa
        if is_kr_accessory_title(item_title) != deal_is_acc:
            continue

        # Spec & pack-count symmetry
        item_specs = extract_product_specs(item_title)
        if _specs_conflict(deal_specs, item_specs):
            continue

        # Token overlap & model-code fidelity
        brand_extra = f"{item.get('brand', '')} {item.get('maker', '')}"
        item_tokens = _tokenize_for_match(f"{item_title} {brand_extra}")
        if not item_tokens:
            continue

        if model_codes and not model_codes.issubset(item_tokens):
            continue

        overlap = query_tokens & item_tokens
        if len(overlap) < 2:
            continue
        if len(overlap) / len(query_tokens) < 0.50:
            continue

        matched.append((lprice, ptype))

    if len(matched) < 3:
        return None

    # Trim extreme bait/inflated outliers relative to raw median
    raw_prices = [p for p, _ in matched]
    raw_median = statistics.median(raw_prices)
    clean = [(p, pt) for p, pt in matched if raw_median * 0.45 <= p <= raw_median * 2.20]
    if len(clean) < 3:
        return None

    clean_prices = sorted(p for p, _ in clean)
    median_krw = int(round(statistics.median(clean_prices)))
    lowest_krw = clean_prices[0]

    # Conservative reference price: midpoint between clean lowest and median
    # (and capped by official Naver price-comparison catalog items when present)
    reference_krw = int(round((median_krw + lowest_krw) / 2.0))
    catalog_prices = [p for p, pt in clean if pt == "1"]
    if catalog_prices:
        catalog_med = int(round(statistics.median(catalog_prices)))
        reference_krw = min(reference_krw, catalog_med)

    if reference_krw <= 0:
        return None

    return reference_krw, lowest_krw, len(clean_prices)


def build_naver_baseline(
    deal_price_krw: int,
    reference_krw: int,
    lowest_krw: int,
    sample_count: int,
    query: str,
) -> MarketBaseline | None:
    """Create a MarketBaseline comparing a deal's KRW price against Naver market stats."""
    if deal_price_krw <= 0 or reference_krw <= 0 or sample_count < 3:
        return None
    drop_pct = round((1.0 - deal_price_krw / reference_krw) * 100.0, 1)
    return MarketBaseline(
        source="naver_shopping",
        reference_price_krw=reference_krw,
        lowest_price_krw=lowest_krw,
        sample_count=sample_count,
        drop_pct=drop_pct,
        query=query,
    )


def make_market_query_key(query: str, product_signature: str = "") -> str:
    """Stable SQLite cache key for a Naver Shopping product query."""
    seed = product_signature or query.strip().lower()
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"naver:{digest}"


def is_eligible_for_naver_lookup(deal: ScoredDeal) -> bool:
    """Return True if a Korean deal is a concrete priced product suitable for Naver market comparison."""
    p = deal.primary
    if not deal.is_domestic_kr or deal.excluded_reason is not None:
        return False
    if p.price_krw is None or p.price_krw < 1000:
        return False
    if is_kr_promo_range_title(p.title):
        return False
    query = build_naver_search_query(p.title)
    if len(query) < 4 or len(_tokenize_for_match(query)) < 2:
        return False
    return True


async def fetch_naver_market_baselines(
    deals: list[ScoredDeal],
    config: NaverShoppingConfig,
    *,
    storage: DealStorage | None = None,
    read_only_cache: bool = False,
    now: datetime | None = None,
) -> dict[str, MarketBaseline]:
    """Fetch live Naver Shopping baselines for candidate Korean deals (with SQLite TTL cache).

    Returns {deal.canonical_key: MarketBaseline}.
    """
    if not config.is_configured or not deals:
        return {}
    if not is_safe_http_url(NAVER_SHOPPING_API_URL, require_https=True):
        return {}

    register_secrets(config.client_id, config.client_secret)
    secrets_tuple = (config.client_id, config.client_secret)
    now_dt = now or datetime.now(timezone.utc)

    baselines: dict[str, MarketBaseline] = {}
    to_query: list[tuple[ScoredDeal, str, str]] = []  # (deal, query, cache_key)
    seen_cache_keys: dict[str, list[tuple[ScoredDeal, str]]] = {}

    for deal in deals:
        if not is_eligible_for_naver_lookup(deal):
            continue
        query = build_naver_search_query(deal.primary.title)
        cache_key = make_market_query_key(query, deal.product_signature)

        if storage is not None:
            cached = storage.get_cached_market_price(
                cache_key,
                ttl_hours=config.cache_ttl_hours,
                now=now_dt,
            )
            if cached is not None:
                ref_krw = cached.get("reference_price_krw")
                low_krw = cached.get("lowest_price_krw")
                cnt = int(cached.get("sample_count") or 0)
                if ref_krw and low_krw and cnt >= 3 and deal.primary.price_krw:
                    bl = build_naver_baseline(deal.primary.price_krw, int(ref_krw), int(low_krw), cnt, query)
                    if bl is not None:
                        baselines[deal.canonical_key] = bl
                continue

        if cache_key not in seen_cache_keys:
            if len(to_query) >= max(0, config.max_calls_per_run):
                continue
            seen_cache_keys[cache_key] = [(deal, query)]
            to_query.append((deal, query, cache_key))
        else:
            seen_cache_keys[cache_key].append((deal, query))

    if not to_query:
        return baselines

    headers = {
        "X-Naver-Client-Id": config.client_id,
        "X-Naver-Client-Secret": config.client_secret,
        "Accept": "application/json",
    }
    sem = asyncio.Semaphore(4)
    abort_event = asyncio.Event()
    cache_writes: list[tuple[str, str, int | None, int | None, int]] = []

    async def _query_one(
        client: httpx.AsyncClient,
        deal: ScoredDeal,
        query: str,
        cache_key: str,
    ) -> None:
        if abort_event.is_set():
            return
        async with sem:
            if abort_event.is_set():
                return
            try:
                resp = await client.get(
                    NAVER_SHOPPING_API_URL,
                    headers=headers,
                    params={"query": query, "display": 15, "sort": "sim"},
                )
                if resp.status_code in (401, 403, 429):
                    logger.warning(
                        "Naver Shopping API returned HTTP %d; halting further lookups for this run.",
                        resp.status_code,
                    )
                    abort_event.set()
                    return
                if resp.status_code >= 400:
                    logger.warning("Naver Shopping API HTTP %d for query '%s'", resp.status_code, query[:30])
                    return

                payload = resp.json()
                items = payload.get("items") if isinstance(payload, dict) else None
                if not isinstance(items, list):
                    cache_writes.append((cache_key, query, None, None, 0))
                    return

                evaluated = evaluate_naver_items(deal.primary.title, query, items)
                if evaluated is None:
                    cache_writes.append((cache_key, query, None, None, 0))
                    return

                ref_krw, low_krw, cnt = evaluated
                cache_writes.append((cache_key, query, ref_krw, low_krw, cnt))
                for group_deal, group_query in seen_cache_keys.get(cache_key, []):
                    if group_deal.primary.price_krw:
                        bl = build_naver_baseline(
                            group_deal.primary.price_krw,
                            ref_krw,
                            low_krw,
                            cnt,
                            group_query,
                        )
                        if bl is not None:
                            baselines[group_deal.canonical_key] = bl
            except Exception as exc:
                safe_msg = redact_secrets(str(exc), secrets_tuple)
                logger.warning(
                    "Naver Shopping lookup failed (%s): %s",
                    type(exc).__name__,
                    safe_msg,
                )

    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        await asyncio.gather(*(_query_one(client, d, q, ck) for d, q, ck in to_query))

    if storage is not None and not read_only_cache:
        for ck, q, ref_krw, low_krw, cnt in cache_writes:
            storage.save_cached_market_price(ck, q, ref_krw, low_krw, cnt, now=now_dt)

    return baselines
