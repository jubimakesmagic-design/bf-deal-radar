"""Core data models for bf-deal-radar (Overseas US + Korean Domestic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class SourceConfig:
    id: str
    name: str
    type: str
    url: str
    enabled: bool = True
    base_score: int = 12
    category_hint: str = "핫딜"
    region: str = "US"  # "US" (overseas) or "KR" (domestic Korea)


@dataclass(slots=True)
class FilterConfig:
    watch_keywords: list[str] = field(default_factory=list)
    boost_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    kr_exclude_keywords: list[str] = field(default_factory=list)
    condition_penalty_keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = True
    max_calls_per_run: int = 8
    timeout_seconds: int = 15
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str = field(default="", repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.gemini_api_key or self.openai_api_key)


@dataclass(slots=True)
class NaverConfig:
    enabled: bool = True
    max_calls_per_run: int = 10
    timeout_seconds: int = 8
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.client_id and self.client_secret)


@dataclass(slots=True)
class AppConfig:
    min_score: int = 66
    kr_min_score: int = 62
    max_alerts_per_run: int = 8
    max_age_hours: int = 18
    min_price_usd: float | None = 5.0
    max_price_usd: float | None = None
    min_price_krw: int | None = 3000
    us_customs_threshold_usd: float = 200.0
    fallback_usd_krw: float = 1400.0
    override_usd_krw: float | None = None
    db_path: str = "data/radar.db"
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = field(default="", repr=False)
    sources: list[SourceConfig] = field(default_factory=list)
    filters: FilterConfig = field(default_factory=FilterConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    naver: NaverConfig = field(default_factory=NaverConfig)


@dataclass(slots=True)
class RawDeal:
    """Raw deal item collected from an external overseas or Korean feed."""

    source_id: str
    source_name: str
    source_type: str
    source_base_score: int
    category_hint: str
    external_id: str
    title: str
    url: str
    buy_url: str | None = None
    region: str = "US"
    description: str = ""
    published_at: datetime | None = None
    retailer: str | None = None
    price_usd: float | None = None
    original_price_usd: float | None = None
    price_krw: int | None = None
    shipping_label: str | None = None
    discount_pct: float | None = None
    savings_usd: float | None = None
    thumb_score: int | None = None
    downvote_count: int | None = None
    comment_count: int | None = None
    view_count: int | None = None
    is_sold_out: bool = False
    staff_pick: bool = False
    coupon_code: str | None = None
    asin: str | None = None
    image_url: str | None = None
    category: str | None = None
    raw_html: str = ""


@dataclass(slots=True)
class CustomsEstimate:
    """Korean import customs/VAT estimate for US direct purchase."""

    usd_krw_rate: float
    price_krw: int
    is_duty_free: bool
    threshold_usd: float
    estimated_tax_rate_low: float = 0.0
    estimated_tax_rate_high: float = 0.0
    estimated_total_krw_low: int = 0
    estimated_total_krw_high: int = 0
    label: str = ""


@dataclass(slots=True)
class NaverMarketCheck:
    """Optional Naver Shopping live market comparison for Korean domestic deals."""

    market_lowest_krw: int
    market_median_krw: int
    savings_vs_median_pct: float
    sample_count: int
    verdict_label: str = ""


@dataclass(slots=True)
class LLMEnrichment:
    """Optional AI-generated Korean summary and real-discount sanity check."""

    korean_title: str = ""
    summary_ko: str = ""
    verdict: str = ""  # e.g., "찐역대가", "좋은할인", "무난함", "주의필요"
    score_adjustment: int = 0


@dataclass(slots=True)
class ScoredDeal:
    """Verified, deduplicated, and scored deal ready for notification."""

    canonical_key: str
    primary: RawDeal
    merged_sources: list[str] = field(default_factory=list)
    merged_urls: dict[str, str] = field(default_factory=dict)
    score: int = 0
    is_watch_match: bool = False
    matched_watch_keywords: list[str] = field(default_factory=list)
    is_tech_category: bool = False
    category_label_ko: str = "🛍️ 핫딜"
    is_historical_low: bool = False
    condition_warning: str | None = None
    voltage_warning: str | None = None
    bulky_warning: str | None = None
    membership_warning: str | None = None
    reasons: list[str] = field(default_factory=list)
    customs: CustomsEstimate | None = None
    naver_market: NaverMarketCheck | None = None
    llm: LLMEnrichment | None = None
    excluded_reason: str | None = None

    @property
    def is_domestic_kr(self) -> bool:
        return self.primary.region.upper() == "KR"

    @property
    def camel_url(self) -> str | None:
        if self.primary.asin:
            return f"https://camelcamelcamel.com/product/{self.primary.asin}"
        return None

    @property
    def keepa_url(self) -> str | None:
        if self.primary.asin:
            return f"https://keepa.com/#!product/1-{self.primary.asin}"
        return None
