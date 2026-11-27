"""Live USD->KRW exchange rate lookup and Korean import customs calculator."""

from __future__ import annotations

import logging

import httpx

from bf_deal_radar.models import CustomsEstimate

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"
ER_API_URL = "https://open.er-api.com/v6/latest/USD"


def fetch_usd_krw_rate(
    fallback: float = 1400.0,
    override: float | None = None,
    timeout: float = 6.0,
) -> float:
    """Fetch live USD->KRW rate with automatic fallback chain."""
    if override is not None and override > 0:
        return override

    headers = {"User-Agent": "BFDealRadar/0.1"}

    # Primary: Frankfurter (ECB data, fast & lightweight)
    try:
        resp = httpx.get(FRANKFURTER_URL, headers=headers, timeout=timeout)
        resp.raise_for_status()
        rate = resp.json().get("rates", {}).get("KRW")
        if isinstance(rate, (int, float)) and 800 <= rate <= 2500:
            return float(rate)
    except Exception as exc:
        logger.debug("Frankfurter exchange rate fetch failed: %s", exc)

    # Secondary: open.er-api.com
    try:
        resp = httpx.get(ER_API_URL, headers=headers, timeout=timeout)
        resp.raise_for_status()
        rate = resp.json().get("rates", {}).get("KRW")
        if isinstance(rate, (int, float)) and 800 <= rate <= 2500:
            return float(rate)
    except Exception as exc:
        logger.debug("ER-API exchange rate fetch failed: %s", exc)

    logger.warning("Using fallback USD/KRW rate: %.2f", fallback)
    return fallback


def estimate_customs(
    price_usd: float,
    usd_krw_rate: float,
    threshold_usd: float = 200.0,
    is_tech: bool = False,
) -> CustomsEstimate:
    """Estimate KRW price and Korean US list-clearance customs/VAT status.

    US direct imports under $200 (list clearance / 목록통관) are generally duty/VAT exempt.
    Above $200:
    - Most IT/electronics (laptops, SSDs, monitors, PC parts): 0% duty + 10% VAT (~10%)
    - General consumer goods (clothing, shoes, audio, appliances): ~8% duty + 10% VAT (~18-20%)
    """
    price_krw = int(round(price_usd * usd_krw_rate / 100.0) * 100)
    is_duty_free = price_usd <= threshold_usd

    if is_duty_free:
        return CustomsEstimate(
            usd_krw_rate=usd_krw_rate,
            price_krw=price_krw,
            is_duty_free=True,
            threshold_usd=threshold_usd,
            estimated_total_krw_low=price_krw,
            estimated_total_krw_high=price_krw,
            label=f"✅ 면세 구간 (미국 목록통관 ${threshold_usd:.0f} 이하)",
        )

    if is_tech:
        low_rate, high_rate = 0.10, 0.18
        total_low = int(round(price_krw * (1 + low_rate) / 1000.0) * 1000)
        total_high = int(round(price_krw * (1 + high_rate) / 1000.0) * 1000)
        label = (
            f"⚠️ ${threshold_usd:.0f} 초과 (부가세 ~10% 예상, "
            f"예상 총액 약 {total_low:,}원~{total_high:,}원)"
        )
    else:
        low_rate, high_rate = 0.18, 0.20
        total_low = int(round(price_krw * (1 + low_rate) / 1000.0) * 1000)
        total_high = int(round(price_krw * (1 + high_rate) / 1000.0) * 1000)
        label = (
            f"⚠️ ${threshold_usd:.0f} 초과 (관부가세 ~18~20% 예상, "
            f"예상 총액 약 {total_low:,}원~{total_high:,}원)"
        )

    return CustomsEstimate(
        usd_krw_rate=usd_krw_rate,
        price_krw=price_krw,
        is_duty_free=False,
        threshold_usd=threshold_usd,
        estimated_tax_rate_low=low_rate,
        estimated_tax_rate_high=high_rate,
        estimated_total_krw_low=total_low,
        estimated_total_krw_high=total_high,
        label=label,
    )
