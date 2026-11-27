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
_UP_TO_PCT_RE = re.compile(
    r"\bup\s+to\s+(?:nearly\s+|almost\s+)?\d{1,2}\s*%\s*off\b",
    re.IGNORECASE,
)

# Ignore vague ("up to $200 off", "save up to $300") and capped ("maximum discount of $60") promos
_VAGUE_OR_CAPPED_SAVINGS_RE = re.compile(
    r"(?:\bup\s+to\s+(?:nearly\s+|almost\s+)?\$\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?"
    r"(?:\s*(?:[-–—]?\s*(?:off\b|discount|savings?|price\s+(?:drop|cut|reduction|slash)|markdown)|"
    r"in\s+(?:(?:total|instant|direct|combined|additional|extra|bonus|black\s+friday|holiday|early)\s+){0,2}"
    r"(?:savings?|discounts?)))?"
    r"|\b(?:save|saves|saving)\s+up\s+to\s+\$\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?\b"
    r"|\bmax(?:imum)?\s+(?:discount|savings?)\s+of\s+\$\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?\b)",
    re.IGNORECASE,
)

_SAVINGS_USD_RE = re.compile(
    r"(?:save\s+\$|cut\s+\$|rare\s+\$|extra\s+\$|take\s+\$|\$)"
    r"(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)\s*"
    r"(?:[-–—]?\s*(?:off\b|discount|savings?|price\s+(?:drop|cut|reduction|slash|markdown)|markdown)|"
    r"(?:straight|right|directly)\s+off\b|"
    r"(?:instant|total)\s+(?:savings?|discount)|"
    r"in\s+(?:(?:total|instant|direct|combined|additional|extra|bonus|black\s+friday|holiday|early)\s+){0,2}"
    r"(?:savings?|discounts?))",
    re.IGNORECASE,
)
_SAVE_PREFIX_USD_RE = re.compile(
    r"(?:(?:save|saves|saving)\s+(?:(?:you|buyers?|shoppers?|customers?|nearly|almost|over|around|about|"
    r"an?|extra|additional|solid|rare|massive|huge|whopping|flat|instant|total)\s+){0,3}|"
    r"(?:discount|savings?|price\s+drop|price\s+cut|markdown|reduction)\s+of\s+|"
    r"(?:cut|cuts|cutting|slashed?|reduced?|discounted|drops?|dropped|dropping|down|lowered)\s+by\s+"
    r"(?:(?:an?|extra|additional)\s+){0,2})"
    r"\$(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)

_ORIGINAL_PRICE_RE = re.compile(
    r"(?:was|reg\.?|regular(?:ly)?(?:\s+costs?|\s+priced(?:\s+at)?)?|"
    r"normally(?:\s+costs?|\s+sells?\s+for)?|usually(?:\s+costs?|\s+sells?\s+for)?|"
    r"typically(?:\s+costs?|\s+sells?\s+for)?|list(?:\s+price)?(?:\s+of)?|msrp(?:\s+of)?|"
    r"orig\.?|down\s+from|retail(?:s\s+(?:at|for))?)\s*"
    r"\$(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

# Prices preceded by shipping/minimum spend/savings qualifiers should not be treated as the item price
_NON_ITEM_PRICE_PREFIX_RE = re.compile(
    r"(?:w/|with|orders?(?:\s+over|\s+of)?|spend|shipping\s+(?:over|on|w/)|free\s+shipping\s+w/|"
    r"save|saves|saving|off|credit|rebate|gift\s+card|cash\s*back|"
    r"(?:coupon|discount|savings?|price\s+drop|price\s+cut|markdown|reduction|rebate|credit|bonus)\s+of|"
    r"under|over|plus|up\s+to|extra|rare|taking|takes|cut|cuts|cutting|slash|slashes|slashing|"
    r"knock|knocks|knocking|shave|shaves|shaving|worth|"
    r"(?:cut|slashed|reduced|discounted|drops?|dropped|dropping|down|lowered)\s+by|"
    r"(?:save|saves|saving|knock|knocks|knocking|shave|shaves|shaving|take|takes|taking)\s+"
    r"(?:(?:you|buyers?|shoppers?|customers?|up\s+to|nearly|almost|over|around|about|roughly|"
    r"an?|extra|additional|solid|rare|massive|huge|whopping|flat|direct|instant|total)\s+){1,3})\s*$",
    re.IGNORECASE,
)
_NON_ITEM_PRICE_SUFFIX_RE = re.compile(
    r"^\s*(?:[-–—]?\s*(?:off\b|discount|savings?|price\s+(?:drop|cut|reduction|slash|markdown)|"
    r"markdown|drop\b|cut\b|reduction\b|slash\b)|"
    r"(?:straight|right|directly)\s+off\b|"
    r"(?:in\s+)?(?:(?:total|instant|direct|combined|additional|extra|bonus|black\s+friday|holiday|early)\s+){0,2}"
    r"(?:savings?|discounts?|credit|rewards?|cash\s*back|rebates?)|"
    r"(?:store|amazon|best\s+buy|target|walmart|apple|samsung|google|promo(?:tional)?|"
    r"statement|account|bill|trade[\s-]in|instant|bonus)\s+"
    r"(?:e?-?gift\s*card|gift\s*card|credit|voucher|coupon|rebate|bonus|reward)|"
    r"e?-?gift\s*card|gift\s*card|credit|rebate|cash\s*back|"
    r"back\b(?!\s+(?:in\s+stock|at\b|to\b|on\s+sale|down\b|for\b))|"
    r"reward|voucher|coupon|promo\b|trade[\s-]in|value\b|"
    r"minimum|min\b|\+\s*order|shipping|less\b|below\b|under\b|cheaper\b|lower\b|more\b)",
    re.IGNORECASE,
)

# Explicit sale-price cues required before accepting a description price equal to title savings
_EXPLICIT_SALE_PRICE_PREFIX_RE = re.compile(
    r"(?:down\s+to|dropped\s+to|drops?\s+to|discounted\s+to|reduced\s+to|slashed\s+to|"
    r"cut\s+to|marked\s+down\s+to|priced\s+at|on\s+sale\s+for|buy\s+(?:it\s+)?for|"
    r"now\s+(?:just\s+|only\s+)?|down\s+at|for\s+(?:just|only))\s*$",
    re.IGNORECASE,
)
_EXPLICIT_SALE_PRICE_SUFFIX_RE = re.compile(
    r"^\s*(?:prime\s+)?shipped\b",
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


def _excluded_price_spans(text: str) -> list[tuple[int, int]]:
    """Return character spans already classified as savings, capped promos, or original prices."""
    spans: list[tuple[int, int]] = []
    for regex in (_ORIGINAL_PRICE_RE, _SAVINGS_USD_RE, _SAVE_PREFIX_USD_RE, _VAGUE_OR_CAPPED_SAVINGS_RE):
        for match in regex.finditer(text):
            spans.append(match.span())
    return spans


def _extract_item_price(text: str, *, suspicious_savings: float | None = None) -> float | None:
    """Extract the most likely product sale price from text, ignoring '$X off' or 'w/ $35'.

    When `suspicious_savings` is provided (e.g. a title-level '$300 off' with no title price),
    any description dollar amount matching that exact savings value requires an explicit
    sale-price cue (e.g. 'down to $300', '$300 shipped') so re-stated savings text cannot
    masquerade as the item price and fabricate a 50% discount.
    """
    if not text:
        return None

    excluded_spans = _excluded_price_spans(text)

    for match in _DOLLAR_PRICE_RE.finditer(text):
        start, end = match.span()
        if any(s <= start < e for s, e in excluded_spans):
            continue
        before = text[max(0, start - 40) : start]
        after = text[end : min(len(text), end + 36)]
        if _NON_ITEM_PRICE_PREFIX_RE.search(before):
            continue
        if _NON_ITEM_PRICE_SUFFIX_RE.search(after):
            continue
        # Avoid "was $399" / "reg $399" / "regularly $3,399" / "from $48" being picked as sale price
        if re.search(
            r"(?:was|reg\.?|regular(?:ly)?|normally|usually|list(?:\s+price)?|msrp|orig\.?|from)\s*$",
            before,
            re.IGNORECASE,
        ):
            continue
        val = _to_float(match.group(1))
        if not (0.5 <= val <= 25000):
            continue
        if suspicious_savings is not None and abs(val - suspicious_savings) < 0.01:
            if not (
                _EXPLICIT_SALE_PRICE_PREFIX_RE.search(before)
                or _EXPLICIT_SALE_PRICE_SUFFIX_RE.search(after)
            ):
                continue
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
    cleaned = _VAGUE_OR_CAPPED_SAVINGS_RE.sub(" ", text)
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
    title_savings = _extract_savings_from(title)

    price_from_description = False
    if info.price_usd is None:
        info.price_usd = _extract_item_price(title)
    if info.price_usd is None:
        desc_price = _extract_item_price(description, suspicious_savings=title_savings)
        if desc_price is not None:
            info.price_usd = desc_price
            price_from_description = True

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
    if title_savings is not None:
        info.savings_usd = title_savings
    else:
        desc_savings = _extract_savings_from(description)
        if desc_savings is not None:
            # Guard against blog intro bleed (e.g., "$400 off MacBook" bleeding onto a "$379 iPad")
            if info.price_usd is None or desc_savings <= info.price_usd * 2.0:
                info.savings_usd = desc_savings

    # Symmetric bleed guard: when title only states "$X off" and description yields a tiny unrelated
    # price (e.g., "$9 Polishing Cloth" bleeding onto "$250 off MacBook"), do not fabricate 96% OFF.
    if (
        price_from_description
        and info.price_usd is not None
        and title_savings is not None
        and info.original_price_usd is None
        and info.discount_pct is None
        and title_savings > info.price_usd * 2.0
    ):
        info.price_usd = None

    # Discard mismatched unit original prices (e.g., "$14.99/mo" paired with "$50 off 6 months")
    if (
        info.price_usd is None
        and info.original_price_usd is not None
        and info.savings_usd is not None
        and info.savings_usd >= info.original_price_usd
    ):
        info.original_price_usd = None

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
    """Extract meaningful normalized tokens (English + Hangul) for cross-source clustering."""
    cleaned = html.unescape(title).lower()
    # Strip USD/KRW prices, percentages, and bracketed retailer tags
    cleaned = re.sub(r"\[[^\]]{1,20}\]", " ", cleaned)
    cleaned = re.sub(r"\$\d+(?:,\d{3})*(?:\.\d{1,2})?", " ", cleaned)
    cleaned = re.sub(r"\d{1,3}(?:,\d{3})+\s*원?", " ", cleaned)
    cleaned = re.sub(r"\d+%", " ", cleaned)
    tokens = re.findall(r"[a-z0-9가-힣]+", cleaned)
    kr_stop = {"무료", "무배", "무료배송", "와우", "할인", "특가", "최저가", "역대가", "쿠폰", "배송비", "별도", "네멤무료", "와우무료"}
    return {t for t in tokens if len(t) >= 2 and t not in _TITLE_STOPWORDS and t not in kr_stop}


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


# --- Korean domestic hot deal parsers ---

_KR_RETAILERS: list[tuple[str, str]] = [
    ("쿠팡와우", "쿠팡와우"),
    ("쿠팡로켓", "쿠팡"),
    ("쿠팡", "쿠팡"),
    ("네이버페이", "네이버페이"),
    ("네이버", "네이버"),
    ("스마트스토어", "네이버"),
    ("지마켓", "G마켓"),
    ("g마켓", "G마켓"),
    ("gmarket", "G마켓"),
    ("옥션", "옥션"),
    ("11번가", "11번가"),
    ("롯데온", "롯데온"),
    ("lotteon", "롯데온"),
    ("ssg", "SSG"),
    ("쓱닷컴", "SSG"),
    ("신세계몰", "SSG"),
    ("하이마트", "하이마트"),
    ("카카오톡딜", "카카오톡딜"),
    ("카카오메이커스", "카카오메이커스"),
    ("톡딜", "카카오톡딜"),
    ("카카오", "카카오"),
    ("토스쇼핑", "토스쇼핑"),
    ("토스", "토스쇼핑"),
    ("cj온스타일", "CJ온스타일"),
    ("지에스샵", "GS샵"),
    ("gs샵", "GS샵"),
    ("gsshop", "GS샵"),
    ("오늘의집", "오늘의집"),
    ("무신사", "무신사"),
    ("29cm", "29CM"),
    ("w컨셉", "W컨셉"),
    ("지그재그", "지그재그"),
    ("에이블리", "에이블리"),
    ("ssf샵", "SSF샵"),
    ("크림", "KREAM"),
    ("올리브영", "올리브영"),
    ("홈플러스", "홈플러스"),
    ("이마트", "이마트"),
    ("코스트코", "코스트코"),
    ("농협몰", "농협몰"),
    ("우체국몰", "우체국몰"),
    ("알리익스프레스", "알리익스프레스"),
    ("알리", "알리익스프레스"),
    ("테무", "테무"),
    ("temu", "테무"),
    ("아이허브", "아이허브"),
    ("iherb", "아이허브"),
    ("이유몰", "이유몰"),
    ("스팀", "스팀"),
    ("에픽게임즈", "에픽게임즈"),
    ("플레이스토어", "플레이스토어"),
    ("컴퓨존", "컴퓨존"),
    ("다나와", "다나와"),
    ("컬리", "마켓컬리"),
    ("배민", "배달의민족"),
    ("나이키", "나이키"),
    ("아디다스", "아디다스"),
    ("유니클로", "유니클로"),
]


def normalize_kr_retailer(name: str | None) -> str | None:
    """Normalize an explicit Korean shopping mall name (e.g. '지마켓' -> 'G마켓')."""
    if not name:
        return None
    cleaned = html.unescape(name).strip()
    if not cleaned:
        return None
    low = cleaned.lower()
    for needle, label in _KR_RETAILERS:
        if needle in low:
            return label
    return cleaned[:20]

_KR_WON_RE = re.compile(r"(?:₩\s*|(?<!\d))(\d{1,3}(?:,\d{3})+|\d{3,7})\s*원|₩\s*(\d{1,3}(?:,\d{3})+|\d{3,7})")
_KR_MAN_WON_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{1,2})?)\s*만\s*(?:(\d{1,4})\s*천?)?\s*원?")
_KR_SLASH_PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+)\s*/\s*(?:무료|무배|와우|네멤|배송|유배|\d)")
_KR_NON_ITEM_PREFIX_RE = re.compile(
    r"(?:배송비|택배비|적립|포인트|캐시백|쿠폰|추가\s*할인|즉시\s*할인|최대)\s*$"
)
_KR_NON_ITEM_SUFFIX_RE = re.compile(
    r"^\s*(?:할인|쿠폰|적립|캐시백|페이백|포인트|추가\s*할인|즉시\s*할인|청구\s*할인)"
)
_KR_SOLD_OUT_RE = re.compile(
    r"(?:\[\s*(?:종료|품절|마감|끌올종료)\s*\]|\(\s*(?:종료|품절|마감)\s*\)|(?:품절|종료|마감)됨\b|^품절\b)",
    re.IGNORECASE,
)
_KR_PCT_RE = re.compile(r"(\d{1,2})\s*%\s*(?:할인|세일|off|특가|적립|쿠폰|↓)?", re.IGNORECASE)
_KR_INGREDIENT_PCT_PREFIX_RE = re.compile(
    r"(?:연육|함량|함유|면|코튼|울|캐시미어|폴리|나일론|알코올|도수|카페인|지방|단백질|"
    r"천연|국산|국내산|원액|과즙|착즙|순면|유기농|유크림|배터리|충전|효율|성분|원유|우유|카카오|다크|초콜릿|과채|농축)\s*$",
    re.IGNORECASE,
)


