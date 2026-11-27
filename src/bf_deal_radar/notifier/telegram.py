"""Telegram Bot API notifier with scannable HTML formatting and price-history buttons."""

from __future__ import annotations

import html
import logging
import time
from urllib.parse import quote_plus

import httpx

from bf_deal_radar.models import ScoredDeal

logger = logging.getLogger(__name__)


def _score_badge(deal: ScoredDeal) -> str:
    if deal.is_watch_match:
        return "🚨🔥"
    if deal.score >= 85:
        return "🔥🔥"
    if deal.score >= 70:
        return "🔥"
    return "⚡"


def _tier_label(deal: ScoredDeal) -> str:
    if deal.llm and deal.llm.verdict:
        return deal.llm.verdict
    if deal.is_historical_low or deal.score >= 85:
        return "찐할인 역대가급"
    if deal.score >= 70:
        return "강력 할인 포착"
    return "인기 핫딜 포착"


def format_telegram_html(deal: ScoredDeal) -> str:
    """Format a ScoredDeal into a mobile-friendly Telegram HTML message."""
    p = deal.primary
    badge = _score_badge(deal)
    tier = html.escape(_tier_label(deal))
    cat_tag = "💻 IT/가전" if deal.is_tech_category else f"🛍️ {html.escape(p.category_hint)}"

    lines: list[str] = [
        f"{badge} <b>[{deal.score}점] {tier}</b> | {cat_tag}",
        "",
    ]

    if deal.llm and deal.llm.korean_title:
        lines.append(f"<b>{html.escape(deal.llm.korean_title)}</b>")
        lines.append(f"<i>{html.escape(p.title)}</i>")
    else:
        lines.append(f"<b>{html.escape(p.title)}</b>")

    lines.append("")

    # Price & Discount line
    price_parts: list[str] = []
    if p.price_usd is not None:
        if deal.customs:
            price_parts.append(f"💰 <b>${p.price_usd:,.2f}</b> (약 {deal.customs.price_krw:,}원)")
        else:
            price_parts.append(f"💰 <b>${p.price_usd:,.2f}</b>")
    if p.original_price_usd is not None and (p.price_usd is None or p.original_price_usd > p.price_usd):
        price_parts.append(f"<s>${p.original_price_usd:,.2f}</s>")
    if p.discount_pct is not None:
        price_parts.append(f"🔥 <b>{p.discount_pct:.0f}% OFF</b>")
    elif p.savings_usd is not None:
        price_parts.append(f"💵 <b>${p.savings_usd:,.0f} 할인</b>")

    if price_parts:
        lines.append("  |  ".join(price_parts))

    # Retailer & Customs line
    meta_parts: list[str] = []
    if p.retailer:
        meta_parts.append(f"🏪 {html.escape(p.retailer)}")
    if deal.merged_sources:
        meta_parts.append(f"📡 {html.escape(', '.join(deal.merged_sources[:3]))}")
    if meta_parts:
        lines.append("  |  ".join(meta_parts))

    if deal.customs:
        lines.append(html.escape(deal.customs.label))

    if p.coupon_code:
        lines.append(f"🎟️ 쿠폰 코드: <code>{html.escape(p.coupon_code)}</code>")

    # Verification reasons
    if deal.reasons:
        lines.append("")
        lines.append("🔎 <b>할인 검증 근거</b>")
        for reason in deal.reasons[:5]:
            lines.append(f"• {html.escape(reason)}")

    # Optional LLM summary
    if deal.llm and deal.llm.summary_ko:
        lines.append("")
        lines.append(f"🤖 <i>{html.escape(deal.llm.summary_ko)}</i>")

    return "\n".join(lines)


def build_telegram_payload(chat_id: str, deal: ScoredDeal) -> dict:
    """Build Telegram Bot API sendMessage JSON payload with inline price-check buttons."""
    row1 = [{"text": "🛒 딜 바로가기", "url": deal.primary.url}]
    row2: list[dict[str, str]] = []

    if deal.camel_url and deal.keepa_url:
        row2.append({"text": "📈 가격추이 (CCC)", "url": deal.camel_url})
        row2.append({"text": "📊 Keepa 차트", "url": deal.keepa_url})
    else:
        query = quote_plus(deal.primary.title[:70])
        row2.append({"text": "📈 가격추이 검색 (CCC)", "url": f"https://camelcamelcamel.com/search?sq={query}"})

    keyboard = [row1, row2] if row2 else [row1]

    return {
        "chat_id": chat_id,
        "text": format_telegram_html(deal),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": {"inline_keyboard": keyboard},
    }


class TelegramNotifier:
    """Sends deal alerts via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 12.0) -> None:
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_deal(self, deal: ScoredDeal) -> bool:
        payload = build_telegram_payload(self.chat_id, deal)
        return self._post(payload)

    def send_test_message(self, usd_krw_rate: float) -> bool:
        text = (
            "✅ <b>BF Deal Radar 연결 성공!</b>\n\n"
            f"💱 현재 적용 환율: <b>1 USD = {usd_krw_rate:,.1f}원</b>\n"
            "📡 해외직구 블랙프라이데이 찐할인 포착 시 이 채팅으로 바로 알림이 도착합니다."
        )
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        with httpx.Client(timeout=self.timeout) as client:
            for attempt in range(2):
                try:
                    resp = client.post(self._api_url, json=payload)
                    if resp.status_code == 429:
                        retry_after = int(resp.json().get("parameters", {}).get("retry_after", 3))
                        logger.warning("Telegram rate limited; sleeping %ds", retry_after)
                        time.sleep(min(retry_after, 10))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("ok"):
                        logger.error("Telegram API error: %s", data)
                        return False
                    return True
                except Exception as exc:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    logger.error("Failed to send Telegram alert: %s", exc)
                    return False
        return False
