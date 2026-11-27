"""Pretty console notifier for dry-run / local debugging."""

from __future__ import annotations

from bf_deal_radar.models import ScoredDeal


class ConsoleNotifier:
    """Prints scored deals cleanly to stdout without sending Telegram messages."""

    def send_deal(self, deal: ScoredDeal) -> bool:
        p = deal.primary
        region = "🇰🇷국내" if deal.is_domestic_kr else "🇺🇸직구"
        err_tag = " [🚨가격오류]" if deal.is_price_error else ""
        watch_tag = " [🚨WATCH]" if deal.is_watch_match else ""

        print("=" * 76)
        print(f"[{region} {deal.score:3d}점]{err_tag}{watch_tag} ({deal.category_label_ko}) {p.title}")

        if deal.is_domestic_kr:
            price_str = f"{p.price_krw:,}원" if p.price_krw is not None else "가격 원글 확인"
            ship_str = f" | {p.shipping_label}" if p.shipping_label else ""
            disc_str = f" | {p.discount_pct:.0f}% 할인" if p.discount_pct else ""
            print(f"  가격: {price_str}{ship_str}{disc_str}")
            print(
                f"  반응: 추천 {p.thumb_score or 0} · 댓글 {p.comment_count or 0} · 조회 {p.view_count or 0:,}"
            )
        else:
            price_str = f"${p.price_usd:,.2f}" if p.price_usd is not None else "가격 미표기"
            krw_str = f" (~{deal.customs.price_krw:,}원)" if deal.customs else ""
            disc_str = f" | {p.discount_pct:.0f}% OFF" if p.discount_pct else (
                f" | ${p.savings_usd:,.0f} OFF" if p.savings_usd else ""
            )
            print(f"  가격: {price_str}{krw_str}{disc_str}")

        if p.retailer or deal.merged_sources:
            print(f"  판매처: {p.retailer or 'Unknown'} | 소스: {', '.join(deal.merged_sources)}")
        if deal.customs:
            print(f"  통관: {deal.customs.label}")
        if deal.voltage_warning:
            print(f"  전압: {deal.voltage_warning}")
        if deal.bulky_warning:
            print(f"  부피: {deal.bulky_warning}")
        if deal.membership_warning:
            print(f"  회원: {deal.membership_warning}")
        if p.coupon_code:
            print(f"  쿠폰: {p.coupon_code}")
        if deal.reasons:
            print(f"  근거: {' / '.join(deal.reasons[:4])}")
        if deal.llm and deal.llm.summary_ko:
            print(f"  AI요약({deal.llm.verdict}): {deal.llm.summary_ko}")
        if p.buy_url:
            print(f"  구매: {p.buy_url}")
        print(f"  원글: {p.url}")
        if deal.camel_url:
            print(f"  CCC:  {deal.camel_url}")
        return True
