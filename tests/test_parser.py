"""Unit tests for price, discount, ASIN, coupon, and retailer parsing."""

from bf_deal_radar.parser import (
    extract_asin,
    extract_coupon_code,
    extract_price_info,
    extract_retailer,
    normalize_title_tokens,
)


def test_price_ignores_minimum_shipping_threshold():
    info = extract_price_info(
        "Panasonic Eneloop C Size Battery Adapters for $6 + free shipping w/ $35",
        "That's its best price, and you'd pay around double elsewhere.",
    )
    assert info.price_usd == 6.0
    assert info.discount_pct == 50.0


def test_price_does_not_confuse_dollar_off_with_item_price():
    info = extract_price_info(
        "The MacBook Neo is receiving a rare $100 discount for Black Friday",
        "Save $100 on the MacBook Neo, currently discounted to $899.",
    )
    assert info.price_usd == 899.0
    assert info.savings_usd == 100.0
    assert info.original_price_usd == 999.0
    assert info.discount_pct is not None and 9.5 <= info.discount_pct <= 10.5


def test_price_calculates_discount_from_original_price():
    info = extract_price_info(
        'Samsung 990 PRO 4TB NVMe SSD $249.99 (was $399.99)',
        "",
    )
    assert info.price_usd == 249.99
    assert info.original_price_usd == 399.99
    assert info.savings_usd == 150.0
    assert info.discount_pct == 37.5


def test_extract_explicit_pct_off():
    info = extract_price_info(
        "Kasa Smart Plug Mini 4-Pack for $28 + free shipping",
        "This 4-pack of Kasa smart plugs is 37% off at Amazon and matches its Amazon all-time low.",
        explicit_price=28.0,
    )
    assert info.price_usd == 28.0
    assert info.discount_pct == 37.0


def test_extract_asin_from_amazon_urls():
    assert extract_asin("https://www.amazon.com/gp/product/B0GJTFXNRX/ref=ox_sc_act?th=1") == "B0GJTFXNRX"
    assert extract_asin("Check https://amazon.com/Some-Product/dp/B091FXQQMQ?tag=xyz") == "B091FXQQMQ"
    assert extract_asin("Deal at https://link.amazon/B0GR6F79MT") == "B0GR6F79MT"
    assert extract_asin("https://www.bestbuy.com/site/something") is None


def test_ignores_vague_up_to_discounts():
    info = extract_price_info(
        "Official Amazon YETI Black Friday deals: Tumblers, coolers up to 40% off (Up to $200 off)",
        "",
    )
    assert info.discount_pct is None
    assert info.savings_usd is None


def test_extract_coupon_code():
    sd_html = (
        "apply code <span data-role='couponCode'><span class='code'>"
        "<strong>XZIZGAKD</strong></span></span> at checkout"
    )
    assert extract_coupon_code(sd_html, "") == "XZIZGAKD"
    assert extract_coupon_code("", "LG C6 OLED $1076 w/ code CYBERFINDS20 + Free S/H") == "CYBERFINDS20"
    assert extract_coupon_code("", "Apply code FREE shipping at checkout") is None


def test_extract_retailer():
    assert extract_retailer("AirTag $18", "", "https://www.amazon.com/dp/B0GJTFXNRX") == "Amazon"
    assert extract_retailer("OLED TV", "Best Buy offers...", "https://www.dealnews.com/123.html") == "Best Buy"
    assert extract_retailer("Deal", "", "", explicit="B&H Photo") == "B&H Photo"


def test_normalize_title_tokens():
    tokens = normalize_title_tokens('Prime Members: Sony WH-1000XM5 Headphones $248 + Free S&H')
    assert "sony" in tokens
    assert "wh" in tokens
    assert "1000xm5" in tokens
    assert "prime" not in tokens
    assert "free" not in tokens
