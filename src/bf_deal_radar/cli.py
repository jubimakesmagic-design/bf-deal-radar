"""Command-line interface for bf-deal-radar."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from bf_deal_radar.config import load_config
from bf_deal_radar.exchange import fetch_usd_krw_rate
from bf_deal_radar.notifier.telegram import TelegramNotifier
from bf_deal_radar.pipeline import run_once


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env)
    if not args.dry_run and not args.seed_only:
        if not config.telegram_bot_token or not config.telegram_chat_id:
            print(
                "❌ TELEGRAM_BOT_TOKEN 과 TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.\n"
                "   테스트하려면 `--dry-run` 옵션을 붙여서 실행해 보세요:\n"
                "   bf-radar run --dry-run",
                file=sys.stderr,
            )
            return 2

    report = run_once(
        config,
        dry_run=args.dry_run,
        seed_only=args.seed_only,
        min_score_override=args.min_score,
        limit_override=args.limit,
    )
    print(
        f"\n📡 수집 {report.raw_count}건 -> 중복병합 {report.clustered_count}건 "
        f"-> 기준통과 {report.qualified_count}건 -> 알림발송 {report.notified_count}건 "
        f"(환율 1 USD = {report.usd_krw_rate:,.1f}원)"
    )
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env)
    if not args.dry_run and (not config.telegram_bot_token or not config.telegram_chat_id):
        print("❌ TELEGRAM_BOT_TOKEN 과 TELEGRAM_CHAT_ID가 필요합니다.", file=sys.stderr)
        return 2

    interval = max(30, int(args.interval))
    print(f"🛰️ BF Deal Radar 상시 감시 시작 (주기: {interval}초, Ctrl+C로 종료)")
    while True:
        try:
            report = run_once(
                config,
                dry_run=args.dry_run,
                min_score_override=args.min_score,
                limit_override=args.limit,
            )
            logging.getLogger("bf_deal_radar").info(
                "Cycle complete: raw=%d clustered=%d qualified=%d notified=%d",
                report.raw_count,
                report.clustered_count,
                report.qualified_count,
                report.notified_count,
            )
        except KeyboardInterrupt:
            print("\n종료합니다.")
            return 0
        except Exception as exc:
            logging.getLogger("bf_deal_radar").exception("Cycle failed: %s", exc)

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n종료합니다.")
            return 0


def _cmd_test_telegram(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env)
    if not config.telegram_bot_token or not config.telegram_chat_id:
        print("❌ TELEGRAM_BOT_TOKEN 과 TELEGRAM_CHAT_ID를 .env 또는 환경변수에 설정하세요.", file=sys.stderr)
        return 2

    rate = fetch_usd_krw_rate(fallback=config.fallback_usd_krw, override=config.override_usd_krw)
    notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    if notifier.send_test_message(rate):
        print("✅ 텔레그램 테스트 메시지 발송 성공!")
        return 0
    print("❌ 텔레그램 테스트 메시지 발송 실패. 토큰과 Chat ID를 확인하세요.", file=sys.stderr)
    return 1


def _cmd_sources(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env)
    for src in config.sources:
        status = "ON " if src.enabled else "OFF"
        print(f"[{status}] {src.name:24s} ({src.type:12s}, base={src.base_score:2d}) {src.url}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bf-radar",
        description="해외직구 & 블랙프라이데이 찐할인 레이더 (텔레그램 알리미)",
    )
    parser.add_argument("--config", default="config.yaml", help="YAML 설정 파일 경로")
    parser.add_argument("--env", default=".env", help=".env 파일 경로")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그 출력")

    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="핫딜 수집 및 알림 1회 실행")
    p_run.add_argument("--dry-run", action="store_true", help="텔레그램 발송 없이 콘솔에 결과만 출력")
    p_run.add_argument("--seed-only", action="store_true", help="알림 없이 현재 딜을 DB에 기록만 함")
    p_run.add_argument("--min-score", type=int, default=None, help="최소 알림 점수 덮어쓰기")
    p_run.add_argument("--limit", type=int, default=None, help="최대 알림 개수 덮어쓰기")
    p_run.set_defaults(func=_cmd_run)

    p_watch = sub.add_parser("watch", help="지정 주기로 상시 감시 (Docker/로컬용)")
    p_watch.add_argument("--interval", type=int, default=180, help="폴링 주기 (초, 기본 180초)")
    p_watch.add_argument("--dry-run", action="store_true", help="콘솔 출력만 수행")
    p_watch.add_argument("--min-score", type=int, default=None, help="최소 알림 점수 덮어쓰기")
    p_watch.add_argument("--limit", type=int, default=None, help="1회 최대 알림 개수")
    p_watch.set_defaults(func=_cmd_watch)

    p_test = sub.add_parser("test-telegram", help="텔레그램 봇 연결 테스트 메시지 발송")
    p_test.set_defaults(func=_cmd_test_telegram)

    p_src = sub.add_parser("sources", help="등록된 수집 소스 목록 확인")
    p_src.set_defaults(func=_cmd_sources)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