def extract_krw_price(title: str, description: str = "") -> int | None:
    """Extract Korean Won price (e.g., 18,930원, ₩25,990, 43,610원/무배, 12.9만원) from deal title."""
    for text in (title, description[:200]):
        if not text:
            continue
        # 1. Explicit "NN,NNN원" or "₩NN,NNN" (including 100~999원 so min_price_krw filter catches tiny promos)
        for m in _KR_WON_RE.finditer(text):
            before = text[max(0, m.start() - 14) : m.start()]
            after = text[m.end() : min(len(text), m.end() + 14)]
            if _KR_NON_ITEM_PREFIX_RE.search(before):
                continue
            if _KR_NON_ITEM_SUFFIX_RE.search(after):
                continue
            digits = m.group(1) or m.group(2)
            val = int(digits.replace(",", ""))
            if 100 <= val <= 50_000_000:
                return val

        # 2. "NN,NNN/무배" format without explicit 원
        m_slash = _KR_SLASH_PRICE_RE.search(text)
        if m_slash:
            val = int(m_slash.group(1).replace(",", ""))
            if 500 <= val <= 50_000_000:
                return val

        # 3. "12.9만원" or "3만5천원"
        m_man = _KR_MAN_WON_RE.search(text)
        if m_man:
            before = text[max(0, m_man.start() - 14) : m_man.start()]
            if not _KR_NON_ITEM_PREFIX_RE.search(before):
                base = float(m_man.group(1)) * 10_000
                extra = int(m_man.group(2)) * 1_000 if m_man.group(2) else 0
                val = int(round(base + extra))
                if 1000 <= val <= 50_000_000:
                    return val
    return None


