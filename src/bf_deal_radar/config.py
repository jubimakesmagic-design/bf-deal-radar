"""Configuration loader supporting YAML and .env files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from bf_deal_radar.models import AppConfig, FilterConfig, LLMConfig, NaverShoppingConfig, SourceConfig
from bf_deal_radar.security import register_secrets


def load_dotenv(env_path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE lines from .env without overriding existing env vars."""
    path = Path(env_path)
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_optional_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    return float(val)


def _parse_optional_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    return int(val)


def load_config(config_path: str | Path = "config.yaml", env_path: str | Path = ".env") -> AppConfig:
    """Load configuration from YAML file and environment variables."""
    load_dotenv(env_path)

    path = Path(config_path)
    data: dict[str, Any] = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    radar = data.get("radar", {}) or {}
    filters_raw = data.get("filters", {}) or {}
    llm_raw = data.get("llm", {}) or {}
    naver_raw = data.get("naver_shopping", {}) or {}
    sources_raw = data.get("sources", []) or []

    sources = [
        SourceConfig(
            id=str(item["id"]),
            name=str(item["name"]),
            type=str(item.get("type", "generic")),
            url=str(item["url"]),
            enabled=bool(item.get("enabled", True)),
            base_score=int(item.get("base_score", 12)),
            category_hint=str(item.get("category_hint", "핫딜")),
            region=str(item.get("region", "US")).upper(),
        )
        for item in sources_raw
    ]

    filters = FilterConfig(
        watch_keywords=[str(k).lower() for k in filters_raw.get("watch_keywords", [])],
        boost_keywords=[str(k).lower() for k in filters_raw.get("boost_keywords", [])],
        exclude_keywords=[str(k).lower() for k in filters_raw.get("exclude_keywords", [])],
        kr_exclude_keywords=[str(k).lower() for k in filters_raw.get("kr_exclude_keywords", [])],
        condition_penalty_keywords=[
            str(k).lower() for k in filters_raw.get("condition_penalty_keywords", [])
        ],
    )

    llm = LLMConfig(
        enabled=bool(llm_raw.get("enabled", True)),
        max_calls_per_run=int(llm_raw.get("max_calls_per_run", 8)),
        timeout_seconds=int(llm_raw.get("timeout_seconds", 15)),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
    )

    naver = NaverShoppingConfig(
        enabled=bool(naver_raw.get("enabled", True)),
        client_id=os.getenv("NAVER_CLIENT_ID", "").strip(),
        client_secret=os.getenv("NAVER_CLIENT_SECRET", "").strip(),
        max_calls_per_run=int(naver_raw.get("max_calls_per_run", 20)),
        min_candidate_score=int(naver_raw.get("min_candidate_score", 18)),
        cache_ttl_hours=int(naver_raw.get("cache_ttl_hours", 6)),
        timeout_seconds=float(naver_raw.get("timeout_seconds", 6.0)),
    )

    min_score_env = os.getenv("RADAR_MIN_SCORE", "").strip()
    max_alerts_env = os.getenv("RADAR_MAX_ALERTS", "").strip()
    rate_env = os.getenv("USD_KRW_RATE", "").strip()

    cfg = AppConfig(
        min_score=int(min_score_env) if min_score_env else int(radar.get("min_score", 65)),
        kr_min_score=int(min_score_env) if min_score_env else int(radar.get("kr_min_score", 65)),
        max_alerts_per_run=(
            int(max_alerts_env) if max_alerts_env else int(radar.get("max_alerts_per_run", 8))
        ),
        max_age_hours=int(radar.get("max_age_hours", 18)),
        price_history_days=int(radar.get("price_history_days", 60)),
        min_price_usd=_parse_optional_float(radar.get("min_price_usd", 5.0)),
        max_price_usd=_parse_optional_float(radar.get("max_price_usd")),
        min_price_krw=_parse_optional_int(radar.get("min_price_krw", 3000)),
        us_customs_threshold_usd=float(radar.get("us_customs_threshold_usd", 200.0)),
        fallback_usd_krw=float(radar.get("fallback_usd_krw", 1400.0)),
        override_usd_krw=_parse_optional_float(rate_env),
        db_path=str(os.getenv("RADAR_DB_PATH") or radar.get("db_path", "data/radar.db")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        sources=sources,
        filters=filters,
        llm=llm,
        naver=naver,
    )
    register_secrets(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        cfg.llm.gemini_api_key,
        cfg.llm.openai_api_key,
        cfg.naver.client_id,
        cfg.naver.client_secret,
    )
    return cfg
