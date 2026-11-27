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


def test_savings_in_savings_not_misparsed_as_item_price_or_fake_50_pct():
    # Exact live regression: 9to5Toys PROLED MacBook Pro ($3,399 -> $3,099) had "$300 in savings"
    # misparsed as price=$300 -> original=$600 -> 50% OFF.
    proled = extract_price_info(
        "Now even lower: New touchscreen PROLED XDR MacBook Pro hits Black Friday low at $300 off",
        "With the main event Amazon Black Friday sale now in full swing today, we are catching some "
        "new price drops on the latest MacBook releases with the new M6 MacBook Pro now dropping "
        "again to $250 off and the most affordable 16-inch PROLED XDR now sitting down at a new "
        "all-time low with a solid $300 in savings .",
    )
    assert proled.price_usd is None
    assert proled.original_price_usd is None
    assert proled.discount_pct is None
    assert proled.savings_usd == 300.0

    # Sibling live regression: "$250 in total savings" + "down by extra $50"
    m6_pro = extract_price_info(
        "New M6 MacBook Pro just dropped again to new Black Friday all-time low at $250 off",
        "Update: 11/27: Amazon has now dropped some additional configurations down by extra $50 "
        "for $250 in total savings and all-time low pricing.",
    )
    assert m6_pro.price_usd is None
    assert m6_pro.original_price_usd is None
    assert m6_pro.discount_pct is None
    assert m6_pro.savings_usd == 250.0

    # When the actual price + "Regularly $3,399" ARE present, parse the real $3,099 / $3,399 (8.8% off)
    proled_full = extract_price_info(
        "Now even lower: New touchscreen PROLED XDR MacBook Pro hits Black Friday low at $300 off",
        "Amazon has now dropped the price on the most affordable 16-inch M5 Pro MacBook Pro "
        "with the PROLED XDR touch display to $3,099 shipped. Regularly $3,399, this is a "
        "straight up $300 price drop and a new all-time low.",
    )
    assert proled_full.price_usd == 3099.0
    assert proled_full.original_price_usd == 3399.0
    assert proled_full.savings_usd == 300.0
    assert proled_full.discount_pct is None  # 8.8% < 10% threshold

    # Defense-in-depth: novel savings wording repeating title savings without sale cue is rejected
    novel = extract_price_info(
        "MacBook Pro hits Black Friday low at $300 off",
        "Enjoy a $300 holiday break on Apple's flagship laptop today.",
    )
    assert novel.price_usd is None
    assert novel.discount_pct is None
    assert novel.savings_usd == 300.0

    # Genuine 50% off deal where sale price explicitly equals savings STILL works
    real_half = extract_price_info(
        "Bose QuietComfort Headphones $150 off for Black Friday",
        "Amazon dropped the price down to $150 shipped for Black Friday.",
    )
    assert real_half.price_usd == 150.0
    assert real_half.original_price_usd == 300.0
    assert real_half.discount_pct == 50.0
    assert real_half.savings_usd == 150.0


def test_ignores_store_credit_max_discount_cap_usually_costs_and_desc_bleed():
    # DealNews "maximum discount of $60" cap must not become price=$60 or savings=$60
    woot = extract_price_info(
        "MSI Black Friday Sale at Woot for Deals from $48 + free shipping w/ Prime",
        "Use promo code BIGFRIDAY to cut an extra 30% off. There's a maximum discount of $60 per order.",
    )
    assert woot.price_usd is None
    assert woot.savings_usd is None
    assert woot.discount_pct == 30.0

    # "$200 store credit" must not be parsed as item price
    fold = extract_price_info(
        "Galaxy Z Fold 8 $300 off + $200 store credit",
        "You can land one at $300 off and score a $200 store credit today.",
    )
    assert fold.price_usd is None
    assert fold.savings_usd == 300.0
    assert fold.discount_pct is None

    # "usually costs $14.99 per month" + "$50 off six months" must not fabricate $14.99/$64.99 77% off
    apple_tv = extract_price_info(
        "Amazon is knocking almost $50 off six months of Apple TV",
        "Apple's streaming service usually costs $14.99 per month, but this promo cuts the bundle.",
    )
    assert apple_tv.price_usd is None
    assert apple_tv.original_price_usd is None
    assert apple_tv.discount_pct is None
    assert apple_tv.savings_usd == 50.0

    # "Regularly $3,399" without a sale price must not be treated as price_usd
    reg_only = extract_price_info(
        "MacBook Pro Black Friday deal",
        "Regularly $3,399, saves you $300 today at Amazon.",
    )
    assert reg_only.price_usd is None
    assert reg_only.original_price_usd == 3399.0
    assert reg_only.savings_usd == 300.0

    # Symmetric blog intro bleed: tiny unrelated "$9" in description must not combine with title "$250 off"
    bleed = extract_price_info(
        "M5 MacBook Air hits Black Friday low at $250 off",
        "First grab the 2026 Apple Polishing Cloth at $9 Prime shipped while it's in stock.",
    )
    assert bleed.price_usd is None
    assert bleed.discount_pct is None
    assert bleed.savings_usd == 250.0

