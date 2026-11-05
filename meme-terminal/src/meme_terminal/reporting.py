"""Daily report and final scorecard.

The headline figures are sums of the rows printed under them.
"""

from __future__ import annotations

from typing import Any

from meme_terminal.config import RULES, Rules, live_equivalent_pnl
from meme_terminal.storage import Store


def decide(calls: int, net_usd: float, min_calls: int) -> tuple[str, str]:
    """Pre-registered bar: at least `min_calls` entries and a positive after-fee result."""
    reasons: list[str] = []
    if calls < min_calls:
        reasons.append(f"{calls} calls so far, need {min_calls}")
    if net_usd <= 0:
        reasons.append(f"net after fees is ${net_usd:,.2f}, need a positive result")
    if reasons:
        return "NO-GO", "; ".join(reasons)
    return (
        "GO",
        f"{calls} calls and ${net_usd:,.2f} net after fees clear the paper bar. "
        "That is a result for this rule set, not an instruction to trade live.",
    )


def max_drawdown(equity_points: list[float]) -> tuple[float, float]:
    """Return (drawdown in USD, drawdown as a fraction of the peak)."""
    peak = None
    worst = 0.0
    worst_pct = 0.0
    for value in equity_points:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            drop = peak - value
            if drop > worst:
                worst = drop
                worst_pct = drop / peak
    return worst, worst_pct


def render_daily(bundle: dict[str, Any], rules: Rules = RULES) -> str:
    day = bundle["date"]
    snap = bundle.get("snapshot")
    lines = [
        f"# Paper daily report — {day}",
        "",
        "Paper only. No wallet was loaded and no order was sent. "
        "The headline numbers are the totals of the tables below.",
        "",
        "## Headline",
        "",
    ]
    if snap is None:
        lines.append("No scan was stored for this UTC date.")
        lines.append("")
    else:
        equity = snap.get("equity_usd")
        equity_txt = _money(equity) if equity is not None else "unavailable (a mark was missing, so equity was not invented)"
        change = None if equity is None else equity - rules.paper_bankroll_usd
        lines.extend(
            [
                f"- Equity: {equity_txt}",
                f"- Change versus the ${rules.paper_bankroll_usd:,.0f} book: {_signed(change)}",
                f"- Live-equivalent change on a ${rules.live_equivalent_bankroll_usd:,.0f} book: {_signed(None if change is None else live_equivalent_pnl(change, rules))}",
                f"- Closed P&L today, after fees: {_signed(bundle['closed_pnl'])}",
                f"- Fees paid today: {_money(bundle['fees'])}",
                f"- Open positions: {len(bundle['open_positions'])}",
                f"- Checklist passes today: {bundle['passes']}",
                f"- Skipped today: {len(bundle['skipped'])}",
                f"- API errors today: {len(bundle['api_errors'])}",
                f"- Halt: {'yes' if snap.get('halted') else 'no'}",
                "",
            ]
        )
    lines.extend(["## Closed today", ""])
    lines.extend(_trade_table(bundle["closed"]))
    lines.extend(["", "## Open positions", ""])
    lines.extend(_open_table(bundle["open_positions"]))
    lines.extend(["", "## Fees today", ""])
    if not bundle["fee_events"]:
        lines.append("No fills today.")
    else:
        lines.append("| Time (UTC) | Symbol | Kind | Fee | Notional cash change |")
        lines.append("| --- | --- | --- | ---: | ---: |")
        for event in bundle["fee_events"]:
            lines.append(
                f"| {event['at']} | {_cell(event.get('symbol'))} | {event['kind']} | "
                f"{_money(event['fee_usd'])} | {_signed(event['cash_delta'])} |"
            )
        lines.append(f"|  |  | **Total** | **{_money(bundle['fees'])}** |  |")
    lines.extend(["", "## Skipped tokens", ""])
    if not bundle["skipped"]:
        lines.append("Nothing was skipped.")
    else:
        for row in bundle["skipped"]:
            lines.append(f"### {_cell(row.get('symbol'))} `{row.get('mint') or ''}`")
            lines.append("")
            lines.append(f"- Outcome: {row['outcome']}")
            lines.append(f"- Reason: {row['reason']}")
            lines.append("")
    lines.extend(["## Failed checks", ""])
    if not bundle["fail_counts"]:
        lines.append("No failed checks.")
    else:
        lines.append("| Check | Failures |")
        lines.append("| --- | ---: |")
        for name, count in bundle["fail_counts"]:
            lines.append(f"| {name} | {count} |")
    lines.extend(["", "## API errors", ""])
    if not bundle["api_errors"]:
        lines.append("No API errors. A check that could not be verified is still a failed check above, not a silent pass.")
    else:
        lines.append("| Time (UTC) | Source | Mint | Message |")
        lines.append("| --- | --- | --- | --- |")
        for row in bundle["api_errors"]:
            lines.append(
                f"| {row['observed_at']} | {_cell(row['source'])} | `{row.get('mint') or ''}` | {_cell(row['message'])} |"
            )
    lines.extend(["", "## Rules in force", ""])
    lines.append(
        f"Bankroll ${rules.paper_bankroll_usd:,.0f} (live-equivalent book ${rules.live_equivalent_bankroll_usd:,.0f}). "
        f"Position ${rules.position_size_usd:,.0f} inside ${rules.position_size_min_usd:,.0f}–${rules.position_size_max_usd:,.0f}. "
        f"Max {rules.max_open_positions} open. Stop {rules.stop_loss_pct:.0%} with a {rules.stop_gap_extra_pct:.0%} modeled gap. "
        f"Sell half at {rules.take_profit_multiple:g}x, then trail {rules.trail_drop_from_peak:.0%} off the observed peak. "
        f"Max hold {rules.max_hold_minutes} minutes. "
        f"Paper halt ${rules.halt_equity_usd:,.0f} (live halt ${rules.live_halt_equity_usd:,.0f} on the ${rules.live_equivalent_bankroll_usd:,.0f} book). "
        f"Fee ${rules.flat_fee_usd:.2f} per fill. Copy signals do not open trades."
    )
    lines.append("")
    return "\n".join(lines)


