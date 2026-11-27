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