def extract_kr_discount_pct(title: str, description: str = "") -> float | None:
    """Extract explicit percentage discount from Korean deal title/description (ignoring <10% and ingredient %)."""
    text = f"{title} {description[:200]}"
    # Ignore "최대 NN%" marketing banners
    text = re.sub(r"최대\s*\d{1,2}\s*%", " ", text)
    valid: list[int] = []
    for m in _KR_PCT_RE.finditer(text):
        before = text[max(0, m.start() - 12) : m.start()]
        if _KR_INGREDIENT_PCT_PREFIX_RE.search(before):
            continue
        pct = int(m.group(1))
        if 10 <= pct <= 95:
            valid.append(pct)
    if valid:
        return float(max(valid))
    if "반값" in text:
        return 50.0
    return None


def extract_kr_retailer(title: str) -> str | None:
    """Extract Korean shopping mall name from bracket prefix '[쿠팡]' or title."""
    bracket = re.search(r"^\s*[\[\(]([^\]\)]{1,18})[\]\)]", title)
    if bracket:
        inside = bracket.group(1).strip()
        inside_low = inside.lower()
        for needle, label in _KR_RETAILERS:
            if needle in inside_low:
                return label
        if len(inside) <= 12:
            return inside

    low = title.lower()
    for needle, label in _KR_RETAILERS:
        if needle in low:
            return label
    return None