def render_scorecard(bundle: dict[str, Any], rules: Rules = RULES) -> str:
    calls = bundle["calls"]
    closed = bundle["closed"]
    wins = [t for t in closed if (t["realized_pnl_usd"] or 0) > 0]
    losses = [t for t in closed if (t["realized_pnl_usd"] or 0) <= 0]
    win_rate = (len(wins) / len(closed)) if closed else None
    avg_win = _mean([t["realized_pnl_usd"] for t in wins])
    avg_loss = _mean([t["realized_pnl_usd"] for t in losses])
    expectancy = _mean([t["realized_pnl_usd"] for t in closed])
    equity = bundle.get("equity")
    net = None if equity is None else equity - rules.paper_bankroll_usd
    drawdown, drawdown_pct = max_drawdown(bundle["equity_curve"])
    if net is None:
        verdict, why = "NO-GO", "equity is unavailable because a mark was missing"
    else:
        verdict, why = decide(calls, net, rules.scorecard_min_calls)
    lines = [
        "# Paper scorecard",
        "",
        "Paper only. Go means this rule set cleared the pre-registered bar. It does not place an order.",
        "",
        f"## Verdict: {verdict}",
        "",
        why,
        "",
        "## Results",
        "",
        f"- Calls (entries): {calls}",
        f"- Closed trades: {len(closed)}",
        f"- Still open: {bundle['open_count']}",
        f"- Win rate on closed trades: {_pct(win_rate)}",
        f"- Average win: {_signed(avg_win)}",
        f"- Average loss: {_signed(avg_loss)}",
        f"- Expectancy after fees (mean realized P&L of closed trades): {_signed(expectancy)}",
        f"- Equity: {_money(equity) if equity is not None else 'unavailable'}",
        f"- Net after fees, including open marks: {_signed(net)}",
        f"- Same net, rescaled to the ${rules.live_equivalent_bankroll_usd:,.0f} book: {_signed(None if net is None else live_equivalent_pnl(net, rules))}",
        f"- Max drawdown: {_money(drawdown)} ({drawdown_pct:.1%} of peak equity)",
        f"- Fees paid: {_money(bundle['fees'])}",
        f"- Bar: {rules.scorecard_min_calls}+ calls and net after fees above zero",
        "",
        "Win rate and expectancy use closed trades only. The go/no-go net also marks open positions at the bid, after modeled exit slippage, and every fill has already paid the flat fee.",
        "",
        "## Calls by market-cap bucket",
        "",
    ]
    buckets = bundle.get("mcap_buckets") or []
    if not buckets:
        lines.append("No calls yet.")
    else:
        lines.append("| Bucket | Calls |")
        lines.append("| --- | ---: |")
        for row in buckets:
            lines.append(f"| {_cell(row['bucket'])} | {int(row['n'])} |")
        lines.append("")
        lines.append("50–250k is recorded as its own bucket. It does not get a separate auto-buy rule.")
    lines.append("")
    return "\n".join(lines)


