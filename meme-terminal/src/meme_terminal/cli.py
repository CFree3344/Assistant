"""Command line for the paper terminal.

scan     one screening pass and an update of open paper positions
report   markdown daily report
scorecard  win rate, expectancy, drawdown, and the go/no-go bar
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from meme_terminal import PAPER_ONLY
from meme_terminal.http_client import HttpClient
from meme_terminal.reporting import load_daily, load_scorecard, render_daily, render_scorecard
from meme_terminal.scan import run_scan
from meme_terminal.storage import Store, utcnow

BANNER = "PAPER ONLY — no wallet, no signing, no orders."


def default_db() -> Path:
    override = os.environ.get("MEME_TERMINAL_DB")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "terminal.duckdb"


def main(argv: list[str] | None = None) -> int:
    if not PAPER_ONLY:
        print("refusing to run: PAPER_ONLY is not set", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(
        prog="meme-terminal",
        description="Dry-run Solana memecoin paper terminal. Cannot sign or send transactions.",
    )
    parser.add_argument("--db", default=None, help="DuckDB file. Defaults to meme-terminal/data/terminal.duckdb")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="screen new tokens and update open paper positions")
    scan.add_argument("--max-candidates", type=int, default=None)

    report = sub.add_parser("report", help="write the daily markdown report")
    report.add_argument("--date", default=None, help="UTC date YYYY-MM-DD (default: today)")
    report.add_argument("--out", default=None, help="also write the report to this path")

    card = sub.add_parser("scorecard", help="write the running scorecard")
    card.add_argument("--out", default=None, help="also write the scorecard to this path")

    args = parser.parse_args(argv)
    print(BANNER)
    db_path = Path(args.db) if args.db else default_db()
    store = Store(db_path)
    try:
        if args.cmd == "scan":
            return _scan(store, args.max_candidates)
        if args.cmd == "report":
            day = args.date or utcnow().strftime("%Y-%m-%d")
            text = render_daily(load_daily(store, day))
            return _emit(text, args.out)
        if args.cmd == "scorecard":
            text = render_scorecard(load_scorecard(store))
            return _emit(text, args.out)
    finally:
        store.close()
    return 2


def _scan(store: Store, max_candidates: int | None) -> int:
    token = os.environ.get("GITHUB_TOKEN") or None
    client = HttpClient(github_token=token)
    summary = run_scan(store, client, max_candidates=max_candidates, now=datetime.now(timezone.utc))
    equity = "unavailable" if summary.equity is None else f"${summary.equity:,.2f}"
    print(
        f"scan {summary.scan_id[:8]} candidates {summary.candidates} "
        f"passed {summary.passed} opened {summary.opened} "
        f"equity {equity} cash ${summary.cash:,.2f} "
        f"halt {'yes' if summary.halted else 'no'} api_errors {summary.api_errors}"
    )
    print(f"ledger {store.path}")
    return 0


def _emit(text: str, path: str | None) -> int:
    print(text)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"\nWrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