def extract_kr_shipping(title: str) -> str | None:
    """Detect free shipping vs paid shipping from Korean hot deal title."""
    if re.search(r"(?:무료배송|무배|와우무료|네멤무료|로켓배송|/\s*무료\b|\(\s*무료\s*\))", title):
        return "무료배송"
    if re.search(r"(?:배송비\s*별도|유배|배송비\s*\d)", title):
        return "배송비 별도"
    return None


def is_kr_sold_out(title: str) -> bool:
    """Return True if a Korean deal title explicitly indicates sold-out/expired."""
    return bool(_KR_SOLD_OUT_RE.search(title))


def clean_kr_product_query(title: str) -> str:
    """Strip mall tags, prices, and shipping suffixes from Korean title for Naver/Danawa search."""
    q = html.unescape(title).strip()
    # Remove leading [몰명] or (몰명)
    q = re.sub(r"^\s*(?:[\[\(][^\]\)]{1,20}[\]\)]\s*)+", "", q)
    # Remove parenthesized price/shipping suffixes like (18,930원/무료)
    q = re.sub(r"\([^)]*(?:\d{1,3}(?:,\d{3})+원|무료|무배|배송)[^)]*\)", " ", q)
    # Remove inline prices & shipping labels
    q = re.sub(r"\d{1,3}(?:,\d{3})+\s*원?(?:\s*/\s*\S+)?", " ", q)
    q = re.sub(r"\d{1,2}%\s*(?:할인|세일|특가)?", " ", q)
    q = re.sub(r"(?:무료배송|무배|와우무료|네멤무료|배송비\s*별도|역대가|최저가|초특가|할인|특가)", " ", q)
    q = _WHITESPACE_RE.sub(" ", q).strip(" -:,|/()")
    return (q or title)[:55]


