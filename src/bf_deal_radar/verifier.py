"""Multi-signal real-discount verification, Korean category classification, and scoring engine."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bf_deal_radar.exchange import estimate_customs
from bf_deal_radar.models import AppConfig, ScoredDeal

_ALL_TIME_LOW_RE = re.compile(
    r"\b(?:all[\s-]time\s+low|lowest\s+price\s+ever|lowest\s+ever|record\s+low|"
    r"best\s+price\s+ever|amazon\s+all[\s-]time\s+low|new\s+low|"
    r"for\s+the\s+first\s+time|lowest\s+it(?:'s|\s+has)\s+ever\s+been)\b",
    re.IGNORECASE,
)

_RECENT_LOW_RE = re.compile(
    r"\b(?:matches\s+its\s+(?:amazon\s+)?lowest|best\s+price(?:\s+of\s+the\s+year)?|"
    r"lowest\s+price(?:\s+we(?:'ve|\s+have)\s+seen)?|rare\s+\$?\d*\s*discount|"
    r"historic(?:al)?\s+low|beats?\s+its\s+previous\s+low)\b",
    re.IGNORECASE,
)

# High-wattage / AC-powered US appliances that often require 120V (not dual-voltage 100-240V)
_US_VOLTAGE_APPLIANCE_RE = re.compile(
    r"\b(?:espresso|coffee\s+maker|keurig|nespresso|air\s+fryer|blender|stand\s+mixer|"
    r"toaster|microwave|electric\s+kettle|rice\s+cooker|pressure\s+cooker|instant\s+pot|"
    r"space\s+heater|heater|hair\s+dryer|airwrap|supersonic|air\s+purifier|humidifier|"
    r"dehumidifier|vacuum|oled\s+tv|qled\s+tv|smart\s+tv|led\s+tv|fire\s+tv|soundbar|"
    r"subwoofer|av\s+receiver|amplifier|waffle\s+maker|slow\s+cooker|juicer|ice\s+maker)\b",
    re.IGNORECASE,
)

# Large / heavy items where US->Korea air freight forwarding shipping can be expensive
_BULKY_ITEM_RE = re.compile(
    r"(?:\b(?:50|55|65|70|75|77|83|85|98)\s*(?:\"|in\.?\b|inch\b).*\btv\b|"
    r"\bgaming\s+pc\b|\bdesktop\s+pc\b|\bsubwoofer\b|\bair\s+conditioner\b|"
    r"\bstanding\s+desk\b|\boffice\s+chair\b|\bgaming\s+chair\b|\bmicrowave\b)",
    re.IGNORECASE,
)

# US membership-gated stores that often require US membership or cancel freight forwarders
_US_MEMBERSHIP_RE = re.compile(
    r"\b(?:costco\s+members?|sam'?s\s+club\s+members?|my\s+best\s+buy\s+(?:plus|total))\b",
    re.IGNORECASE,
)

_ROUNDUP_TITLE_RE = re.compile(
    r"(?:^live:\s|\b(?:best\s+(?:amazon\s+)?(?:black\s+friday|thanksgiving|cyber\s+monday)\s+deals|"
    r"\d+\+\s+of\s+the\s+best|deals\s+we(?:'ve|\s+have)\s+already\s+found|"
    r"deals\s+roundup|deals\s+hub|sale\s+now\s+live|deals\s+now\s+live|live\s+blog|"
    r"everything\s+on\s+sale|top\s+\d+\s+deals|shopping\s+guide|"
    r"every\s+current\s+model)\b|"
    r"\b(?:black\s+friday(?:\s+week)?|thanksgiving|cyber\s+monday)\s+deals:|"
    r"\bin\s+my\s+cart:|,\s*more\s*$)",
    re.IGNORECASE,
)

# Accessory words that indicate a cheap add-on rather than the primary watched device itself
_ACCESSORY_NOUN_RE = re.compile(
    r"\b(?:band|strap|case|cover|sleeve|skin|folio|screen\s+protector|tempered\s+glass|"
    r"charger|charging\s+(?:pad|stand|dock)|cable|adapter|dongle|hub|dock|stand|mount|"
    r"power\s+bank|ear\s+tips|replacement|pouch|bag|backpack)\b",
    re.IGNORECASE,
)

_PLATFORM_MAP = {
    "slickdeals": "Slickdeals",
    "dealnews": "DealNews",
    "techbargains": "TechBargains",
    "9to5toys": "9to5",
    "9to5mac": "9to5",
    "9to5google": "9to5",
    "pcmag": "PCMag",
    "the verge": "The Verge",
    "cnet": "CNET",
    "ign": "IGN",
    "hip2save": "Hip2Save",
    "dan's deals": "Dan's Deals",
    "gottadeal": "GottaDeal",
    "reddit": "Reddit",
    # Map specific Korean communities BEFORE generic aggregator names so
    # "핫구사·루리웹" and "루리웹 핫딜" collapse into one platform ("루리웹").
    "루리웹": "루리웹",
    "클리앙": "클리앙",
    "딜바다": "딜바다",
    "어미새": "어미새",
    "개드립": "개드립",
    "뽐뿌": "뽐뿌",
    "에펨코리아": "에펨코리아",
    "아카라이브": "아카라이브",
    "퀘이사존": "퀘이사존",
    "쿨엔조이": "쿨엔조이",
    "zod": "ZOD",
    "다사자": "다사자",
    "핫구사": "핫구사",
}

_KR_TRUSTED_MALLS = {
    "쿠팡",
    "쿠팡와우",
    "네이버",
    "G마켓",
    "옥션",
    "11번가",
    "롯데온",
    "SSG",
    "하이마트",
    "카카오톡딜",
    "토스쇼핑",
    "올리브영",
    "무신사",
    "29CM",
    "W컨셉",
    "지그재그",
    "SSF샵",
    "KREAM",
    "홈플러스",
    "이마트",
    "코스트코",
    "농협몰",
    "우체국몰",
    "마켓컬리",
    "GS샵",
    "CJ온스타일",
    "오늘의집",
    "컴퓨존",
    "알리익스프레스",
    "테무",
    "아이허브",
    "나이키",
    "아디다스",
    "유니클로",
}

_KR_HOT_VALUE_RE = re.compile(
    r"(?:역대가|역대\s*최저|초특가|반값|대란|혜자|최저가|꿀딜|특가|50%\s*할인)",
    re.IGNORECASE,
)

_KR_CATEGORY_MAP = {
    "음식": "🍔 식품/간식",
    "식품": "🍔 식품/간식",
    "식품/건강": "🍔 식품/간식",
    "사탕": "🍔 식품/간식",
    "과자": "🍔 식품/간식",
    "음료": "🍔 식품/간식",
    "임박특가": "🍔 식품/간식",
    "PC/가전": "💻 IT/가전",
    "PC/HW": "💻 IT/가전",
    "디지털": "💻 IT/가전",
    "전자": "💻 IT/가전",
    "컴퓨터": "💻 IT/가전",
    "가전": "🏠 스마트홈/가전",
    "게임S/W": "🎮 게이밍",
    "게임/완구": "🎮 게이밍",
    "생활용품": "🏠 생활/주방",
    "의류": "👕 패션/잡화",
    "의류/잡화": "👕 패션/잡화",
    "패션국내": "👕 패션/잡화",
    "패션해외": "👕 패션/잡화",
    "패션/뷰티": "👕 패션/잡화",
    "패션/의류": "👕 패션/잡화",
    "화장품": "🧴 뷰티/건강",
    "건강/뷰티": "🧴 뷰티/건강",
    "아이허브": "🧴 뷰티/건강",
    "등산/캠핑": "🏕️ 스포츠/캠핑",
    "스포츠/캠핑": "🏕️ 스포츠/캠핑",
    "자동차/공구": "🚗 자동차/공구",
    "육아": "🍼 육아/반려동물",
    "상품권": "🎟️ 상품권/쿠폰",
    "상품정보": "🛍️ 국내핫딜",
    "쿠팡핫딜": "🛍️ 쿠팡핫딜",
    "알리핫딜": "🌏 알리/직구",
    "테무핫딜": "🌏 알리/직구",
    "해외핫딜": "🌏 해외직구",
    "이벤트정보": "🎁 이벤트/혜택",
}

_CATEGORY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:ssd|nvme|ryzen|processor|graphics\s+card|motherboard|ddr5|cpu|nas|synology)\b", re.I), "💾 SSD/PC부품"),
    (re.compile(r"\b(?:macbook|laptop|chromebook|thinkpad|legion|gaming\s+pc|mini\s+pc|desktop\s+pc)\b|노트북|맥북", re.I), "💻 노트북/PC"),
    (re.compile(r"\b(?:headphone|earbud|airpods|soundbar|speaker|bose|sonos|sennheiser|beats|wh-1000xm)\b|이어폰|헤드폰|에어팟|스피커|마이크", re.I), "🎧 음향/오디오"),
    (re.compile(r"\b(?:smartwatch|garmin|apple\s+watch|smart\s+ring|ringconn|forerunner|fenix|fēnix|instinct)\b|애플워치|갤럭시워치", re.I), "⌚ 스마트워치/웨어러블"),
    (re.compile(r"\b(?:ipad|tablet|iphone|pixel|galaxy|kindle|e-reader)\b|아이패드|아이폰|갤럭시|태블릿", re.I), "📱 태블릿/모바일"),
    (re.compile(r"\b(?:oled|qled|monitor|4k\s+tv|smart\s+tv|led\s+tv|projector|fire\s+tv)\b|모니터|(?:^|[\s\[\(])티비(?:$|[\s\]\)])", re.I), "🖥️ 모니터/TV"),
    (re.compile(r"\b(?:ps5|playstation|nintendo|switch\s*2?|xbox|steam\s+deck|controller|lego)\b|스팀|닌텐도|플스|마인크래프트|레고", re.I), "🎮 게이밍"),
    (re.compile(r"\b(?:camera|gopro|dji|insta360|lens|drone)\b|카메라|드론", re.I), "📷 카메라/드론"),
    (re.compile(r"\b(?:charger|power\s+bank|gan|usb-c|hub|router|wi-fi|wifi|keyboard|mouse|airtag)\b|파워뱅크|보조배터리|충전기|키보드|마우스|보호필름", re.I), "🔌 주변기기/충전"),
    (re.compile(r"\b(?:smart\s+plug|thermostat|security\s+cam|solocam|vacuum|roborock|dyson|espresso|coffee\s+maker|heater|purifier|blender|air\s+fryer)\b|청소기|공기청정기|온풍기|가습기|드라이어|에스프레소", re.I), "🏠 스마트홈/가전"),
    (re.compile(r"\b(?:jacket|hoodie|sweater|sneakers?|running\s+shoes?|boots?|jeans?|denim|shirt|coat|parka|fleece|nike|adidas|arcteryx|arc'teryx|lululemon)\b|패딩|자켓|재킷|코트|후드|맨투맨|니트|데님|운동화|러닝화|스니커즈|부츠|아크테릭스|닥터마틴|보드숏|가방|백팩", re.I), "👕 패션/잡화"),
    (re.compile(r"\b(?:shampoo|lotion|serum|sunscreen|vitamin|supplement|protein|perfume|skincare|toothpaste)\b|로션|선크림|샴푸|트리트먼트|치약|칫솔|화장품|세럼|비타민|멀티비타|비타|유산균|영양제|홍삼", re.I), "🧴 뷰티/건강"),
    (re.compile(r"\b(?:tent|camping|hiking|golf|dumbbell|treadmill|bicycle|bike|cooler|yeti)\b|캠핑|등산|텐트|골프|자전거|텀블러", re.I), "🏕️ 스포츠/캠핑"),
    (re.compile(r"\b(?:drill|dewalt|makita|milwaukee|wrench|toolbox|dash\s*cam|wiper|tire)\b|전동드릴|스크루|공구|블랙박스|와이퍼|세차", re.I), "🚗 자동차/공구"),
    (re.compile(r"\b(?:diaper|stroller|pet\s+food|cat\s+litter|dog\s+food)\b|기저귀|분유|물티슈|고양이|강아지|사료", re.I), "🍼 육아/반려동물"),
    (re.compile(r"감귤|샤인머스켓|사과|꿀사과|키위|딸기|한라봉|천혜향|복숭아|수박|참외|오렌지|블루베리|체리|포도|망고|바나나|토마토|감자|양파|마늘|고구마|백미|현미|찹쌀|계란|우유|두유|요거트|치즈|버터|라면|우동|만두|떡갈비|닭갈비|닭가슴살|곱창|갈비탕|어묵|과자|스낵|캔디|초코|초콜릿|젤리|베이글|케이크|쿠키|견과|아몬드|호두|콜라|탄산|음료|주스|매실|커피|원두|드립백|생수|햇반|식용유|고등어|오징어|새우|갈치|전복|삼겹살|목살|한돈|한우|소고기|돼지고기|오리|치킨|피자|아이스크림|배스킨|베스킨|레몬즙|김치|연어|아보카도", re.I), "🍔 식품/간식"),
    (re.compile(r"\b(?:organizer|detergent|cookware|skillet|pan|towel|bedding)\b|세제|섬유유연제|휴지|화장지|주방세제|프라이팬|냄비|수건|도자기|그릇|타원볼|식기", re.I), "🏠 생활/주방"),
]


def classify_korean_category(title: str, category_hint: str) -> str:
    """Assign a clean Korean category tag based on product title or community category."""
    if category_hint in _KR_CATEGORY_MAP and category_hint in (
        "음식",
        "식품/건강",
        "상품권",
        "의류",
        "의류/잡화",
        "패션국내",
        "패션해외",
        "패션/뷰티",
        "패션/의류",
    ):
        return _KR_CATEGORY_MAP[category_hint]
    combined = f"{title} {category_hint}"
    for pattern, label in _CATEGORY_RULES:
        if pattern.search(combined):
            return label
    if category_hint in _KR_CATEGORY_MAP:
        return _KR_CATEGORY_MAP[category_hint]
    if category_hint in ("IT/가전", "IT/PC"):
        return "💻 IT/가전"
    return f"🛍️ {category_hint}"


def _platform_for_source(name: str) -> str:
    lower = name.lower()
    for key, label in _PLATFORM_MAP.items():
        if key in lower:
            return label
    return name


def _distinct_platforms(source_names: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for name in source_names:
        platform = _platform_for_source(name)
        if platform not in seen:
            seen.add(platform)
            found.append(platform)
    return found


def dedupe_source_labels(source_names: list[str]) -> list[str]:
    """Keep at most one representative source label per platform for clean UI/Telegram display."""
    found: list[str] = []
    seen: set[str] = set()
    for name in source_names:
        platform = _platform_for_source(name)
        if platform not in seen:
            seen.add(platform)
            found.append(name)
    return found


def _contains_keyword(haystack: str, keyword: str) -> bool:
    kw = keyword.strip().lower()
    if not kw:
        return False
    # Use word boundaries for short ASCII alphanumeric terms to avoid false matches ("ram" in "program")
    if len(kw) <= 4 and kw.isascii() and kw.isalnum():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", haystack))
    return kw in haystack


def _is_accessory_mention(title_lower: str, keyword: str, price_usd: float | None) -> bool:
    """Return True if a VIP device keyword only appears as an accessory (e.g., 'Apple Watch band', 'Hub for MacBook')."""
    kw_esc = re.escape(keyword.lower())
    # Explicit "for <device>" / "w/ <device> charger" / "<device> band|case|strap|charger"
    if re.search(rf"\b(?:for|fits?|compatible\s+with|w/)\s+(?:the\s+)?{kw_esc}\b", title_lower):
        return True
    if re.search(rf"\b{kw_esc}\s+(?:band|strap|case|cover|charger|cable|dock|stand|mount|sleeve|hub|adapter)\b", title_lower):
        return True
    # Cheap accessory (< $80) containing accessory nouns alongside an expensive device brand
    if _ACCESSORY_NOUN_RE.search(title_lower) and price_usd is not None and price_usd < 80.0:
        if keyword.lower() in {"macbook", "ipad", "airpods", "apple watch", "garmin", "dyson", "roborock", "steam deck"}:
            return True
    return False


_KR_PROMO_RANGE_RE = re.compile(
    r"(?:모음전|외\s*다수|외\s*\d+종|外\s*\d+종|단\s*하루|단하루|골라담기|BEST상품|특가전|\d[\d,]*원\s*~)"
)


def _verify_and_score_kr(
    deal: ScoredDeal,
    config: AppConfig,
    now: datetime,
) -> ScoredDeal:
    """Evaluate a Korean domestic hot deal with strict pillar caps (70+ genuine deals, 90+ rare mega-deals)."""
    primary = deal.primary
    title_lower = primary.title.lower()
    full_text = f"{primary.title}\n{primary.description}\n{primary.category or ''}".lower()

    # 1. Immediately drop sold-out / expired deals
    if primary.is_sold_out:
        deal.excluded_reason = "품절/종료된 딜"
        return deal

    # 2. Age check
    if primary.published_at is not None and config.max_age_hours > 0:
        cutoff = now - timedelta(hours=config.max_age_hours)
        if primary.published_at < cutoff:
            deal.excluded_reason = f"오래된 딜 ({config.max_age_hours}시간 초과)"
            return deal

    # 3. Korean spam / click-point / non-deal exclusion
    for kw in config.filters.kr_exclude_keywords:
        if _contains_keyword(full_text, kw):
            deal.excluded_reason = f"국내 제외 키워드 매칭: {kw}"
            return deal

    # 4. Minimum KRW price check (drops 5원/10원/500원 point-click promos)
    if primary.price_krw is not None and config.min_price_krw is not None:
        if primary.price_krw < config.min_price_krw:
            deal.excluded_reason = f"최소 가격 미달 ({primary.price_krw:,}원)"
            return deal

    # Normalized source credibility base (11~17 pts)
    score = min(17, int(round(primary.source_base_score * 0.78)))
    reasons: list[str] = []

    upvotes = primary.thumb_score or 0
    downvotes = primary.downvote_count or 0
    comments = primary.comment_count or 0
    views = primary.view_count or 0
    platforms = _distinct_platforms(deal.merged_sources)

    # 5. Pillar A — Community engagement proof (capped at 26 pts so viral small discounts cannot inflate to 100)
    eng = 0
    if upvotes >= 40:
        eng += 16
        reasons.append(f"🔥 커뮤니티 추천 폭주 (+{upvotes})")
    elif upvotes >= 20:
        eng += 13
        reasons.append(f"🔥 커뮤니티 고추천 (+{upvotes})")
    elif upvotes >= 10:
        eng += 9
        reasons.append(f"👍 커뮤니티 추천 (+{upvotes})")
    elif upvotes >= 5:
        eng += 6
        reasons.append(f"👍 커뮤니티 추천 (+{upvotes})")
    elif upvotes >= 2:
        eng += 3
        reasons.append(f"👍 추천 (+{upvotes})")

    if comments >= 30:
        eng += 12
        reasons.append(f"💬 댓글 폭발 ({comments}개)")
    elif comments >= 15:
        eng += 8
        reasons.append(f"💬 뜨거운 반응 (댓글 {comments}개)")
    elif comments >= 8:
        eng += 5
        reasons.append(f"💬 댓글 반응 ({comments}개)")
    elif comments >= 4:
        eng += 2

    if views >= 10000:
        eng += 5
        reasons.append(f"👀 높은 관심 (조회 {views:,})")
    elif views >= 4000:
        eng += 2

    if primary.staff_pick:
        eng += 4
        reasons.append("🔥 커뮤니티 인기(불꽃) 핫딜")

    score += min(26, eng)

    if downvotes >= 3 and downvotes >= upvotes:
        score -= 28
        reasons.append(f"⚠️ 비추천 다수 (추천 {upvotes} / 비추 {downvotes})")

    # 6. Pillar B — Cross-community confirmation (2 sites +12, 3+ sites +18)
    if len(platforms) >= 3:
        score += 18
        reasons.append(f"🔁 국내 {len(platforms)}개 커뮤니티 동시 포착 ({', '.join(platforms)})")
    elif len(platforms) == 2:
        score += 12
        reasons.append(f"🔁 국내 2개 커뮤니티 교차 검증 ({', '.join(platforms)})")

    # 7. Pillar C — Discount depth & historical-low proof (unified, capped at 44 pts — no double-counting)
    has_stat_low = (
        "개월 평균가 대비" in primary.description
        or ("대비" in primary.description and "가격 하락" in primary.description)
    )
    pct = primary.discount_pct
    disc = 0
    if pct is not None:
        if pct >= 50:
            disc = 34
            reasons.append(f"🔥 {pct:.0f}% 초특가 할인")
        elif pct >= 40:
            disc = 29
            reasons.append(f"💥 {pct:.0f}% 대폭 할인")
        elif pct >= 30:
            disc = 23
            reasons.append(f"📉 {pct:.0f}% 대폭 할인")
        elif pct >= 20:
            disc = 15
            reasons.append(f"📉 {pct:.0f}% 할인")
        elif pct >= 15:
            disc = 8
            reasons.append(f"💸 {pct:.0f}% 할인")

    # Cap unverified store-banner percentages on multi-item range promos (e.g. "모음전 8,900원~ (74%)")
    if (
        not has_stat_low
        and disc > 10
        and _KR_PROMO_RANGE_RE.search(primary.title)
        and upvotes < 8
        and comments < 8
        and len(platforms) < 2
    ):
        disc = 10

    if has_stat_low:
        if pct is not None and pct >= 25:
            deal.is_historical_low = True
            disc = min(44, disc + 10)
            reasons.append(f"🏆 {primary.description}")
        elif pct is not None and pct >= 20:
            deal.is_historical_low = True
            disc = min(44, disc + 6)
            reasons.append(f"🏆 {primary.description}")
    elif _KR_HOT_VALUE_RE.search(full_text):
        deal.is_historical_low = "역대" in full_text or "최저" in full_text
        if pct is None:
            disc = min(44, disc + 10)
            reasons.append("🏆 역대가/초특가 키워드")
        elif pct >= 20:
            disc = min(44, disc + 4)
            reasons.append("🏆 역대가/초특가 키워드")

    score += disc

    # 8. Trusted domestic mall & free shipping baseline (+3 / +2)
    if primary.retailer in _KR_TRUSTED_MALLS:
        score += 3
    if primary.shipping_label == "무료배송":
        score += 2

    # 9. Optional VIP watch keyword bonus (+8)
    matched_watch = [
        kw for kw in config.filters.watch_keywords if _contains_keyword(title_lower, kw)
    ]
    if matched_watch:
        deal.is_watch_match = True
        deal.matched_watch_keywords = matched_watch[:3]
        score += 8
        reasons.append(f"🚨 관심 키워드 ({', '.join(deal.matched_watch_keywords)})")

    # Down-rank vague promo notices with no concrete price and low community proof
    if primary.price_krw is None and primary.discount_pct is None and upvotes < 5 and comments < 10:
        score -= 18

    deal.category_label_ko = classify_korean_category(primary.title, primary.category_hint)
    deal.is_tech_category = any(
        tag in deal.category_label_ko for tag in ("노트북", "PC", "음향", "워치", "태블릿", "모니터", "카메라", "주변기기")
    )
    deal.score = max(0, min(100, int(round(score))))
    deal.reasons = reasons
    return deal


def verify_and_score(
    deal: ScoredDeal,
    config: AppConfig,
    usd_krw_rate: float,
    now: datetime | None = None,
    previous_low_usd: float | None = None,
) -> ScoredDeal:
    """Evaluate whether a deal (US overseas or KR domestic) is a genuine big discount."""
    now = now or datetime.now(timezone.utc)
    if deal.is_domestic_kr:
        return _verify_and_score_kr(deal, config=config, now=now)
    primary = deal.primary
    title_lower = primary.title.lower()
    title_and_cat = f"{primary.title} {primary.category or ''}".lower()
    full_text = f"{primary.title}\n{primary.description}\n{primary.category or ''}".lower()

    # 1. Age check
    if primary.published_at is not None and config.max_age_hours > 0:
        cutoff = now - timedelta(hours=config.max_age_hours)
        if primary.published_at < cutoff:
            deal.excluded_reason = f"오래된 딜 ({config.max_age_hours}시간 초과)"
            return deal

    # 2. Hard exclude keywords (US-only services, military/student-only, in-store only, bulky unshippable)
    for kw in config.filters.exclude_keywords:
        if _contains_keyword(full_text, kw):
            deal.excluded_reason = f"제외 키워드 매칭: {kw}"
            return deal

    # 3. Price range check (only when a concrete price was parsed)
    if primary.price_usd is not None:
        if config.min_price_usd is not None and primary.price_usd < config.min_price_usd:
            deal.excluded_reason = f"최소 가격 미달 (${primary.price_usd:.2f})"
            return deal
        if config.max_price_usd is not None and primary.price_usd > config.max_price_usd:
            deal.excluded_reason = f"최대 가격 초과 (${primary.price_usd:.2f})"
            return deal

    # Normalized source credibility base (10~20 pts)
    score = min(20, int(round(primary.source_base_score * 0.85)))
    reasons: list[str] = []

    # 4. Community & editor validation (capped at 18 pts)
    if primary.source_id == "slickdeals_fp":
        reasons.append("⭐ Slickdeals Frontpage (커뮤니티 검증)")
    eng = 0
    if primary.thumb_score is not None:
        if primary.thumb_score >= 150:
            eng += 16
            reasons.append(f"🔥 커뮤니티 추천 폭주 (+{primary.thumb_score})")
        elif primary.thumb_score >= 80:
            eng += 12
            reasons.append(f"🔥 커뮤니티 고추천 (+{primary.thumb_score})")
        elif primary.thumb_score >= 40:
            eng += 8
            reasons.append(f"👍 커뮤니티 추천 (+{primary.thumb_score})")
        elif primary.thumb_score >= 20:
            eng += 4
            reasons.append(f"👍 커뮤니티 추천 (+{primary.thumb_score})")
        elif primary.thumb_score < 0:
            eng -= 20
            reasons.append(f"⚠️ 커뮤니티 비추천 ({primary.thumb_score})")

    if primary.staff_pick:
        eng += 11
        reasons.append("✅ DealNews 에디터 Staff Pick")

    score += min(18, eng)

    # 5. Cross-platform confirmation (strongest real-deal signal!)
    platforms = _distinct_platforms(deal.merged_sources)
    if len(platforms) >= 3:
        score += 22
        reasons.append(f"🔁 {len(platforms)}개 사이트 동시 포착 ({', '.join(platforms)})")
    elif len(platforms) == 2:
        score += 15
        reasons.append(f"🔁 2개 사이트 교차 검증 ({', '.join(platforms)})")

    # 6. Quantitative discount depth (max 36 pts)
    discount_points = 0
    if primary.discount_pct is not None:
        pct = primary.discount_pct
        if pct >= 60:
            discount_points = 36
            reasons.append(f"🔥 {pct:.0f}% 초특가 할인")
        elif pct >= 50:
            discount_points = 32
            reasons.append(f"🔥 {pct:.0f}% 초특가 할인")
        elif pct >= 40:
            discount_points = 26
            reasons.append(f"💥 {pct:.0f}% 대폭 할인")
        elif pct >= 30:
            discount_points = 20
            reasons.append(f"📉 {pct:.0f}% 할인")
        elif pct >= 20:
            discount_points = 13
            reasons.append(f"📉 {pct:.0f}% 할인")
        elif pct >= 15:
            discount_points = 7
            reasons.append(f"💸 {pct:.0f}% 할인")

    if primary.savings_usd is not None:
        sav = primary.savings_usd
        sav_pts = 0
        if sav >= 250:
            sav_pts = 24
        elif sav >= 120:
            sav_pts = 18
        elif sav >= 60:
            sav_pts = 12
        elif sav >= 30:
            sav_pts = 6
        if sav_pts > discount_points:
            discount_points = sav_pts
            reasons.append(f"💵 ${sav:,.0f} 즉시 할인")

    score += discount_points

    # 7. Historical low / price-drop verification
    if _ALL_TIME_LOW_RE.search(full_text):
        deal.is_historical_low = True
        score += 20
        reasons.append("🏆 역대 최저가(All-Time Low) 언급")
    elif _RECENT_LOW_RE.search(full_text):
        deal.is_historical_low = True
        score += 13
        reasons.append("📉 최근/연중 최저가 언급")

    # Internal price-history drop check (if we saw this exact item at a higher price earlier)
    if (
        previous_low_usd is not None
        and primary.price_usd is not None
        and previous_low_usd > 0
        and primary.price_usd <= previous_low_usd * 0.90
    ):
        drop_pct = (1.0 - primary.price_usd / previous_low_usd) * 100.0
        score += 14
        reasons.append(f"📉 이전 포착가(${previous_low_usd:,.2f}) 대비 {drop_pct:.0f}% 추가 하락")

    # 8. User VIP watch keywords (strictly on title, excluding cheap accessories like "Apple Watch band")
    matched_watch = [
        kw
        for kw in config.filters.watch_keywords
        if _contains_keyword(title_lower, kw)
        and not _is_accessory_mention(title_lower, kw, primary.price_usd)
    ]
    if matched_watch:
        deal.is_watch_match = True
        deal.matched_watch_keywords = matched_watch[:3]
        score += 10
        reasons.append(f"🚨 관심 키워드 매칭 ({', '.join(deal.matched_watch_keywords)})")

    is_tech = primary.category_hint in ("IT/가전", "IT/PC", "IT/게이밍") or any(
        _contains_keyword(title_and_cat, kw) for kw in config.filters.boost_keywords
    )
    if is_tech:
        deal.is_tech_category = True
        score += 6
        if not matched_watch:
            reasons.append("💻 IT/PC/가전 관심 분야")

    deal.category_label_ko = classify_korean_category(primary.title, primary.category_hint)

    # 9. Condition, US membership, and roundup penalties
    for kw in config.filters.condition_penalty_keywords:
        if _contains_keyword(title_lower, kw):
            deal.condition_warning = kw
            score -= 15
            reasons.append(f"⚠️ 상품 상태 주의 ({kw})")
            break

    if _US_MEMBERSHIP_RE.search(title_lower):
        deal.membership_warning = "🏷️ 미국 회원제 필요 / 배대지 취소 주의"
        score -= 12

    if _ROUNDUP_TITLE_RE.search(title_lower):
        score -= 40
        deal.is_watch_match = False
        reasons.append("⚠️ 단일 상품이 아닌 모음/허브 기사 (감점)")
    elif primary.price_usd is None and primary.discount_pct is None and primary.savings_usd is None:
        score -= 22

    # 10. Korean overseas-import practical heads-up badges (110V voltage & bulky freight)
    if (
        _US_VOLTAGE_APPLIANCE_RE.search(title_and_cat)
        and "usb" not in title_lower
        and "battery" not in title_lower
        and "stick" not in title_lower
    ):
        deal.voltage_warning = "⚡ 미국 110~120V 전압 확인 필요 (프리볼트/변압기 체크)"

    if _BULKY_ITEM_RE.search(title_lower):
        deal.bulky_warning = "📦 대형/고중량 (배대지 항공 배송비 확인 필요)"

    # 11. Korean KRW + customs calculation
    if primary.price_usd is not None:
        deal.customs = estimate_customs(
            price_usd=primary.price_usd,
            usd_krw_rate=usd_krw_rate,
            threshold_usd=config.us_customs_threshold_usd,
            is_tech=deal.is_tech_category,
        )

    deal.score = max(0, min(100, int(round(score))))
    deal.reasons = reasons
    return deal
