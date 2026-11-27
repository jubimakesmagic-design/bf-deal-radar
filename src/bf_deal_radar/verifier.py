"""Multi-signal real-discount verification and scoring engine."""

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

_ROUNDUP_TITLE_RE = re.compile(
    r"(?:\b(?:best\s+(?:amazon\s+)?black\s+friday\s+deals|deals\s+we(?:'ve|\s+have)\s+already\s+found|"
    r"deals\s+roundup|deals\s+hub|sale\s+now\s+live|deals\s+now\s+live|live\s+blog|"
    r"everything\s+on\s+sale|top\s+\d+\s+deals|shopping\s+guide|in\s+my\s+cart:)\b|"
    r",\s*more\s*$)",
    re.IGNORECASE,
)

_PLATFORM_MAP = {
    "slickdeals": "Slickdeals",
    "dealnews": "DealNews",
    "techbargains": "TechBargains",
    "9to5toys": "9to5Toys",
    "the verge": "The Verge",
    "reddit": "Reddit",
}


def _distinct_platforms(source_names: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for name in source_names:
        lower = name.lower()
        platform = name
        for key, label in _PLATFORM_MAP.items():
            if key in lower:
                platform = label
                break
        if platform not in seen:
            seen.add(platform)
            found.append(platform)
    return found


def _contains_keyword(haystack: str, keyword: str) -> bool:
    kw = keyword.strip().lower()
    if not kw:
        return False
    # Use word boundaries for short alphanumeric terms to avoid false matches (e.g., "ram" in "program")
    if len(kw) <= 4 and kw.isalnum():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", haystack))
    return kw in haystack


def verify_and_score(
    deal: ScoredDeal,
    config: AppConfig,
    usd_krw_rate: float,
    now: datetime | None = None,
    previous_low_usd: float | None = None,
) -> ScoredDeal:
    """Evaluate whether a deal is a genuine big discount and compute an explainable 0-100 score."""
    now = now or datetime.now(timezone.utc)
    primary = deal.primary
    title_lower = primary.title.lower()
    full_text = f"{primary.title}\n{primary.description}\n{primary.category or ''}".lower()

    # 1. Age check
    if primary.published_at is not None and config.max_age_hours > 0:
        cutoff = now - timedelta(hours=config.max_age_hours)
        if primary.published_at < cutoff:
            deal.excluded_reason = f"오래된 딜 ({config.max_age_hours}시간 초과)"
            return deal

    # 2. Hard exclude keywords (US-only services, in-store only, noise)
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

    score = primary.source_base_score
    reasons: list[str] = []

    # 4. Source reputation & community validation
    if primary.source_id == "slickdeals_fp":
        reasons.append("⭐ Slickdeals Frontpage (커뮤니티 검증)")
    if primary.thumb_score is not None:
        if primary.thumb_score >= 100:
            score += 18
            reasons.append(f"🔥 커뮤니티 추천 폭주 (+{primary.thumb_score})")
        elif primary.thumb_score >= 50:
            score += 12
            reasons.append(f"👍 커뮤니티 고추천 (+{primary.thumb_score})")
        elif primary.thumb_score >= 20:
            score += 6
            reasons.append(f"👍 커뮤니티 추천 (+{primary.thumb_score})")
        elif primary.thumb_score < 0:
            score -= 20
            reasons.append(f"⚠️ 커뮤니티 비추천 ({primary.thumb_score})")

    if primary.staff_pick:
        score += 16
        reasons.append("✅ DealNews 에디터 Staff Pick")

    # 5. Cross-platform confirmation (strongest real-deal signal!)
    platforms = _distinct_platforms(deal.merged_sources)
    if len(platforms) >= 3:
        score += 30
        reasons.append(f"🔁 {len(platforms)}개 사이트 동시 포착 ({', '.join(platforms)})")
    elif len(platforms) == 2:
        score += 22
        reasons.append(f"🔁 2개 사이트 교차 검증 ({', '.join(platforms)})")

    # 6. Quantitative discount depth
    discount_points = 0
    if primary.discount_pct is not None:
        pct = primary.discount_pct
        if pct >= 50:
            discount_points = 34
            reasons.append(f"🔥 {pct:.0f}% 초특가 할인")
        elif pct >= 35:
            discount_points = 26
            reasons.append(f"💥 {pct:.0f}% 대폭 할인")
        elif pct >= 25:
            discount_points = 18
            reasons.append(f"📉 {pct:.0f}% 할인")
        elif pct >= 15:
            discount_points = 10
            reasons.append(f"💸 {pct:.0f}% 할인")

    if primary.savings_usd is not None:
        sav = primary.savings_usd
        sav_pts = 0
        if sav >= 200:
            sav_pts = 26
        elif sav >= 100:
            sav_pts = 18
        elif sav >= 50:
            sav_pts = 12
        elif sav >= 25:
            sav_pts = 6
        if sav_pts > discount_points:
            discount_points = sav_pts
            reasons.append(f"💵 ${sav:,.0f} 즉시 할인")

    score += discount_points

    # 7. Historical low / price-drop verification
    if _ALL_TIME_LOW_RE.search(full_text):
        deal.is_historical_low = True
        score += 25
        reasons.append("🏆 역대 최저가(All-Time Low) 언급")
    elif _RECENT_LOW_RE.search(full_text):
        deal.is_historical_low = True
        score += 16
        reasons.append("📉 최근/연중 최저가 언급")

    # Internal price-history drop check (if we saw this exact item at a higher price earlier)
    if (
        previous_low_usd is not None
        and primary.price_usd is not None
        and previous_low_usd > 0
        and primary.price_usd <= previous_low_usd * 0.90
    ):
        drop_pct = (1.0 - primary.price_usd / previous_low_usd) * 100.0
        score += 18
        reasons.append(f"📉 이전 포착가(${previous_low_usd:,.2f}) 대비 {drop_pct:.0f}% 추가 하락")

    # 8. User VIP watch keywords (strictly on title to avoid "compatible with AirPods/MacBook" false positives)
    title_and_cat = f"{primary.title} {primary.category or ''}".lower()
    matched_watch = [
        kw for kw in config.filters.watch_keywords if _contains_keyword(title_lower, kw)
    ]
    if matched_watch:
        deal.is_watch_match = True
        deal.matched_watch_keywords = matched_watch[:3]
        score += 24
        reasons.append(f"🚨 관심 키워드 매칭 ({', '.join(deal.matched_watch_keywords)})")

    is_tech = primary.category_hint in ("IT/가전", "IT/PC") or any(
        _contains_keyword(title_and_cat, kw) for kw in config.filters.boost_keywords
    )
    if is_tech:
        deal.is_tech_category = True
        score += 12
        if not matched_watch:
            reasons.append("💻 IT/PC/가전 관심 분야")

    # 9. Condition & roundup penalties (filter fake hype / multi-item roundup hubs)
    for kw in config.filters.condition_penalty_keywords:
        if _contains_keyword(title_lower, kw):
            deal.condition_warning = kw
            score -= 15
            reasons.append(f"⚠️ 상품 상태 주의 ({kw})")
            break

    if _ROUNDUP_TITLE_RE.search(title_lower):
        score -= 40
        deal.is_watch_match = False
        reasons.append("⚠️ 단일 상품이 아닌 모음/허브 기사 (감점)")
    elif primary.price_usd is None and primary.discount_pct is None and primary.savings_usd is None:
        score -= 22

    # 10. US 110-120V voltage heads-up for AC/high-wattage appliances
    if _US_VOLTAGE_APPLIANCE_RE.search(title_and_cat) and "usb" not in title_lower and "battery" not in title_lower:
        deal.voltage_warning = "⚡ 미국 110~120V 전압 확인 필요 (프리볼트/변압기 체크)"

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