_KR_PROMO_RANGE_TITLE_RE = re.compile(
    r"(?:모음전|외\s*다수|외\s*\d+종|外\s*\d+종|단\s*하루|단하루|골라담기|BEST상품|특가전|전품목|\d[\d,]*원\s*~)"
)

_KR_QUERY_NOISE_RE = re.compile(
    r"(?:체감가|카드할인|카드\s*할인|쿠폰가|쿠폰적용|쿠폰\s*적용|중복쿠폰|즉시할인|즉시\s*할인|"
    r"네이버페이|네멤|와우할인|와우\s*할인|로켓배송|로켓직구|가격오류|가격\s*오류|오표기|가격실수|"
    r"긴급|대란|반값|오늘만|타임특가|단독|공식정품|공식\s*정품|정품|국내정발|국내배송|빠른배송|당일발송|"
    r"사은품|증정|적립|최대|선착순|한정수량|마감임박|무료|유배|직배|신한|삼성카드|국민카드|현대카드|토스페이)",
    re.IGNORECASE,
)

_KR_BRAND_SYNONYMS: dict[str, str] = {
    "삼성전자": "삼성",
    "samsung": "삼성",
    "lg전자": "lg",
    "엘지": "lg",
    "엘지전자": "lg",
    "애플": "apple",
    "소니": "sony",
    "다이슨": "dyson",
    "로보락": "roborock",
    "닌텐도": "nintendo",
    "보스": "bose",
    "로지텍": "logitech",
    "필립스": "philips",
    "에어팟": "airpods",
    "아이패드": "ipad",
    "맥북": "macbook",
    "아이폰": "iphone",
    "갤럭시": "galaxy",
    "나이키": "nike",
    "아디다스": "adidas",
    "아크테릭스": "arcteryx",
    "파타고니아": "patagonia",
    "레고": "lego",
    "키크론": "keychron",
    "앤커": "anker",
    "유그린": "ugreen",
    "샤오미": "xiaomi",
    "브레빌": "breville",
    "드롱기": "delonghi",
    "네스프레소": "nespresso",
    "가민": "garmin",
}

