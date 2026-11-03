"""DuckDB ledger for scans, checklist results, and paper trades."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from meme_terminal.config import RULES, rules_dict
from meme_terminal.risk import Account, Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS account_state (
    id INTEGER PRIMARY KEY,
    cash_usd DOUBLE,
    halted BOOLEAN,
    created_at VARCHAR
);
CREATE TABLE IF NOT EXISTS scans (
    scan_id VARCHAR PRIMARY KEY,
    started_at VARCHAR,
    finished_at VARCHAR,
    candidate_count INTEGER,
    passed_count INTEGER,
    opened_count INTEGER,
    halted BOOLEAN,
    equity_usd DOUBLE,
    rules_json VARCHAR
);
CREATE TABLE IF NOT EXISTS snapshots (
    id VARCHAR PRIMARY KEY,
    scan_id VARCHAR,
    mint VARCHAR,
    symbol VARCHAR,
    source VARCHAR,
    observed_at VARCHAR,
    payload_json VARCHAR
);
CREATE TABLE IF NOT EXISTS check_results (
    id VARCHAR PRIMARY KEY,
    scan_id VARCHAR,
    mint VARCHAR,
    symbol VARCHAR,
    check_name VARCHAR,
    passed BOOLEAN,
    reason VARCHAR,
    evidence_json VARCHAR,
    observed_at VARCHAR
);
CREATE TABLE IF NOT EXISTS api_errors (
    id VARCHAR PRIMARY KEY,
    scan_id VARCHAR,
    mint VARCHAR,
    source VARCHAR,
    message VARCHAR,
    observed_at VARCHAR
);
CREATE TABLE IF NOT EXISTS decisions (
    id VARCHAR PRIMARY KEY,
    scan_id VARCHAR,
    mint VARCHAR,
    symbol VARCHAR,
    outcome VARCHAR,
    reason VARCHAR,
    observed_at VARCHAR
);
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id VARCHAR PRIMARY KEY,
    mint VARCHAR,
    symbol VARCHAR,
    pair_address VARCHAR,
    status VARCHAR,
    opened_at VARCHAR,
    closed_at VARCHAR,
    entry_fill DOUBLE,
    qty DOUBLE,
    remaining_qty DOUBLE,
    initial_notional_usd DOUBLE,
    cost_remaining_usd DOUBLE,
    cash_in_usd DOUBLE,
    fees_usd DOUBLE,
    realized_pnl_usd DOUBLE,
    peak_mid DOUBLE,
    half_sold BOOLEAN,
    liquidity_usd DOUBLE,
    last_mid DOUBLE,
    exit_reason VARCHAR,
    updated_at VARCHAR
);
CREATE TABLE IF NOT EXISTS trade_events (
    id VARCHAR PRIMARY KEY,
    trade_id VARCHAR,
    scan_id VARCHAR,
    event_at VARCHAR,
    kind VARCHAR,
    qty DOUBLE,
    mid DOUBLE,
    fill DOUBLE,
    fee_usd DOUBLE,
    slippage_bps INTEGER,
    cash_delta DOUBLE,
    note VARCHAR
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id VARCHAR PRIMARY KEY,
    scan_id VARCHAR,
    snapshot_at VARCHAR,
    cash_usd DOUBLE,
    positions_value_usd DOUBLE,
    equity_usd DOUBLE,
    halted BOOLEAN
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps(payload: Any) -> str:
    text = json.dumps(payload, default=str, separators=(",", ":"))
    if len(text) > 200_000:
        return text[:200_000]
    return text


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        self.con.execute(SCHEMA)
        self._ensure_account()

    def close(self) -> None:
        self.con.close()

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def _ensure_account(self) -> None:
        rows = self.query("SELECT id FROM account_state WHERE id = 1")
        if rows:
            return
        created = iso(utcnow())
        self.con.execute(
            "INSERT INTO account_state VALUES (1, ?, FALSE, ?)",
            [RULES.paper_bankroll_usd, created],
        )
        self.con.execute(
            "INSERT INTO equity_snapshots VALUES (?, ?, ?, ?, ?, ?, FALSE)",
            ["equity-start", "bootstrap", created, RULES.paper_bankroll_usd, 0.0, RULES.paper_bankroll_usd],
        )

    def load_account(self) -> tuple[Account, dict[str, dict[str, Any]]]:
        state = self.query("SELECT cash_usd, halted FROM account_state WHERE id = 1")[0]
        rows = self.query("SELECT * FROM paper_trades")
        meta: dict[str, dict[str, Any]] = {}
        positions: list[Position] = []
        for row in rows:
            meta[row["trade_id"]] = {
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
                "exit_reason": row["exit_reason"],
            }
            positions.append(
                Position(
                    trade_id=row["trade_id"],
                    mint=row["mint"],
                    symbol=row["symbol"] or row["mint"][:6],
                    pair_address=row["pair_address"],
                    qty=row["qty"],
                    remaining_qty=row["remaining_qty"],
                    entry_fill=row["entry_fill"],
                    initial_notional_usd=row["initial_notional_usd"],
                    cost_remaining_usd=row["cost_remaining_usd"],
                    cash_in_usd=row["cash_in_usd"],
                    fees_usd=row["fees_usd"],
                    realized_pnl_usd=row["realized_pnl_usd"],
                    peak_mid=row["peak_mid"],
                    half_sold=bool(row["half_sold"]),
                    status=row["status"],
                    liquidity_usd=row["liquidity_usd"],
                    last_mid=row["last_mid"],
                )
            )
        account = Account(
            cash_usd=float(state["cash_usd"]),
            halted=bool(state["halted"]),
            positions=tuple(positions),
        )
        return account, meta

    def save_account(self, account: Account) -> None:
        self.con.execute(
            "UPDATE account_state SET cash_usd = ?, halted = ? WHERE id = 1",
            [account.cash_usd, account.halted],
        )

    def traded_mints(self) -> set[str]:
        return {row["mint"] for row in self.query("SELECT mint FROM paper_trades")}

    def upsert_trade(
        self,
        position: Position,
        *,
        opened_at: str,
        closed_at: str | None,
        exit_reason: str | None,
        updated_at: str,
    ) -> None:
        self.con.execute("DELETE FROM paper_trades WHERE trade_id = ?", [position.trade_id])
        self.con.execute(
            """
            INSERT INTO paper_trades VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                position.trade_id,
                position.mint,
                position.symbol,
                position.pair_address,
                position.status,
                opened_at,
                closed_at,
                position.entry_fill,
                position.qty,
                position.remaining_qty,
                position.initial_notional_usd,
                position.cost_remaining_usd,
                position.cash_in_usd,
                position.fees_usd,
                position.realized_pnl_usd,
                position.peak_mid,
                position.half_sold,
                position.liquidity_usd,
                position.last_mid,
                exit_reason,
                updated_at,
            ],
        )

    def add_event(
        self,
        *,
        trade_id: str,
        scan_id: str,
        at: str,
        kind: str,
        qty: float | None,
        mid: float | None,
        fill: float | None,
        fee_usd: float | None,
        slippage_bps: int | None,
        cash_delta: float | None,
        note: str,
    ) -> None:
        self.con.execute(
            "INSERT INTO trade_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                uuid.uuid4().hex,
                trade_id,
                scan_id,
                at,
                kind,
                qty,
                mid,
                fill,
                fee_usd,
                slippage_bps,
                cash_delta,
                note,
            ],
        )

    def add_snapshot(self, scan_id: str, mint: str, symbol: str | None, source: str, at: str, payload: Any) -> None:
        self.con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            [uuid.uuid4().hex, scan_id, mint, symbol, source, at, dumps(payload)],
        )

    def add_check(
        self,
        scan_id: str,
        mint: str,
        symbol: str | None,
        name: str,
        passed: bool,
        reason: str,
        evidence: dict[str, Any],
        at: str,
    ) -> None:
        self.con.execute(
            "INSERT INTO check_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [uuid.uuid4().hex, scan_id, mint, symbol, name, passed, reason, dumps(evidence), at],
        )

    def add_api_error(self, scan_id: str, mint: str | None, source: str, message: str, at: str) -> None:
        self.con.execute(
            "INSERT INTO api_errors VALUES (?, ?, ?, ?, ?, ?)",
            [uuid.uuid4().hex, scan_id, mint, source, message[:500], at],
        )

    def add_decision(self, scan_id: str, mint: str, symbol: str | None, outcome: str, reason: str, at: str) -> None:
        self.con.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            [uuid.uuid4().hex, scan_id, mint, symbol, outcome, reason[:1000], at],
        )

    def start_scan(self, at: str) -> str:
        scan_id = uuid.uuid4().hex
        self.con.execute(
            "INSERT INTO scans VALUES (?, ?, NULL, 0, 0, 0, FALSE, NULL, ?)",
            [scan_id, at, dumps(rules_dict())],
        )
        return scan_id

    def finish_scan(
        self,
        scan_id: str,
        *,
        finished_at: str,
        candidates: int,
        passed: int,
        opened: int,
        halted: bool,
        equity: float | None,
        cash: float,
        positions_value: float | None,
    ) -> None:
        self.con.execute(
            """
            UPDATE scans
            SET finished_at = ?, candidate_count = ?, passed_count = ?, opened_count = ?,
                halted = ?, equity_usd = ?
            WHERE scan_id = ?
            """,
            [finished_at, candidates, passed, opened, halted, equity, scan_id],
        )
        self.con.execute(
            "INSERT INTO equity_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            [uuid.uuid4().hex, scan_id, finished_at, cash, positions_value, equity, halted],
        )
