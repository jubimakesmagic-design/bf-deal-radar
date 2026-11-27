"""Static HTML + JSON dashboard exporter for browsing verified overseas deals safely."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from bf_deal_radar.models import ScoredDeal
from bf_deal_radar.parser import clean_kr_product_query, clean_product_query
from bf_deal_radar.security import is_safe_http_url
from bf_deal_radar.verifier import dedupe_source_labels


def serialize_deal_for_dashboard(deal: ScoredDeal) -> dict[str, Any]:
    """Serialize only public deal metadata (never secrets or user PII)."""
    p = deal.primary
    safe_url = p.url if is_safe_http_url(p.url) else ""
    safe_buy_url = p.buy_url if (p.buy_url and is_safe_http_url(p.buy_url)) else ""
    if deal.is_domestic_kr:
        kr_q = quote_plus(clean_kr_product_query(p.title))
        camel_url = f"https://search.shopping.naver.com/search/all?query={kr_q}"
        keepa_url = f"https://search.danawa.com/dsearch.php?query={kr_q}"
        price_krw = p.price_krw
    else:
        camel_url = (
            deal.camel_url
            if (deal.camel_url and is_safe_http_url(deal.camel_url))
            else f"https://camelcamelcamel.com/search?sq={quote_plus(clean_product_query(p.title))}"
        )
        keepa_url = deal.keepa_url if (deal.keepa_url and is_safe_http_url(deal.keepa_url)) else None
        price_krw = deal.customs.price_krw if deal.customs else None

    return {
        "key": deal.canonical_key,
        "region": p.region,
        "title": p.title,
        "korean_title": deal.llm.korean_title if deal.llm else "",
        "summary_ko": deal.llm.summary_ko if deal.llm else "",
        "verdict": deal.llm.verdict if deal.llm else "",
        "url": safe_url,
        "buy_url": safe_buy_url,
        "camel_url": camel_url,
        "keepa_url": keepa_url,
        "asin": p.asin,
        "retailer": p.retailer or ("국내몰" if deal.is_domestic_kr else "Overseas"),
        "sources": dedupe_source_labels(deal.merged_sources),
        "score": deal.score,
        "category_label_ko": deal.category_label_ko,
        "price_usd": p.price_usd,
        "original_price_usd": p.original_price_usd,
        "discount_pct": p.discount_pct,
        "savings_usd": p.savings_usd,
        "price_krw": price_krw,
        "shipping_label": p.shipping_label,
        "is_duty_free": deal.customs.is_duty_free if deal.customs else None,
        "customs_label": deal.customs.label if deal.customs else (f"🚚 {p.shipping_label}" if p.shipping_label else ""),
        "is_watch_match": deal.is_watch_match,
        "matched_watch_keywords": deal.matched_watch_keywords,
        "is_tech": deal.is_tech_category,
        "is_historical_low": deal.is_historical_low,
        "is_cross_source": len(deal.merged_sources) >= 2,
        "coupon_code": p.coupon_code,
        "condition_warning": deal.condition_warning,
        "voltage_warning": deal.voltage_warning,
        "bulky_warning": deal.bulky_warning,
        "membership_warning": deal.membership_warning,
        "reasons": deal.reasons,
        "published_at": p.published_at.isoformat() if p.published_at else None,
    }


def build_dashboard_payload(
    deals: list[ScoredDeal],
    usd_krw_rate: float,
    us_customs_threshold_usd: float = 200.0,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "usd_krw_rate": round(usd_krw_rate, 2),
        "us_customs_threshold_usd": us_customs_threshold_usd,
        "count": len(deals),
        "deals": [serialize_deal_for_dashboard(d) for d in deals],
    }


def render_dashboard_html(payload: dict[str, Any]) -> str:
    """Render a self-contained, XSS-safe static HTML dashboard."""
    # Escape '</' so untrusted RSS text can never break out of <script type="application/json">
    safe_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return _DASHBOARD_TEMPLATE.replace("__DEALS_JSON__", safe_json)


def export_dashboard(
    deals: list[ScoredDeal],
    usd_krw_rate: float,
    output_dir: str | Path = "docs",
    us_customs_threshold_usd: float = 200.0,
) -> tuple[Path, Path]:
    """Export both deals.json and index.html to output_dir."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = build_dashboard_payload(
        deals,
        usd_krw_rate=usd_krw_rate,
        us_customs_threshold_usd=us_customs_threshold_usd,
    )
    json_path = out / "deals.json"
    html_path = out / "index.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_dashboard_html(payload), encoding="utf-8")
    return html_path, json_path


