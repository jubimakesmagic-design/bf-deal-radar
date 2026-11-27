"""Unit tests for static dashboard export (including XSS safety), US 110V warning, and Chat ID discovery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from bf_deal_radar.dashboard import build_dashboard_payload, export_dashboard, render_dashboard_html
from bf_deal_radar.models import AppConfig, FilterConfig, RawDeal, ScoredDeal
from bf_deal_radar.notifier.telegram import discover_chat_ids, format_telegram_html
from bf_deal_radar.verifier import verify_and_score


def test_us_voltage_warning_on_ac_appliance_but_not_usb_gadget():
    now = datetime.now(timezone.utc)
    cfg = AppConfig(min_score=50, max_age_hours=24, filters=FilterConfig())

    heater = ScoredDeal(
        canonical_key="url:heater",
        primary=RawDeal(
            source_id="cnet_deals",
            source_name="CNET Deals",
            source_type="generic",
            source_base_score=13,
            category_hint="가전/홈",
            external_id="h1",
            title="Dreo Space Heater Just $98 for Black Friday (35% off)",
            url="https://example.com/heater",
            published_at=now,
            price_usd=98.0,
            discount_pct=35.0,
        ),
        merged_sources=["CNET Deals"],
    )
    verify_and_score(heater, cfg, usd_krw_rate=1350.0, now=now)
    assert heater.voltage_warning is not None
    assert "110~120V" in heater.voltage_warning
    assert "110~120V" in format_telegram_html(heater)

    usb_ssd = ScoredDeal(
        canonical_key="url:ssd",
        primary=RawDeal(
            source_id="dealnews_comp",
            source_name="DealNews Computers",
            source_type="dealnews",
            source_base_score=14,
            category_hint="IT/PC",
            external_id="s1",
            title="Samsung T7 2TB Portable USB-C SSD for $119",
            url="https://example.com/ssd",
            published_at=now,
            price_usd=119.0,
            discount_pct=30.0,
        ),
        merged_sources=["DealNews Computers"],
    )
    verify_and_score(usb_ssd, cfg, usd_krw_rate=1350.0, now=now)
    assert usb_ssd.voltage_warning is None


def test_for_the_first_time_triggers_historical_low():
    now = datetime.now(timezone.utc)
    cfg = AppConfig(min_score=50, max_age_hours=24, filters=FilterConfig())
    deal = ScoredDeal(
        canonical_key="url:beats",
        primary=RawDeal(
            source_id="ign_deals",
            source_name="IGN Deals",
            source_type="generic",
            source_base_score=13,
            category_hint="IT/가전",
            external_id="b1",
            title="Beats Studio Pro Headphones Drop Below $100 For the First Time",
            url="https://example.com/beats",
            published_at=now,
            price_usd=99.99,
        ),
        merged_sources=["IGN Deals"],
    )
    verify_and_score(deal, cfg, usd_krw_rate=1350.0, now=now)
    assert deal.is_historical_low is True
    assert deal.score >= 35


def test_dashboard_export_escapes_script_breakout_and_strips_unsafe_urls(tmp_path: Path):
    evil_deal = ScoredDeal(
        canonical_key="url:evil",
        primary=RawDeal(
            source_id="test",
            source_name="Test",
            source_type="generic",
            source_base_score=10,
            category_hint="IT",
            external_id="x1",
            title="Deal </script><script>alert(1)</script> $50",
            url="javascript:alert(1)",
            published_at=datetime.now(timezone.utc),
            price_usd=50.0,
        ),
        merged_sources=["Test"],
        score=75,
    )

    html_path, json_path = export_dashboard([evil_deal], usd_krw_rate=1350.0, output_dir=tmp_path)
    assert html_path.is_file()
    assert json_path.is_file()

    html_content = html_path.read_text(encoding="utf-8")
    assert "</script><script>alert(1)" not in html_content
    assert "<\\/script>" in html_content

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["deals"][0]["url"] == ""
    assert data["usd_krw_rate"] == 1350.0


def test_discover_chat_ids_parses_updates_and_redacts_errors(monkeypatch):
    def _fake_get(self, url):
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "ok": True,
                "result": [
                    {"message": {"chat": {"id": 123456789, "type": "private", "first_name": "Jubi"}}},
                    {"channel_post": {"chat": {"id": -100112233, "type": "channel", "title": "BF Alerts"}}},
                ],
            },
        )

    monkeypatch.setattr(httpx.Client, "get", _fake_get)
    found = discover_chat_ids("999888777:AAFakeTokenForTesting1234567890")
    assert len(found) == 2
    assert {c["chat_id"] for c in found} == {"123456789", "-100112233"}

    # Ensure network error never leaks the bot token
    token = "999888777:AAVerySecretBotTokenMustNotLeak1234"

    def _boom_get(self, url):
        req = httpx.Request("GET", url)
        raise httpx.ConnectError(f"Connection failed for {url}", request=req)

    monkeypatch.setattr(httpx.Client, "get", _boom_get)
    with pytest.raises(RuntimeError) as exc_info:
        discover_chat_ids(token)
    assert token not in str(exc_info.value)