_KR_ACCESSORY_RE = re.compile(
    r"(?:케이스|보호필름|강화유리|필름|스트랩|워치밴드|밴드|이어패드|이어팁|케이블|거치대|스탠드|"
    r"파우치|커버|충전기|어댑터|젠더|허브|독|리필|교체용|교체|부품|칫솔모|브러쉬|브러시|필터|배터리|"
    r"스킨|스티커|키캡|마우스패드|액세서리|악세사리|"
    r"\b(?:case|cover|strap|band|cable|charger|adapter|film|earpad|eartip|refill|filter|pouch|stand|mount)\b)",
    re.IGNORECASE,
)

_SPEC_STORAGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*(tb|gb)(?![a-z가-힣])", re.IGNORECASE)
_SPEC_WEIGHT_RE = re.compile(r"(?<!\d)(\d{1,4}(?:\.\d{1,2})?)\s*(kg|g)(?![a-z가-힣])", re.IGNORECASE)
_SPEC_VOLUME_RE = re.compile(r"(?<!\d)(\d{1,4}(?:\.\d{1,2})?)\s*(ml|l|리터)(?![a-z가-힣])", re.IGNORECASE)
_SPEC_COUNT_RE = re.compile(
    r"(?<!\d)(\d{1,4})\s*(?:개입|개|입|캔|병|팩|봉지|봉|매|정|캡슐|포|구|박스|box|세트|set|p|ea|롤|장|켤레|족|갑)(?![a-z가-힣])",
    re.IGNORECASE,
)
_SPEC_MULT_COUNT_RE = re.compile(
    r"[x×*]\s*(\d{1,3})(?!\s*(?:gb|tb|mb|ml|l|g|kg|mm|cm|hz|w|인치))(?![a-z0-9])",
    re.IGNORECASE,
)
_SPEC_PLUS_PACK_RE = re.compile(r"(?<!\d)(\d\+\d)(?!\d)")
_SPEC_SCREEN_RE = re.compile(r"(?<!\d)(\d{2}(?:\.\d)?)\s*(?:인치|형|inch|\")", re.IGNORECASE)


