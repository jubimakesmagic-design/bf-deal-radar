"""Unit tests for source-specific RSS and Atom XML feed parsing."""

from bf_deal_radar.models import SourceConfig
from bf_deal_radar.sources.rss import parse_feed_xml

SLICKDEALS_XML = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <item>
      <title>Sony WH-1000XM5 Wireless Noise Canceling Headphones $228 + Free S&amp;H</title>
      <link>https://slickdeals.net/f/20409999-sony-wh-1000xm5-228?utm_source=rss</link>
      <description>Amazon has Sony WH-1000XM5 on sale for $228 (was $399.99) w/ code SONYBF20.</description>
      <content:encoded><![CDATA[
        <div><img src="https://static.slickdealscdn.com/img.jpg"></div>
        <div>Thumb Score: +85 </div>
        <div>Use code <span data-role='couponCode'><span class='code'><strong>SONYBF20</strong></span></span>
        at <a href="https://www.amazon.com/dp/B09XS7JWHH">Amazon</a>.</div>
      ]]></content:encoded>
      <pubDate>Fri, 27 Nov 2026 00:30:00 +0000</pubDate>
      <guid>thread-20409999</guid>
    </item>
  </channel>
</rss>
"""

DEALNEWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dealnews="https://www.dealnews.com/ns/rss/1.0.htm">
  <channel>
    <item>
      <title>Kasa Smart Plug Mini 4-Pack for $28 + free shipping w/ Prime</title>
      <link>https://www.dealnews.com/products/Kasa/564513.html?iref=rss-c142</link>
      <description><![CDATA[
        <div class="snippet summary" title="This 4-pack of Kasa smart plugs is 37% off at Amazon and matches its Amazon all-time low of $28.">short</div>
      ]]></description>
      <pubDate>Thu, 26 Nov 2026 18:25:22 -0500</pubDate>
      <dealnews:retailer>Amazon</dealnews:retailer>
      <dealnews:price currency="USD">28.00</dealnews:price>
      <dealnews:staffPick>true</dealnews:staffPick>
      <dealnews:category>Smart Home</dealnews:category>
    </item>
  </channel>
</rss>
"""

TECHBARGAINS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="0.91">
  <channel>
    <item>
      <title>Apple 2nd Generation AirTag Tracker $18</title>
      <link>https://www.amazon.com/gp/product/B0GJTFXNRX/ref=ox_sc_act?smid=ATVPDKIKX0DER</link>
      <description><![CDATA[<a href="https://www.amazon.com/gp/product/B0GJTFXNRX">AirTag $18</a>]]></description>
      <pubDate>Thu, 26 Nov 2026 22:34:00 +0000</pubDate>
      <vendorname>Amazon</vendorname>
      <category>Computer Accessories</category>
      <imagelink>https://www.techbargains.com/img.jpg</imagelink>
    </item>
  </channel>
</rss>
"""

VERGE_ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title type="html">The Garmin Forerunner 265S is down to $300, its best price of the year</title>
    <link rel="alternate" type="text/html" href="https://www.theverge.com/gadgets/1042483/garmin-265s"/>
    <id>https://www.theverge.com/?p=1042483</id>
    <published>2026-11-26T14:00:00-05:00</published>
    <category term="Black Friday"/>
    <category term="Wearable"/>
    <summary type="html">Down to $300, saving $150 off retail.</summary>
  </entry>
</feed>
"""


def test_parse_slickdeals_xml():
    src = SourceConfig(id="slickdeals_fp", name="Slickdeals Frontpage", type="slickdeals", url="https://example.com", base_score=24)
    deals = parse_feed_xml(SLICKDEALS_XML, src)
    assert len(deals) == 1
    d = deals[0]
    assert d.price_usd == 228.0
    assert d.original_price_usd == 399.99
    assert d.thumb_score == 85
    assert d.coupon_code == "SONYBF20"
    assert d.asin == "B09XS7JWHH"
    assert d.retailer == "Amazon"


def test_parse_dealnews_xml():
    src = SourceConfig(id="dealnews_elec", name="DealNews Electronics", type="dealnews", url="https://example.com", base_score=14)
    deals = parse_feed_xml(DEALNEWS_XML, src)
    assert len(deals) == 1
    d = deals[0]
    assert d.price_usd == 28.0
    assert d.discount_pct == 37.0
    assert d.staff_pick is True
    assert d.retailer == "Amazon"
    assert "all-time low" in d.description.lower()


def test_parse_techbargains_xml():
    src = SourceConfig(id="techbargains", name="TechBargains", type="techbargains", url="https://example.com", base_score=14)
    deals = parse_feed_xml(TECHBARGAINS_XML, src)
    assert len(deals) == 1
    d = deals[0]
    assert d.price_usd == 18.0
    assert d.asin == "B0GJTFXNRX"
    assert d.retailer == "Amazon"
    assert d.image_url == "https://www.techbargains.com/img.jpg"


def test_parse_verge_atom_xml():
    src = SourceConfig(id="verge_deals", name="The Verge Deals", type="verge", url="https://example.com", base_score=14)
    deals = parse_feed_xml(VERGE_ATOM_XML, src)
    assert len(deals) == 1
    d = deals[0]
    assert d.price_usd == 300.0
    assert d.savings_usd == 150.0
    assert d.published_at is not None
    assert "Wearable" in (d.category or "")
