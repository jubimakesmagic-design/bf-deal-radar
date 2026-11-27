"""Telegram Bot API notifier with scannable HTML formatting and price-history buttons."""

from __future__ import annotations

import html
import logging
import time
from urllib.parse import quote_plus

import httpx

from bf_deal_radar.models import ScoredDeal
from bf_deal_radar.parser import clean_kr_product_query, clean_product_query
from bf_deal_radar.security import is_safe_http_url, redact_secrets, register_secrets
from bf_deal_radar.verifier import dedupe_source_labels

logger = logging.getLogger(__name__)


def _score_badge(deal: ScoredDeal) -> str:
    if deal.score >= 90:
        return "🔥🔥🔥"
    if deal.is_watch_match or deal.score >= 80:
        return "🔥🔥"
    if deal.score >= 70:
        return "🔥"
    return "⚡"


def _tier_label(deal: ScoredDeal) -> str:
    if deal.llm and deal.llm.verdict:
        return deal.llm.verdict
    if deal.score >= 90:
        return "역대급 초특가 대란"
    if deal.score >= 80 or deal.is_historical_low:
        return "찐할인 역대가급"
    if deal.score >= 70:
        return "강력 할인 포착"
    return "인기 핫딜 포착"


def format_telegram_html(deal: ScoredDeal) -> str:
    """Format a ScoredDeal (US overseas or KR domestic) into a mobile-friendly Telegram HTML message."""
    p = deal.primary
    badge = _score_badge(deal)
    tier = html.escape(_tier_label(deal))
    region_tag = "🇰🇷 국내" if deal.is_domestic_kr else "🇺🇸 직구"
    cat_tag = html.escape(deal.category_label_ko or ("💻 IT/가전" if deal.is_tech_category else f"🛍️ {p.category_hint}"))

    lines: list[str] = [
        f"{badge} <b>[{region_tag} · {deal.score}점] {tier}</b> | {cat_tag}",
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
    if deal.is_domestic_kr:
        if p.price_krw is not None:
            price_parts.append(f"💰 <b>{p.price_krw:,}원</b>")
        if p.shipping_label:
            price_parts.append(f"🚚 {html.escape(p.shipping_label)}")
        if p.discount_pct is not None:
            price_parts.append(f"🔥 <b>{p.discount_pct:.0f}% 할인</b>")
    else:
        if p.price_usd is not None:
            if deal.customs:
                price_parts.append(f"💰 <b>${p.price_usd:,.2f}</b> (약 {deal.customs.price_krw:,}원)")
            else:
                price_parts.append(f"💰 <b>${p.price_usd:,.2f}</b>")
        if p.original_price_usd is not None and (p.price_usd is None or p.original_price_usd > p.price_usd):
            price_parts.append(f"<s>${p.original_price_usd:,.2f}</s>")

        savings_krw_note = ""
        if p.savings_usd is not None and deal.customs and deal.customs.usd_krw_rate > 0:
            sav_krw = int(round(p.savings_usd * deal.customs.usd_krw_rate / 1000.0) * 1000)
            if sav_krw >= 5000:
                savings_krw_note = f" (약 {sav_krw:,}원 절약)"

        if p.discount_pct is not None:
            price_parts.append(f"🔥 <b>{p.discount_pct:.0f}% OFF</b>{savings_krw_note}")
        elif p.savings_usd is not None:
            price_parts.append(f"💵 <b>${p.savings_usd:,.0f} 할인</b>{savings_krw_note}")

    if price_parts:
        lines.append("  |  ".join(price_parts))

    # Retailer & Sources line (dedupe repeated platform variants like 다사자 쿠팡 + 다사자 BEST)
    meta_parts: list[str] = []
    if p.retailer:
        meta_parts.append(f"🏪 {html.escape(p.retailer)}")
    if deal.merged_sources:
        display_sources = dedupe_source_labels(deal.merged_sources)[:3]
        meta_parts.append(f"📡 {html.escape(', '.join(display_sources))}")
    if meta_parts:
        lines.append("  |  ".join(meta_parts))

    # Community engagement summary for Korean deals
    if deal.is_domestic_kr:
        eng: list[str] = []
        if p.thumb_score:
            eng.append(f"👍 추천 {p.thumb_score}")
        if p.comment_count:
            eng.append(f"💬 댓글 {p.comment_count}")
        if p.view_count:
            eng.append(f"👀 조회 {p.view_count:,}")
        if eng:
            lines.append(" · ".join(eng))

    if deal.customs:
        lines.append(html.escape(deal.customs.label))
    if deal.voltage_warning:
        lines.append(html.escape(deal.voltage_warning))
    if deal.bulky_warning:
        lines.append(html.escape(deal.bulky_warning))
    if deal.membership_warning:
        lines.append(html.escape(deal.membership_warning))

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
    """Build Telegram Bot API sendMessage JSON payload with validated inline buttons."""
    p = deal.primary
    row1: list[dict[str, str]] = []
    if deal.is_domestic_kr:
        if p.buy_url and is_safe_http_url(p.buy_url):
            row1.append({"text": "🛒 바로 구매하기", "url": p.buy_url})
            if is_safe_http_url(p.url):
                row1.append({"text": "💬 원글/댓글", "url": p.url})
        elif is_safe_http_url(p.url):
            row1.append({"text": "🛒 핫딜 원글 보기", "url": p.url})
    elif is_safe_http_url(p.url):
        row1.append({"text": "🛒 딜 바로가기", "url": p.url})

    row2: list[dict[str, str]] = []
    if deal.is_domestic_kr:
        kr_q = quote_plus(clean_kr_product_query(p.title))
        row2.append({"text": "🔍 네이버 최저가 비교", "url": f"https://search.shopping.naver.com/search/all?query={kr_q}"})
        row2.append({"text": "📊 다나와 시세", "url": f"https://search.danawa.com/dsearch.php?query={kr_q}"})
    elif deal.camel_url and deal.keepa_url and is_safe_http_url(deal.camel_url) and is_safe_http_url(deal.keepa_url):
        row2.append({"text": "📈 가격추이 (CCC)", "url": deal.camel_url})
        row2.append({"text": "📊 Keepa 차트", "url": deal.keepa_url})
    else:
        query = quote_plus(clean_product_query(p.title))
        row2.append({"text": "📈 가격추이 검색 (CCC)", "url": f"https://camelcamelcamel.com/search?sq={query}"})

    keyboard = [r for r in (row1, row2) if r]

    return {
        "chat_id": chat_id,
        "text": format_telegram_html(deal),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": {"inline_keyboard": keyboard},
    }


class TelegramNotifier:
    """Sends deal alerts via Telegram Bot API without leaking bot credentials in logs."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 12.0) -> None:
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self.timeout = timeout
        self._api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        register_secrets(bot_token, chat_id)

    def __repr__(self) -> str:
        return "TelegramNotifier(bot_token='[REDACTED]', chat_id='[REDACTED]')"

    def send_deal(self, deal: ScoredDeal) -> bool:
        payload = build_telegram_payload(self._chat_id, deal)
        return self._post(payload)

    def send_test_message(self, usd_krw_rate: float) -> bool:
        text = (
            "✅ <b>BF Deal Radar 연결 성공!</b>\n\n"
            f"💱 현재 적용 환율: <b>1 USD = {usd_krw_rate:,.1f}원</b>\n"
            "📡 해외직구 · 국내 핫딜 찐할인 포착 시 이 채팅으로 바로 알림이 도착합니다."
        )
        payload = {
            "chat_id": self._chat_id,
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
                    if resp.status_code >= 400:
                        # Log only HTTP status and Telegram description — NEVER log request URL containing bot token
                        err_desc = ""
                        try:
                            err_desc = str(resp.json().get("description", ""))
                        except Exception:
                            err_desc = resp.reason_phrase
                        logger.error(
                            "Telegram API rejected request (HTTP %d): %s",
                            resp.status_code,
                            redact_secrets(err_desc, (self._bot_token, self._chat_id)),
                        )
                        return False
                    data = resp.json()
                    if not data.get("ok"):
                        desc = redact_secrets(str(data.get("description", "unknown error")), (self._bot_token, self._chat_id))
                        logger.error("Telegram API error: %s", desc)
                        return False
                    return True
                except httpx.RequestError as exc:
                    # Scrub any URL/token from httpx transport errors
                    safe_msg = redact_secrets(str(exc), (self._bot_token, self._chat_id))
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    logger.error("Telegram network error (%s): %s", type(exc).__name__, safe_msg)
                    return False
                except Exception as exc:
                    safe_msg = redact_secrets(str(exc), (self._bot_token, self._chat_id))
                    logger.error("Unexpected Telegram error (%s): %s", type(exc).__name__, safe_msg)
                    return False
        return False


def discover_chat_ids(bot_token: str, timeout: float = 10.0) -> list[dict[str, str]]:
    """Safely discover Telegram chat IDs that recently messaged the bot (via getUpdates)."""
    token = (bot_token or "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN must be provided.")
    register_secrets(token)
    url = f"https://api.telegram.org/bot{token}/getUpdates"

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise RuntimeError(f"Telegram API error (HTTP {resp.status_code})")
            payload = resp.json()
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error: {redact_secrets(str(exc), (token,))}") from None

    if not payload.get("ok"):
        desc = redact_secrets(str(payload.get("description", "unknown")), (token,))
        raise RuntimeError(f"Telegram API returned error: {desc}")

    chats: dict[str, dict[str, str]] = {}
    for update in payload.get("result", []):
        msg = (
            update.get("message")
            or update.get("channel_post")
            or update.get("edited_message")
            or {}
        )
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            cid_str = str(cid)
            chats[cid_str] = {
                "chat_id": cid_str,
                "type": str(chat.get("type", "unknown")),
                "title": str(
                    chat.get("title")
                    or chat.get("username")
                    or chat.get("first_name")
                    or "Chat"
                ),
            }
    return list(chats.values())
