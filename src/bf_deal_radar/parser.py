"""Text/HTML parsers for prices, discounts, ASINs, coupon codes, and retailers."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# Match Amazon ASIN in URLs (10-char alphanumeric, including amazon.com and link.amazon shorteners)
_ASIN_RE = re.compile(
    r"(?:amazon\.[a-z.]{2,6}/(?:[^\"'<>\s]*/)?(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/|link\.amazon/)"
    r"([A-Z0-9]{10})(?:[/?&#\"'\s]|$)",
    re.IGNORECASE,
)

# Slickdeals embedded coupon HTML span
_SD_COUPON_HTML_RE = re.compile(
    r"data-role=['\"]couponCode['\"].*?<strong>([A-Z0-9_-]{3,24})</strong>",
    re.IGNORECASE | re.DOTALL,
)

# Generic coupon patterns in deal text
_COUPON_TEXT_RE = re.compile(
    r"(?:w/\s*code|promo\s+code|coupon\s+code|use\s+code|apply\s+code|with\s+code)\s*[:*\"']*\s*"
    r"([A-Z0-9][A-Z0-9_-]{3,20})\b",
    re.IGNORECASE,
)

_COUPON_FALSE_POSITIVES = {
    "FREE",
    "SHIPPING",
    "PRIME",
    "ONLINE",
    "CHECKOUT",
    "CART",
    "DEAL",
    "SAVE",
    "DISCOUNT",
    "COUPON",
    "PROMO",
    "CODE",
    "TODAY",
    "ONLY",
    "OFFER",
    "AMAZON",
}

# Match concrete single-item percentage discounts (explicitly ignore marketing "up to XX% off")
_PCT_OFF_RE = re.compile(
    r"(?<!up\s)(?<!to\s)(?:save\s+)?(\d{1,2})\s*%\s*(?:off|discount|savings?)",
    re.IGNORECASE,
)
_UP_TO_PCT_RE = re.compile(r"\bup\s+to\s+\d{1,2}\s*%\s*off\b", re.IGNORECASE)

_SAVINGS_USD_RE = re.compile(
    r"(?:save\s+\$|cut\s+\$|rare\s+\$|extra\s+\$|take\s+\$|\$)(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)\s*"
    r"(?:off|discount|savings?|price\s+drop)",
    re.IGNORECASE,
)
_SAVE_PREFIX_USD_RE = re.compile(
    r"(?:save|saves|saving|discount\s+of|cut\s+by|drops?\s+by)\s+\$(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)

_ORIGINAL_PRICE_RE = re.compile(
    r"(?:was|reg\.?|regular(?:ly)?|normally|list(?:\s+price)?|msrp|orig\.?|down\s+from|retail(?:s\s+for)?)\s*"
    r"\$(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

# Prices preceded by shipping/minimum spend/savings qualifiers should not be treated as the item price
_NON_ITEM_PRICE_PREFIX_RE = re.compile(
    r"(?:w/|with|orders?(?:\s+over|\s+of)?|spend|shipping\s+(?:over|on|w/)|free\s+shipping\s+w/|"
    r"save|saves|saving|off|credit|rebate|gift\s+card|cash\s*back|coupon\s+of|under|over|plus|"
    r"up\s+to|extra|rare|taking|takes|cut|cuts|drops?\s+by|shave|shaves|worth)\s*$",
    re.IGNORECASE,
)
_NON_ITEM_PRICE_SUFFIX_RE = re.compile(
    r"^\s*(?:off|discount|savings?|price\s+drop|gift\s*card|credit|cash\s*back|back|reward|"
    r"minimum|min\b|\+\s*order|shipping|less\b|below\b|under\b)",
    re.IGNORECASE,
)

_DOLLAR_PRICE_RE = re.compile(r"\$(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)")

_KNOWN_RETAILERS: list[tuple[str, str]] = [
    ("amazon", "Amazon"),
    ("best buy", "Best Buy"),
    ("bestbuy", "Best Buy"),
    ("walmart", "Walmart"),
    ("target", "Target"),
    ("b&h", "B&H Photo"),
    ("bhphotovideo", "B&H Photo"),
    ("newegg", "Newegg"),
    ("ebay", "eBay"),
    ("apple.com", "Apple"),
    ("micro center", "Micro Center"),
    ("microcenter", "Micro Center"),
    ("adorama", "Adorama"),
    ("woot", "Woot"),
    ("costco", "Costco"),
    ("dell", "Dell"),
    ("lenovo", "Lenovo"),
    ("hp.com", "HP"),
    ("samsung.com", "Samsung"),
    ("sony.com", "Sony"),
    ("rei.com", "REI"),
    ("nike", "Nike"),
    ("adidas", "Adidas"),
    ("gamestop", "GameStop"),
    ("homedepot", "Home Depot"),
    ("home depot", "Home Depot"),
    ("lowe's", "Lowe's"),
    ("lowes", "Lowe's"),
    ("monoprice", "Monoprice"),
    ("crutchfield", "Crutchfield"),
    ("walgreens", "Walgreens"),
    ("staples", "Staples"),
    ("office depot", "Office Depot"),
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

_TITLE_STOPWORDS = {
    "the",
    "a",
    "an",
    "for",
    "with",
    "w",
    "and",
    "or",
    "of",
    "at",
    "to",
    "in",
    "on",
    "is",
    "now",
    "down",
    "from",
    "by",
    "via",
    "free",
    "shipping",
    "s",
    "h",
    "sh",
    "prime",
    "members",
    "member",
    "deal",
    "deals",
    "sale",
    "black",
    "friday",
    "thanksgiving",
    "cyber",
    "monday",
    "off",
    "save",
    "all",
    "time",
    "low",
    "best",
    "price",
    "more",
    "code",
    "coupon",
    "plus",
    "pack",
}


@dataclass(slots=True)
class PriceInfo:
    price_usd: float | None = None
    original_price_usd: float | None = None
    discount_pct: float | None = None
    savings_usd: float | None = None


def strip_html(raw: str) -> str:
    """Convert HTML snippet to clean plain text."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _to_float(num_str: str) -> float:
    return float(num_str.replace(",", ""))