_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>BF Deal Radar — 해외직구 &amp; 국내 핫딜 찐할인 대시보드</title>
  <style>
    :root {
      --bg: #0b0f17;
      --card: #131a27;
      --card-hover: #182133;
      --border: #243047;
      --text: #eef2ff;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --hot: #f97316;
      --ok: #22c55e;
      --warn: #f59e0b;
      --danger: #ef4444;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f8fafc;
        --card: #ffffff;
        --card-hover: #f1f5f9;
        --border: #e2e8f0;
        --text: #0f172a;
        --muted: #475569;
        --accent: #0284c7;
        --hot: #ea580c;
        --ok: #16a34a;
        --warn: #d97706;
        --danger: #dc2626;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans KR", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    .wrap { max-width: 1120px; margin: 0 auto; padding: 20px 16px 48px; }
    header {
      display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-end;
      gap: 12px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
    }
    h1 { margin: 0; font-size: 1.45rem; letter-spacing: -0.02em; }
    .sub { color: var(--muted); font-size: 0.88rem; margin-top: 4px; }
    .stats { display: flex; flex-wrap: wrap; gap: 8px; }
    .pill {
      background: var(--card); border: 1px solid var(--border);
      padding: 6px 12px; border-radius: 999px; font-size: 0.82rem; font-weight: 600;
    }
    .calc-box {
      margin-top: 16px; background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 14px 16px;
      display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between;
    }
    .calc-inputs { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    input, select {
      background: var(--bg); color: var(--text); border: 1px solid var(--border);
      border-radius: 8px; padding: 8px 10px; font-size: 0.9rem;
    }
    .controls {
      margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
      justify-content: space-between;
    }
    .tabs { display: flex; flex-wrap: wrap; gap: 6px; }
    .tab {
      background: var(--card); color: var(--text); border: 1px solid var(--border);
      padding: 6px 12px; border-radius: 999px; cursor: pointer; font-size: 0.84rem;
    }
    .tab.active { background: var(--accent); color: #fff; border-color: var(--accent); font-weight: 600; }
    .grid {
      margin-top: 16px; display: grid; gap: 14px;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    }
    .card {
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 16px; display: flex; flex-direction: column; gap: 10px;
      transition: border-color 0.15s ease, background 0.15s ease;
    }
    .card:hover { border-color: var(--accent); background: var(--card-hover); }
    .card-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
    .score {
      font-weight: 800; font-size: 0.84rem; padding: 3px 9px; border-radius: 999px;
      background: rgba(249, 115, 22, 0.16); color: var(--hot);
    }
    .score.high { background: rgba(239, 68, 68, 0.18); color: var(--danger); }
    .meta { color: var(--muted); font-size: 0.8rem; }
    .title { font-weight: 700; font-size: 0.98rem; margin: 0; }
    .price-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; }
    .price { font-size: 1.25rem; font-weight: 800; }
    .krw { color: var(--muted); font-size: 0.88rem; }
    .off {
      background: rgba(34, 197, 94, 0.16); color: var(--ok);
      font-weight: 700; font-size: 0.8rem; padding: 2px 8px; border-radius: 6px;
    }
    .badge {
      display: inline-block; font-size: 0.78rem; padding: 3px 8px; border-radius: 6px;
      border: 1px solid var(--border); color: var(--muted);
    }
    .badge.ok { color: var(--ok); border-color: rgba(34, 197, 94, 0.4); }
    .badge.warn { color: var(--warn); border-color: rgba(245, 158, 11, 0.4); }
    .reasons { display: flex; flex-wrap: wrap; gap: 6px; }
    .actions { margin-top: auto; display: flex; flex-wrap: wrap; gap: 8px; padding-top: 6px; }
    .btn {
      text-decoration: none; font-size: 0.82rem; font-weight: 600;
      padding: 7px 11px; border-radius: 8px; border: 1px solid var(--border);
      color: var(--text); background: var(--bg);
    }
    .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .empty { text-align: center; padding: 40px 16px; color: var(--muted); }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>🛰️ BF Deal Radar — 해외직구 &amp; 국내 핫딜 찐할인 레이더</h1>
        <div class="sub" id="meta-line">데이터 로딩 중...</div>
      </div>
      <div class="stats" id="stats-bar"></div>
    </header>

    <section class="calc-box">
      <div class="calc-inputs">
        <strong>🧮 직구 관부가세 계산기</strong>
        <input id="calc-usd" type="number" min="1" step="1" value="250" style="width:110px" aria-label="USD price" />
        <select id="calc-cat" aria-label="Category">
          <option value="tech">IT / PC / 전자기기 (부가세 ~10%)</option>
          <option value="general">일반 품목 / 의류 / 가전 (관부가세 ~20%)</option>
        </select>
      </div>
      <div id="calc-out" class="sub" style="font-weight:600;color:var(--text)"></div>
    </section>

    <section class="controls">
      <div class="tabs" id="filter-tabs">
        <button class="tab active" data-filter="all">전체</button>
        <button class="tab" data-filter="kr">🇰🇷 국내핫딜</button>
        <button class="tab" data-filter="us">🇺🇸 해외직구</button>
        <button class="tab" data-filter="watch">🚨 관심 키워드</button>
        <button class="tab" data-filter="tech">💻 IT/가전</button>
        <button class="tab" data-filter="dutyfree">✅ 면세 ($200 이하)</button>
        <button class="tab" data-filter="atl">🏆 역대 최저가</button>
      </div>
      <div class="calc-inputs">
        <input id="search-input" type="search" placeholder="키워드 검색 (예: 쿠팡, AirPods, SSD)" style="width:230px" />
        <select id="sort-select" aria-label="Sort deals">
          <option value="score">점수 높은순</option>
          <option value="discount">할인율 높은순</option>
          <option value="price_asc">가격 낮은순</option>
        </select>
      </div>
    </section>

    <main class="grid" id="deals-grid"></main>
  </div>

  <script id="deals-data" type="application/json">__DEALS_JSON__</script>
  <script>
    const DATA = JSON.parse(document.getElementById("deals-data").textContent);
    const rate = DATA.usd_krw_rate || 1400;
    const threshold = DATA.us_customs_threshold_usd || 200;
    let currentFilter = "all";

    const fmtKrw = (v) => Math.round(v / 100) * 100;
    const isKr = (d) => (d.region || "").toUpperCase() === "KR";
    const effectiveKrw = (d) => {
      if (d.price_krw != null) return d.price_krw;
      if (d.price_usd != null) return d.price_usd * rate;
      return Number.MAX_SAFE_INTEGER;
    };

    function updateMeta() {
      const dt = DATA.generated_at ? new Date(DATA.generated_at).toLocaleString("ko-KR") : "-";
      document.getElementById("meta-line").textContent =
        `기준 환율 1 USD = ${rate.toLocaleString("ko-KR")}원 · 업데이트: ${dt}`;
      const stats = document.getElementById("stats-bar");
      const krCnt = DATA.deals.filter(isKr).length;
      const usCnt = DATA.deals.length - krCnt;
      const watchCnt = DATA.deals.filter(d => d.is_watch_match).length;
      const atlCnt = DATA.deals.filter(d => d.is_historical_low).length;
      stats.replaceChildren();
      [
        `총 ${DATA.deals.length}건`,
        `🇰🇷 국내 ${krCnt}건`,
        `🇺🇸 직구 ${usCnt}건`,
        `🚨 관심 ${watchCnt}건`,
        `🏆 역대가 ${atlCnt}건`,
      ].forEach(txt => {
        const s = document.createElement("span");
        s.className = "pill";
        s.textContent = txt;
        stats.appendChild(s);
      });
    }

    function updateCalc() {
      const usd = parseFloat(document.getElementById("calc-usd").value) || 0;
      const cat = document.getElementById("calc-cat").value;
      const krw = fmtKrw(usd * rate);
      const out = document.getElementById("calc-out");
      if (usd <= 0) { out.textContent = "달러($) 금액을 입력하세요."; return; }
      if (usd <= threshold) {
        out.textContent = `약 ${krw.toLocaleString("ko-KR")}원 · ✅ 미국 목록통관 면세 ($${threshold} 이하)`;
      } else {
        const taxRate = cat === "tech" ? 0.10 : 0.20;
        const total = Math.round((krw * (1 + taxRate)) / 1000) * 1000;
        out.textContent = `상품가 약 ${krw.toLocaleString("ko-KR")}원 → 세금(${Math.round(taxRate * 100)}%) 포함 예상 약 ${total.toLocaleString("ko-KR")}원`;
      }
    }

    function renderDeals() {
      const q = document.getElementById("search-input").value.trim().toLowerCase();
      const sort = document.getElementById("sort-select").value;
      let list = DATA.deals.filter(d => {
        if (currentFilter === "kr" && !isKr(d)) return false;
        if (currentFilter === "us" && isKr(d)) return false;
        if (currentFilter === "watch" && !d.is_watch_match) return false;
        if (currentFilter === "tech" && !d.is_tech) return false;
        if (currentFilter === "dutyfree" && d.is_duty_free !== true) return false;
        if (currentFilter === "atl" && !d.is_historical_low) return false;
        if (q) {
          const hay = `${d.title} ${d.korean_title} ${d.retailer} ${d.category_label_ko || ""} ${(d.sources || []).join(" ")}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      });

      list.sort((a, b) => {
        if (sort === "discount") return (b.discount_pct || 0) - (a.discount_pct || 0) || b.score - a.score;
        if (sort === "price_asc") return effectiveKrw(a) - effectiveKrw(b) || b.score - a.score;
        return (
          b.score - a.score ||
          (b.is_historical_low ? 1 : 0) - (a.is_historical_low ? 1 : 0) ||
          (b.is_watch_match ? 1 : 0) - (a.is_watch_match ? 1 : 0)
        );
      });

      const grid = document.getElementById("deals-grid");
      grid.replaceChildren();
      if (!list.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "조건에 맞는 핫딜이 없습니다.";
        grid.appendChild(empty);
        return;
      }

      for (const d of list) {
        const domestic = isKr(d);
        const card = document.createElement("article");
        card.className = "card";

        const top = document.createElement("div");
        top.className = "card-top";
        const sc = document.createElement("span");
        sc.className = "score" + (d.score >= 85 ? " high" : "");
        sc.textContent = `${domestic ? "🇰🇷" : "🇺🇸"} ${d.is_watch_match ? "🚨 " : ""}${d.score}점`;
        const meta = document.createElement("span");
        meta.className = "meta";
        const catTxt = d.category_label_ko ? `${d.category_label_ko} · ` : "";
        meta.textContent = `${catTxt}${d.retailer} · ${(d.sources || []).slice(0, 2).join(", ")}`;
        top.append(sc, meta);

        const title = document.createElement("h2");
        title.className = "title";
        title.textContent = d.korean_title || d.title;

        const priceRow = document.createElement("div");
        priceRow.className = "price-row";
        const pr = document.createElement("span");
        pr.className = "price";
        if (domestic) {
          pr.textContent = d.price_krw != null ? `${d.price_krw.toLocaleString("ko-KR")}원` : "가격 원글 확인";
          priceRow.appendChild(pr);
          if (d.price_usd != null) {
            const usdSub = document.createElement("span");
            usdSub.className = "krw";
            usdSub.textContent = `($${d.price_usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })})`;
            priceRow.appendChild(usdSub);
          }
        } else {
          pr.textContent = d.price_usd != null ? `$${d.price_usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "가격 확인";
          priceRow.appendChild(pr);
          if (d.price_krw != null) {
            const krw = document.createElement("span");
            krw.className = "krw";
            krw.textContent = `약 ${d.price_krw.toLocaleString("ko-KR")}원`;
            priceRow.appendChild(krw);
          }
        }
        if (d.discount_pct != null) {
          const off = document.createElement("span");
          off.className = "off";
          off.textContent = domestic ? `${Math.round(d.discount_pct)}% 할인` : `${Math.round(d.discount_pct)}% OFF`;
          priceRow.appendChild(off);
        } else if (d.savings_usd != null) {
          const off = document.createElement("span");
          off.className = "off";
          off.textContent = `$${Math.round(d.savings_usd)} OFF`;
          priceRow.appendChild(off);
        }

        const badges = document.createElement("div");
        badges.className = "reasons";
        if (d.customs_label) {
          const b = document.createElement("span");
          if (domestic) {
            b.className = "badge" + (d.customs_label.includes("무료") ? " ok" : "");
          } else {
            b.className = "badge " + (d.is_duty_free ? "ok" : "warn");
          }
          b.textContent = d.customs_label;
          badges.appendChild(b);
        }
        for (const warnText of [d.voltage_warning, d.bulky_warning, d.membership_warning]) {
          if (warnText) {
            const w = document.createElement("span");
            w.className = "badge warn";
            w.textContent = warnText;
            badges.appendChild(w);
          }
        }
        if (d.condition_warning) {
          const cw = document.createElement("span");
          cw.className = "badge warn";
          cw.textContent = `⚠️ 상태 주의: ${d.condition_warning}`;
          badges.appendChild(cw);
        }
        if (d.coupon_code) {
          const c = document.createElement("span");
          c.className = "badge";
          c.textContent = `🎟️ 쿠폰: ${d.coupon_code}`;
          badges.appendChild(c);
        }
        for (const r of (d.reasons || []).slice(0, 3)) {
          const rb = document.createElement("span");
          rb.className = "badge";
          rb.textContent = r;
          badges.appendChild(rb);
        }

        const actions = document.createElement("div");
        actions.className = "actions";
        if (domestic) {
          if (d.buy_url) {
            const buy = document.createElement("a");
            buy.className = "btn primary";
            buy.href = d.buy_url;
            buy.target = "_blank";
            buy.rel = "noopener noreferrer";
            buy.textContent = "🛒 바로 구매하기";
            actions.appendChild(buy);
            if (d.url) {
              const post = document.createElement("a");
              post.className = "btn";
              post.href = d.url;
              post.target = "_blank";
              post.rel = "noopener noreferrer";
              post.textContent = "💬 원글/댓글";
              actions.appendChild(post);
            }
          } else if (d.url) {
            const a = document.createElement("a");
            a.className = "btn primary";
            a.href = d.url;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            a.textContent = "🛒 핫딜 원글 보기";
            actions.appendChild(a);
          }
          if (d.camel_url) {
            const n = document.createElement("a");
            n.className = "btn";
            n.href = d.camel_url;
            n.target = "_blank";
            n.rel = "noopener noreferrer";
            n.textContent = "🔍 네이버 최저가";
            actions.appendChild(n);
          }
          if (d.keepa_url) {
            const dn = document.createElement("a");
            dn.className = "btn";
            dn.href = d.keepa_url;
            dn.target = "_blank";
            dn.rel = "noopener noreferrer";
            dn.textContent = "📊 다나와 시세";
            actions.appendChild(dn);
          }
        } else {
          if (d.url) {
            const a = document.createElement("a");
            a.className = "btn primary";
            a.href = d.url;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            a.textContent = "🛒 딜 바로가기";
            actions.appendChild(a);
          }
          if (d.camel_url) {
            const c = document.createElement("a");
            c.className = "btn";
            c.href = d.camel_url;
            c.target = "_blank";
            c.rel = "noopener noreferrer";
            c.textContent = "📈 가격추이(CCC)";
            actions.appendChild(c);
          }
          if (d.keepa_url) {
            const k = document.createElement("a");
            k.className = "btn";
            k.href = d.keepa_url;
            k.target = "_blank";
            k.rel = "noopener noreferrer";
            k.textContent = "📊 Keepa";
            actions.appendChild(k);
          }
        }

        card.append(top, title, priceRow, badges, actions);
        grid.appendChild(card);
      }
    }

    document.querySelectorAll(".tab").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentFilter = btn.dataset.filter;
        renderDeals();
      });
    });
    document.getElementById("search-input").addEventListener("input", renderDeals);
    document.getElementById("sort-select").addEventListener("change", renderDeals);
    document.getElementById("calc-usd").addEventListener("input", updateCalc);
    document.getElementById("calc-cat").addEventListener("change", updateCalc);

    updateMeta();
    updateCalc();
    renderDeals();
  </script>
</body>
</html>
"""
