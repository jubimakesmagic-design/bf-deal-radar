"""Optional LLM enrichment (Gemini or OpenAI-compatible) for Korean deal summary & sanity check."""

from __future__ import annotations

import json
import logging
import re

import httpx

from bf_deal_radar.models import LLMConfig, LLMEnrichment, ScoredDeal
from bf_deal_radar.security import is_safe_http_url, redact_secrets, register_secrets

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert shopping deal analyst for Korean buyers covering both US overseas deals (Region=US) "
    "and Korean domestic hot deals (Region=KR). Analyze the given deal and respond ONLY with a JSON object containing:\n"
    '{"korean_title": "concise Korean product title (max 45 chars)", '
    '"summary_ko": "1-2 short Korean sentences on whether this discount is genuinely strong; for Region=US mention key import caveats (voltage, shipping, customs) if applicable, and for Region=KR focus on Korean market price value & shipping without mentioning US customs/110V", '
    '"verdict": "one of: 찐역대가, 좋은할인, 무난함, 주의필요", '
    '"score_adjustment": integer between -15 and 10}'
)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _build_user_prompt(deal: ScoredDeal) -> str:
    p = deal.primary
    if deal.is_domestic_kr:
        lines = [
            "Region: KR (Korean domestic hot deal)",
            f"Title: {p.title}",
            f"Sources: {', '.join(deal.merged_sources)}",
            f"Retailer: {p.retailer or 'Unknown'}",
            f"Price KRW: {f'{p.price_krw:,}원' if p.price_krw is not None else 'Unknown'}",
            f"Shipping: {p.shipping_label or 'Unknown'}",
            f"Discount %: {f'{p.discount_pct:.0f}%' if p.discount_pct is not None else 'Unknown'}",
            f"Engagement: upvotes={p.thumb_score or 0}, comments={p.comment_count or 0}, views={p.view_count or 0}",
            f"Community/Rule Signals: {', '.join(deal.reasons) or 'None'}",
            f"Description: {p.description[:350]}",
        ]
    else:
        lines = [
            "Region: US (Overseas direct import)",
            f"Title: {p.title}",
            f"Sources: {', '.join(deal.merged_sources)}",
            f"Retailer: {p.retailer or 'Unknown'}",
            f"Price USD: {f'${p.price_usd:.2f}' if p.price_usd is not None else 'Unknown'}",
            f"Original USD: {f'${p.original_price_usd:.2f}' if p.original_price_usd is not None else 'Unknown'}",
            f"Discount %: {f'{p.discount_pct:.0f}%' if p.discount_pct is not None else 'Unknown'}",
            f"Customs: {deal.customs.label if deal.customs else 'Unknown'}",
            f"Community/Rule Signals: {', '.join(deal.reasons) or 'None'}",
            f"Description: {p.description[:350]}",
        ]
    return "\n".join(lines)


def _call_gemini(client: httpx.Client, config: LLMConfig, prompt: str) -> dict | None:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.gemini_model}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    resp = client.post(
        url,
        headers={"x-goog-api-key": config.gemini_api_key, "Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _extract_json(text)


def _call_openai(client: httpx.Client, config: LLMConfig, prompt: str) -> dict | None:
    if not is_safe_http_url(config.openai_base_url, require_https=True, allow_localhost=True):
        raise ValueError("OPENAI_BASE_URL must use https:// (or http://localhost for local servers)")

    url = f"{config.openai_base_url}/chat/completions"
    payload = {
        "model": config.openai_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    resp = client.post(
        url,
        headers={
            "Authorization": f"Bearer {config.openai_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    return _extract_json(text)


def enrich_deals_with_llm(deals: list[ScoredDeal], config: LLMConfig) -> None:
    """Optionally enrich top candidate deals with Korean summary & AI verdict."""
    if not config.is_configured or not deals:
        return

    register_secrets(config.gemini_api_key, config.openai_api_key)
    candidates = deals[: config.max_calls_per_run]
    with httpx.Client(timeout=config.timeout_seconds) as client:
        for deal in candidates:
            prompt = _build_user_prompt(deal)
            try:
                parsed = (
                    _call_gemini(client, config, prompt)
                    if config.gemini_api_key
                    else _call_openai(client, config, prompt)
                )
                if not parsed:
                    continue
                adj = int(parsed.get("score_adjustment", 0))
                adj = max(-15, min(10, adj))
                deal.llm = LLMEnrichment(
                    korean_title=str(parsed.get("korean_title", "")).strip(),
                    summary_ko=str(parsed.get("summary_ko", "")).strip(),
                    verdict=str(parsed.get("verdict", "")).strip(),
                    score_adjustment=adj,
                )
                deal.score = max(0, min(100, deal.score + adj))
            except Exception as exc:
                safe_err = redact_secrets(
                    str(exc), (config.gemini_api_key, config.openai_api_key)
                )
                logger.warning(
                    "LLM enrichment skipped for '%s' (%s): %s",
                    deal.primary.title[:40],
                    type(exc).__name__,
                    safe_err,
                )