def is_kr_promo_range_title(title: str) -> bool:
    """Return True if title is a multi-item promo range/collection rather than a single product."""
    return bool(_KR_PROMO_RANGE_TITLE_RE.search(title or ""))


def is_kr_accessory_title(title: str) -> bool:
    """Return True if a Korean/mixed title describes an accessory, consumable refill, or spare part."""
    return bool(_KR_ACCESSORY_RE.search(title or ""))


def normalize_brand_token(token: str) -> str:
    """Normalize major Korean/English brand synonyms into a single canonical token."""
    low = token.lower()
    return _KR_BRAND_SYNONYMS.get(low, low)


def extract_product_specs(text: str) -> dict[str, set[str]]:
    """Extract normalized quantitative specs (storage, weight, volume, count, plus_pack, screen)."""
    cleaned = html.unescape(text or "")
    # Remove prices and percentages before spec parsing so "12,000원" or "30%" don't interfere
    cleaned = re.sub(r"\d{1,3}(?:,\d{3})+\s*원?", " ", cleaned)
    cleaned = re.sub(r"\d+(?:\.\d+)?\s*만\s*\d*천?\s*원?", " ", cleaned)
    cleaned = re.sub(r"\d{1,2}\s*%", " ", cleaned)

    specs: dict[str, set[str]] = {
        "storage": set(),
        "weight": set(),
        "volume": set(),
        "count": set(),
        "plus_pack": set(),
        "screen": set(),
    }

    for m in _SPEC_STORAGE_RE.finditer(cleaned):
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "tb" and 1 <= val <= 32:
            specs["storage"].add(f"{val}tb")
        elif unit == "gb" and 4 <= val <= 4096:
            specs["storage"].add(f"{val}gb")

    for m in _SPEC_WEIGHT_RE.finditer(cleaned):
        val = float(m.group(1))
        unit = m.group(2).lower()
        grams = int(round(val * 1000)) if unit == "kg" else int(round(val))
        if 1 <= grams <= 100_000:
            specs["weight"].add(str(grams))

    for m in _SPEC_VOLUME_RE.finditer(cleaned):
        val = float(m.group(1))
        unit = m.group(2).lower()
        ml = int(round(val * 1000)) if unit in ("l", "리터") else int(round(val))
        if 5 <= ml <= 50_000:
            specs["volume"].add(str(ml))

    for m in _SPEC_COUNT_RE.finditer(cleaned):
        cnt = int(m.group(1))
        if 1 <= cnt <= 2000:
            specs["count"].add(str(cnt))

    for m in _SPEC_MULT_COUNT_RE.finditer(cleaned):
        cnt = int(m.group(1))
        if 2 <= cnt <= 500:
            specs["count"].add(str(cnt))

    for m in _SPEC_PLUS_PACK_RE.finditer(cleaned):
        specs["plus_pack"].add(m.group(1))

    for m in _SPEC_SCREEN_RE.finditer(cleaned):
        val = float(m.group(1))
        if 10.0 <= val <= 110.0:
            specs["screen"].add(f"{val:g}")

    return specs


def build_naver_search_query(title: str) -> str:
    """Build a crisp Korean product query for Naver Shopping API price comparison."""
    q = clean_kr_product_query(title)
    # Strip remaining Korean price shorthands like '12.9만원', '₩25,990', '14.8발'
    q = re.sub(r"₩\s*\d[\d,]*", " ", q)
    q = re.sub(r"(?<!\d)\d{1,3}(?:\.\d{1,2})?\s*만\s*(?:\d{1,4}\s*천?)?\s*원?", " ", q)
    q = re.sub(r"(?<!\d)\d{1,2}\.\d{1,2}\s*발\b", " ", q)
    # Strip trailing '/무료', '/무배', '/유배', '(...)' pure promo brackets
    q = re.sub(r"/\s*(?:무료|무배|유배|와우|네멤|배송비[^\s]*)\b", " ", q)
    q = re.sub(r"\([^)]{0,20}(?:무료|무배|와우|네멤|쿠폰|카드|할인|특가|적립|배송)[^)]{0,20}\)", " ", q)
    q = _KR_QUERY_NOISE_RE.sub(" ", q)
    q = _WHITESPACE_RE.sub(" ", q).strip(" -:,|/()[]~!?")
    return q[:55]