def extract_asin(*texts: str) -> str | None:
    """Extract an Amazon ASIN from URLs or HTML/plain text."""
    for text in texts:
        if not text:
            continue
        for candidate in (text, unquote(text)):
            match = _ASIN_RE.search(candidate)
            if match:
                asin = match.group(1).upper()
                if len(asin) == 10:
                    return asin
    return None


def extract_coupon_code(raw_html: str = "", plain_text: str = "") -> str | None:
    """Extract promotional coupon code from deal HTML or plain text."""
    if raw_html:
        m = _SD_COUPON_HTML_RE.search(raw_html)
        if m:
            code = m.group(1).strip().upper()
            if code not in _COUPON_FALSE_POSITIVES:
                return code

    if plain_text:
        for m in _COUPON_TEXT_RE.finditer(plain_text):
            raw_code = m.group(1).strip()
            code = raw_code.upper()
            # Coupon codes usually have uppercase/digits or length >= 5
            if code in _COUPON_FALSE_POSITIVES:
                continue
            if any(c.isdigit() for c in code) or raw_code.isupper():
                return code
    return None


def _extract_item_price(text: str) -> float | None:
    """Extract the most likely product sale price from text, ignoring '$X off' or 'w/ $35'."""
    if not text:
        return None

    for match in _DOLLAR_PRICE_RE.finditer(text):
        start, end = match.span()
        before = text[max(0, start - 28) : start]
        after = text[end : min(len(text), end + 24)]
        if _NON_ITEM_PRICE_PREFIX_RE.search(before):
            continue
        if _NON_ITEM_PRICE_SUFFIX_RE.search(after):
            continue
        # Avoid "was $399" / "reg $399" being picked as sale price
        if re.search(
            r"(?:was|reg\.?|normally|list(?:\s+price)?|msrp|orig\.?|from)\s*$",
            before,
            re.IGNORECASE,
        ):
            continue
        val = _to_float(match.group(1))
        if 0.5 <= val <= 25000:
            return val
    return None


def _extract_pct_from(text: str) -> float | None:
    if not text:
        return None
    cleaned = _UP_TO_PCT_RE.sub(" ", text)
    pct_matches = [int(m.group(1)) for m in _PCT_OFF_RE.finditer(cleaned)]
    # Ignore tiny 5% credit-card promos (e.g., Target RedCard 5% off) as genuine discounts
    valid_pcts = [p for p in pct_matches if 10 <= p <= 95]
    if valid_pcts:
        return float(max(valid_pcts))
    if re.search(r"\bhalf\s+off\b|\b50%\s+off\b|around\s+double\s+elsewhere", cleaned, re.IGNORECASE):
        return 50.0
    return None