def load_daily(store: Store, day: str) -> dict[str, Any]:
    prefix = day + "%"
    snaps = store.query(
        "SELECT * FROM equity_snapshots WHERE snapshot_at LIKE ? ORDER BY snapshot_at, rowid",
        [prefix],
    )
    closed = store.query(
        """
        SELECT t.*, t.symbol AS symbol
        FROM paper_trades t
        WHERE t.status = 'closed' AND t.closed_at LIKE ?
        ORDER BY t.closed_at
        """,
        [prefix],
    )
    opened_now = store.query(
        "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY opened_at"
    )
    fee_events = store.query(
        """
        SELECT e.event_at AS at, e.kind, e.fee_usd, e.cash_delta, t.symbol
        FROM trade_events e
        LEFT JOIN paper_trades t ON t.trade_id = e.trade_id
        WHERE e.event_at LIKE ? AND e.fee_usd IS NOT NULL
        ORDER BY e.event_at
        """,
        [prefix],
    )
    skipped = store.query(
        """
        SELECT mint, symbol, outcome, reason
        FROM decisions
        WHERE observed_at LIKE ? AND outcome != 'opened'
        ORDER BY observed_at
        """,
        [prefix],
    )
    fails = store.query(
        """
        SELECT check_name, COUNT(*) AS n
        FROM check_results
        WHERE observed_at LIKE ? AND passed = FALSE
        GROUP BY check_name
        ORDER BY n DESC, check_name
        """,
        [prefix],
    )
    errors = store.query(
        "SELECT * FROM api_errors WHERE observed_at LIKE ? ORDER BY observed_at",
        [prefix],
    )
    passes = store.query(
        """
        SELECT COUNT(*) AS n FROM decisions
        WHERE observed_at LIKE ? AND outcome IN ('opened', 'skip_risk')
        """,
        [prefix],
    )
    fees = sum(float(row["fee_usd"] or 0) for row in fee_events)
    closed_pnl = sum(float(row["realized_pnl_usd"] or 0) for row in closed)
    return {
        "date": day,
        "snapshot": snaps[-1] if snaps else None,
        "closed": closed,
        "closed_pnl": closed_pnl,
        "open_positions": opened_now,
        "fee_events": fee_events,
        "fees": fees,
        "skipped": skipped,
        "fail_counts": [(row["check_name"], int(row["n"])) for row in fails],
        "api_errors": errors,
        "passes": int(passes[0]["n"]) if passes else 0,
    }


def load_scorecard(store: Store) -> dict[str, Any]:
    calls = store.query("SELECT COUNT(*) AS n FROM trade_events WHERE kind = 'open'")
    closed = store.query(
        "SELECT realized_pnl_usd FROM paper_trades WHERE status = 'closed' ORDER BY closed_at"
    )
    open_count = store.query("SELECT COUNT(*) AS n FROM paper_trades WHERE status = 'open'")
    fees = store.query("SELECT COALESCE(SUM(fee_usd), 0) AS n FROM trade_events")
    curve = store.query(
        "SELECT equity_usd FROM equity_snapshots WHERE equity_usd IS NOT NULL ORDER BY snapshot_at, rowid"
    )
    latest = store.query(
        "SELECT equity_usd FROM equity_snapshots ORDER BY snapshot_at DESC, rowid DESC LIMIT 1"
    )
    buckets = store.query(
        """
        SELECT COALESCE(NULLIF(mcap_bucket, ''), 'unknown') AS bucket, COUNT(*) AS n
        FROM paper_trades
        GROUP BY 1
        ORDER BY 1
        """
    )
    equity = latest[0]["equity_usd"] if latest else None
    return {
        "calls": int(calls[0]["n"]) if calls else 0,
        "closed": closed,
        "open_count": int(open_count[0]["n"]) if open_count else 0,
        "fees": float(fees[0]["n"] or 0) if fees else 0.0,
        "equity_curve": [float(row["equity_usd"]) for row in curve],
        "equity": None if equity is None else float(equity),
        "mcap_buckets": buckets,
    }


def _trade_table(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No trades closed today."]
    lines = [
        "| Symbol | Mint | Mcap bucket | Entry | Realized P&L | Fees | Exit |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {_cell(row.get('symbol'))} | `{row['mint']}` | {_bucket(row)} | {_px(row['entry_fill'])} | "
            f"{_signed(row['realized_pnl_usd'])} | {_money(row['fees_usd'])} | {_cell(row.get('exit_reason'))} |"
        )
    return lines


def _open_table(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No open paper positions."]
    lines = [
        "| Symbol | Mint | Mcap bucket | Qty left | Entry fill | Last mid | Peak mid | Half sold | Fees so far | Realized so far |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {_cell(row.get('symbol'))} | `{row['mint']}` | {_bucket(row)} | {row['remaining_qty']:.4f} | "
            f"{_px(row['entry_fill'])} | {_px(row['last_mid'])} | {_px(row['peak_mid'])} | "
            f"{'yes' if row['half_sold'] else 'no'} | {_money(row['fees_usd'])} | {_signed(row['realized_pnl_usd'])} |"
        )
    return lines


def _px(value: Any) -> str:
    """USD price of one token. Memecoins sit well below one cent."""
    if value is None:
        return "n/a"
    number = float(value)
    if abs(number) >= 0.01:
        return f"${number:,.4f}"
    text = f"{number:.8f}".rstrip("0").rstrip(".")
    return f"${text}"


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"


def _signed(value: Any) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}${number:,.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _mean(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _bucket(row: dict[str, Any]) -> str:
    return str(row.get("mcap_bucket") or "unknown")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "/").replace("\n", " ")
