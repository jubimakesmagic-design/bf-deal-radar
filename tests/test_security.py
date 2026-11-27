"""Security regression tests ensuring tokens, API keys, and unsafe URLs cannot leak."""

from __future__ import annotations

import logging

import httpx
import pytest

from bf_deal_radar.llm import enrich_deals_with_llm
from bf_deal_radar.models import AppConfig, LLMConfig, RawDeal, ScoredDeal
from bf_deal_radar.notifier.telegram import TelegramNotifier, build_telegram_payload
from bf_deal_radar.security import (
    SecretRedactingFilter,
    clear_registered_secrets,
    is_safe_http_url,
    redact_secrets,
    register_secrets,
)


def test_config_and_notifier_repr_never_expose_secrets():
    token = "987654321:AAFakeSuperSecretBotToken1234567890"
    chat_id = "-100999888777"
    gemini_key = "AIzaSyFakeGeminiKeyValue12345678901234"
    openai_key = "sk-FakeOpenAiSecretKey1234567890abcdef"

    cfg = AppConfig(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        llm=LLMConfig(gemini_api_key=gemini_key, openai_api_key=openai_key),
    )
    cfg_repr = repr(cfg)
    assert token not in cfg_repr
    assert chat_id not in cfg_repr
    assert gemini_key not in cfg_repr
    assert openai_key not in cfg_repr

    notifier = TelegramNotifier(token, chat_id)
    notifier_repr = repr(notifier)
    assert token not in notifier_repr
    assert chat_id not in notifier_repr
    clear_registered_secrets()


def test_redact_secrets_scrubs_known_patterns_and_registered_values():
    clear_registered_secrets()
    register_secrets("my-custom-chat-id-99999")
    sample = (
        "HTTP error for url 'https://api.telegram.org/bot123456789:AAH_SecretTokenValue1234567890/sendMessage' "
        "chat=my-custom-chat-id-99999 "
        "headers={'Authorization': 'Bearer sk-TestSecretKey1234567890abcd'} "
        "gemini=AIzaSyTestSecretKey1234567890abcdef"
    )
    redacted = redact_secrets(sample)
    assert "AAH_SecretTokenValue1234567890" not in redacted
    assert "my-custom-chat-id-99999" not in redacted
    assert "sk-TestSecretKey1234567890abcd" not in redacted
    assert "AIzaSyTestSecretKey1234567890abcdef" not in redacted
    assert "[REDACTED]" in redacted
    clear_registered_secrets()


def test_logging_filter_scrubs_messages_and_exception_tracebacks(caplog):
    clear_registered_secrets()
    secret = "555666777:AAH_TopSecretTelegramToken999999999"
    register_secrets(secret)

    test_logger = logging.getLogger("bf_deal_radar.test_security")
    redactor = SecretRedactingFilter()
    test_logger.addFilter(redactor)
    try:
        with caplog.at_level(logging.ERROR, logger="bf_deal_radar.test_security"):
            test_logger.error("Request failed for https://api.telegram.org/bot%s/sendMessage", secret)
        assert secret not in caplog.text
        assert "[REDACTED]" in caplog.text
    finally:
        test_logger.removeFilter(redactor)
        clear_registered_secrets()


def test_telegram_error_logs_never_leak_bot_token(caplog, monkeypatch):
    clear_registered_secrets()
    token = "111222333:AAH_NeverLeakThisToken1234567890abc"
    chat_id = "444555666"
    notifier = TelegramNotifier(token, chat_id)

    def _boom_post(self, url, json=None):
        req = httpx.Request("POST", url)
        raise httpx.ConnectTimeout(f"Timed out connecting to {url}", request=req)

    monkeypatch.setattr(httpx.Client, "post", _boom_post)
    monkeypatch.setattr("bf_deal_radar.notifier.telegram.time.sleep", lambda *_: None)

    with caplog.at_level(logging.ERROR, logger="bf_deal_radar.notifier.telegram"):
        ok = notifier.send_test_message(1400.0)

    assert ok is False
    assert token not in caplog.text
    assert chat_id not in caplog.text
    assert "[REDACTED]" in caplog.text
    clear_registered_secrets()


def test_telegram_rejects_unsafe_url_schemes_in_buttons():
    deal = ScoredDeal(
        canonical_key="url:xss",
        primary=RawDeal(
            source_id="test",
            source_name="Test",
            source_type="generic",
            source_base_score=10,
            category_hint="핫딜",
            external_id="1",
            title="Suspicious Deal",
            url="javascript:alert(1)",
        ),
    )
    payload = build_telegram_payload("12345", deal)
    all_urls = [
        btn["url"]
        for row in payload["reply_markup"]["inline_keyboard"]
        for btn in row
    ]
    assert all(url.startswith("https://") for url in all_urls)
    assert not any("javascript:" in url for url in all_urls)


def test_llm_refuses_plaintext_http_remote_endpoint(caplog):
    clear_registered_secrets()
    secret_key = "sk-NeverSendOverPlaintextHttp123456"
    cfg = LLMConfig(
        enabled=True,
        openai_api_key=secret_key,
        openai_base_url="http://evil-remote-host.example.com/v1",
    )
    deal = ScoredDeal(
        canonical_key="url:1",
        primary=RawDeal(
            source_id="s",
            source_name="S",
            source_type="generic",
            source_base_score=10,
            category_hint="IT",
            external_id="1",
            title="Test Deal $100",
            url="https://example.com/deal",
        ),
    )
    with caplog.at_level(logging.WARNING, logger="bf_deal_radar.llm"):
        enrich_deals_with_llm([deal], cfg)

    assert deal.llm is None
    assert secret_key not in caplog.text
    assert "https://" in caplog.text
    clear_registered_secrets()


@pytest.mark.parametrize(
    ("url", "require_https", "allow_localhost", "expected"),
    [
        ("https://api.openai.com/v1", True, True, True),
        ("http://localhost:11434/v1", True, True, True),
        ("http://127.0.0.1:8000/v1", True, True, True),
        ("http://example.com/v1", True, True, False),
        ("javascript:alert(1)", False, False, False),
        ("file:///etc/passwd", False, False, False),
        ("https://www.amazon.com/dp/B0TEST1234", False, False, True),
    ],
)
def test_is_safe_http_url(url, require_https, allow_localhost, expected):
    assert is_safe_http_url(url, require_https=require_https, allow_localhost=allow_localhost) is expected