def _extract_savings_from(text: str) -> float | None:
    if not text:
        return None
    cleaned = re.sub(
        r"\bup\s+to\s+\$\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?\s*off\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    savings_vals: list[float] = []
    for regex in (_SAVINGS_USD_RE, _SAVE_PREFIX_USD_RE):
        for m in regex.finditer(cleaned):
            val = _to_float(m.group(1))
            if 1.0 <= val <= 10000.0:
                savings_vals.append(val)
    return max(savings_vals) if savings_vals else None


def extract_price_info(
    title: str,
    description: str = "",
    explicit_price: float | None = None,
) -> PriceInfo:
    """Extract current sale price, original price, dollar savings, and discount percentage.

    Prefers title-level discount numbers over description text so blog intro sentences
    referencing other deals don't corrupt single-item discount calculations.
    """
    info = PriceInfo(price_usd=explicit_price)

    if info.price_usd is None:
        info.price_usd = _extract_item_price(title)
    if info.price_usd is None:
        info.price_usd = _extract_item_price(description)

    # Original / list price (prefer title first, then description)
    orig_match = _ORIGINAL_PRICE_RE.search(title) or _ORIGINAL_PRICE_RE.search(description)
    if orig_match:
        orig_val = _to_float(orig_match.group(1))
        if info.price_usd is None or orig_val > info.price_usd:
            info.original_price_usd = orig_val

    # Percentage off (prefer title, fallback to description)
    info.discount_pct = _extract_pct_from(title)
    if info.discount_pct is None:
        info.discount_pct = _extract_pct_from(description)

    # Dollar savings ($X off / save $X) — prefer title; sanity-check description-only savings
    title_savings = _extract_savings_from(title)
    if title_savings is not None:
        info.savings_usd = title_savings
    else:
        desc_savings = _extract_savings_from(description)
        if desc_savings is not None:
            # Guard against blog intro bleed (e.g., "$400 off MacBook" bleeding onto a "$379 iPad")
            if info.price_usd is None or desc_savings <= info.price_usd * 2.0:
                info.savings_usd = desc_savings

    # Derive missing fields consistently
    if info.price_usd is not None and info.original_price_usd is not None:
        if info.original_price_usd > info.price_usd:
            calc_savings = round(info.original_price_usd - info.price_usd, 2)
            calc_pct = round((calc_savings / info.original_price_usd) * 100.0, 1)
            if info.savings_usd is None:
                info.savings_usd = calc_savings
            if info.discount_pct is None and calc_pct >= 10.0:
                info.discount_pct = calc_pct
    elif info.price_usd is not None and info.savings_usd is not None:
        implied_orig = round(info.price_usd + info.savings_usd, 2)
        if implied_orig > info.price_usd:
            info.original_price_usd = implied_orig
            calc_pct = round((info.savings_usd / implied_orig) * 100.0, 1)
            if info.discount_pct is None and calc_pct >= 10.0:
                info.discount_pct = calc_pct
    elif info.price_usd is not None and info.discount_pct is not None and 10.0 <= info.discount_pct < 90.0:
        implied_orig = round(info.price_usd / (1.0 - info.discount_pct / 100.0), 2)
        if implied_orig > info.price_usd:
            info.original_price_usd = implied_orig
            if info.savings_usd is None:
                info.savings_usd = round(implied_orig - info.price_usd, 2)

    return info


def extract_retailer(
    title: str,
    description: str = "",
    url: str = "",
    explicit: str | None = None,
) -> str | None:
    """Identify the merchant/retailer for an overseas deal."""
    if explicit and explicit.strip():
        return explicit.strip()

    host = urlparse(url).netloc.lower() if url else ""
    haystack = f"{host} {title} {description[:300]}".lower()
    for needle, label in _KNOWN_RETAILERS:
        if needle in haystack:
            return label
    return None


def normalize_title_tokens(title: str) -> set[str]:
    """Extract meaningful normalized tokens from a deal title for cross-source clustering."""
    cleaned = html.unescape(title).lower()
    # Strip prices and percentages
    cleaned = re.sub(r"\$\d+(?:,\d{3})*(?:\.\d{1,2})?", " ", cleaned)
    cleaned = re.sub(r"\d+%", " ", cleaned)
    tokens = re.findall(r"[a-z0-9]+", cleaned)
    return {t for t in tokens if len(t) >= 2 and t not in _TITLE_STOPWORDS}


_CLEAN_QUERY_PREFIX_RE = re.compile(
    r"^(?:prime\s+members?:|costco\s+members?:|certified\s+open\s+box:|"
    r"certified\s+refurb(?:ished)?:|whoa!|the\s+best\s+black\s+friday\s+deal\s+is\s+already\s+live:|"
    r"land\s+|all-new\s+|the\s+)",
    re.IGNORECASE,
)
_CLEAN_QUERY_SUFFIX_RE = re.compile(
    r"(?:\s+(?:for|at|down\s+(?:at|to)|now\s+at|from)\s+\$|\s+\+\s*(?:free\s+)?s&?h|\s+\+\s*free\s+shipping|"
    r"\s+@\s*\w+|\s+w/\s*code\b|\s+for\s+black\s+friday|\s+for\s+thanksgiving|"
    r"\s*\((?:reg\.|was|save|up\s+to|giant|same\s+price)[^)]*\)).*$",
    re.IGNORECASE,
)


def clean_product_query(title: str) -> str:
    """Produce a concise product search query stripped of prices/shipping noise for CamelCamelCamel."""
    q = html.unescape(title).strip()
    q = _CLEAN_QUERY_PREFIX_RE.sub("", q).strip()
    q = _CLEAN_QUERY_SUFFIX_RE.sub("", q).strip()
    q = re.sub(r"\$\d+(?:,\d{3})*(?:\.\d{1,2})?", "", q)
    q = _WHITESPACE_RE.sub(" ", q).strip(" -:,|!")
    return (q or title)[:68]
