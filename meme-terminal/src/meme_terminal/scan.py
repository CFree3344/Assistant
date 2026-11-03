"""One screening pass: refresh open paper positions, then check new mints.

Network I/O stays in this module. The checklist and the risk engine stay pure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from meme_terminal.checklist import RawInputs, evaluate, github_targets, mark_from_pairs
from meme_terminal.config import MAJOR_QUOTES, RULES, Rules
from meme_terminal.http_client import FetchResult
from meme_terminal.risk import TradeEvent, equity, mark_position, needs_halt, open_position
from meme_terminal.sources import (
    JsonClient,
    discover,
    fetch_github,
    fetch_marks,
    fetch_pairs,
    fetch_rug,
    rpc_account,
    rpc_largest_accounts,
)
from meme_terminal.storage import Store, iso, utcnow

_BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


@dataclass
class ScanSummary:
    scan_id: str
    candidates: int
    passed: int
    opened: int
    equity: float | None
    cash: float
    halted: bool
    api_errors: int
    notes: list[str] = field(default_factory=list)


def run_scan(
    store: Store,
    client: JsonClient,
    *,
    rules: Rules = RULES,
    max_candidates: int | None = None,
    now: datetime | None = None,
) -> ScanSummary:
    moment = now or utcnow()
    stamp = iso(moment)
    scan_id = store.start_scan(stamp)
    account, meta = store.load_account()
    error_count = 0

    def note_error(source: str, message: str, mint: str | None = None) -> None:
        nonlocal error_count
        error_count += 1
        store.add_api_error(scan_id, mint, source, message, stamp)

    account, fresh = _refresh_open(store, client, account, meta, scan_id, stamp, rules, note_error)
    if _marks_cover(account, fresh) and needs_halt(account, fresh, rules):
        account = _flatten(store, account, meta, fresh, scan_id, stamp, rules)

    limit = max_candidates if max_candidates is not None else rules.max_candidates_per_scan
    passed = 0
    opened = 0
    considered = 0
    if account.halted:
        store.add_decision(scan_id, "", None, "halt", "global halt is active; no new paper entries", stamp)
    else:
        discovery = discover(client)
        for source, message in discovery.errors:
            note_error(source, message)
        traded = store.traded_mints()
        seeds = []
        for seed in discovery.seeds:
            if seed.mint in traded or seed.mint in MAJOR_QUOTES or not _mint_ok(seed.mint):
                continue
            seeds.append(seed)
            if len(seeds) >= limit:
                break
        considered = len(seeds)
        for seed in seeds:
            try:
                ok, did_open, account, fresh = _screen_one(
                    store,
                    client,
                    account,
                    meta,
                    seed.mint,
                    seed.profile,
                    seed.source,
                    scan_id,
                    stamp,
                    moment,
                    rules,
                    note_error,
                    fresh,
                )
            except Exception as exc:
                note_error("scan", f"{type(exc).__name__}: {exc}", seed.mint)
                store.add_decision(
                    scan_id,
                    seed.mint,
                    None,
                    "skip_checklist",
                    f"scan error: {type(exc).__name__}: {exc}",
                    stamp,
                )
                continue
            passed += int(ok)
            opened += int(did_open)

    book = equity(account, fresh, rules) if _marks_cover(account, fresh) else None
    positions_value = None if book is None else book - account.cash_usd
    store.save_account(account)
    store.finish_scan(
        scan_id,
        finished_at=stamp,
        candidates=considered,
        passed=passed,
        opened=opened,
        halted=account.halted,
        equity=book,
        cash=account.cash_usd,
        positions_value=positions_value,
    )
    return ScanSummary(
        scan_id=scan_id,
        candidates=considered,
        passed=passed,
        opened=opened,
        equity=book,
        cash=account.cash_usd,
        halted=account.halted,
        api_errors=error_count,
    )


def _marks_cover(account, fresh: dict[str, float]) -> bool:
    return all(p.mint in fresh and fresh[p.mint] > 0 for p in account.open_positions())


def _refresh_open(store, client, account, meta, scan_id, stamp, rules, note_error):
    fresh: dict[str, float] = {}
    open_positions = account.open_positions()
    if not open_positions:
        return account, fresh
    mints = [p.mint for p in open_positions]
    result = fetch_marks(client, mints)
    if not result.ok:
        note_error("dexscreener_marks", result.error or "mark refresh failed")
        return account, fresh
    payload = result.data
    by_mint = {}
    if isinstance(payload, list):
        for pair in payload:
            if isinstance(pair, dict):
                base = (pair.get("baseToken") or {}).get("address")
                if base:
                    by_mint.setdefault(base, []).append(pair)
    elif isinstance(payload, dict) and isinstance(payload.get("pairs"), list):
        for pair in payload["pairs"]:
            if isinstance(pair, dict):
                base = (pair.get("baseToken") or {}).get("address")
                if base:
                    by_mint.setdefault(base, []).append(pair)
    for position in open_positions:
        mark = mark_from_pairs(position.mint, by_mint.get(position.mint))
        if mark is None:
            note_error("dexscreener_marks", "no usable mark; position left unchanged", position.mint)
            continue
        fresh[position.mint] = float(mark["price"])
        store.add_snapshot(scan_id, position.mint, position.symbol, "dex_mark", stamp, by_mint.get(position.mint))
        event = mark_position(position, mark["price"], mark.get("liquidity"), rules)
        if event is None:
            continue
        account = _commit_event(store, account, meta, event, scan_id, stamp)
    return account, fresh


def _flatten(store, account, meta, marks, scan_id, stamp, rules):
    from meme_terminal.risk import flatten

    flattened, events = flatten(account, marks, rules)
    for event in events:
        _record_event(store, meta, event, scan_id, stamp)
    if not events and account.open_positions():
        # Halt is required but a mark was missing, so flatten refused to invent a price.
        return account
    return flattened


def _screen_one(store, client, account, meta, mint, profile, source, scan_id, stamp, moment, rules, note_error, fresh):
    pairs = fetch_pairs(client, mint)
    rug = fetch_rug(client, mint)
    rpc = rpc_account(client, mint)
    if not pairs.ok:
        note_error("dexscreener_pairs", pairs.error or "pairs failed", mint)
    if not rug.ok:
        note_error("rugcheck", rug.error or "report failed", mint)
    if not rpc.ok:
        note_error("solana_rpc", rpc.error or "mint account failed", mint)
    rug_data = rug.data if rug.ok and isinstance(rug.data, dict) else None
    rpc_lp, largest, lp_accounts, locker_accounts, lp_errors = _lp_context(client, rug_data)
    for src, message in lp_errors:
        note_error(src, message, mint)
    raw = RawInputs(
        mint=mint,
        dex_pairs=pairs.data if pairs.ok else None,
        profile=profile,
        rug_report=rug_data,
        rpc_mint=rpc.data if rpc.ok and isinstance(rpc.data, dict) else None,
        rpc_lp_mint=rpc_lp if isinstance(rpc_lp, dict) else None,
        rpc_lp_largest=largest,
        rpc_lp_accounts=lp_accounts,
        rpc_locker_accounts=locker_accounts,
    )
    targets = github_targets(raw)
    if targets:
        raw.github = []
        for owner, repo in targets:
            item = fetch_github(client, owner, repo)
            raw.github.append(item)
            if item.error:
                note_error("github", item.error, mint)
    result = evaluate(raw, rules, moment)
    symbol = result.symbol
    store.add_snapshot(scan_id, mint, symbol, source, stamp, {"profile": profile})
    if pairs.ok:
        store.add_snapshot(scan_id, mint, symbol, "dex_pairs", stamp, pairs.data)
    if rug_data is not None:
        store.add_snapshot(scan_id, mint, symbol, "rugcheck", stamp, rug_data)
    if raw.rpc_mint is not None:
        store.add_snapshot(scan_id, mint, symbol, "rpc_mint", stamp, raw.rpc_mint)
    for check in result.checks:
        store.add_check(scan_id, mint, symbol, check.name, check.passed, check.reason, check.evidence, stamp)
    if not result.passed:
        reason = "; ".join(f"{c.name}: {c.reason}" for c in result.failures())
        store.add_decision(scan_id, mint, symbol, "skip_checklist", reason, stamp)
        return False, False, account, fresh
    if result.price_usd is None or result.liquidity_usd is None:
        store.add_decision(scan_id, mint, symbol, "skip_risk", "checklist passed without a usable price", stamp)
        return True, False, account, fresh
    opened = open_position(
        account,
        trade_id=uuid.uuid4().hex,
        mint=mint,
        symbol=symbol or mint[:6],
        pair_address=result.pair_address,
        mid=result.price_usd,
        liquidity_usd=result.liquidity_usd,
        rules=rules,
    )
    if opened[0] is None:
        store.add_decision(scan_id, mint, symbol, "skip_risk", str(opened[1]), stamp)
        return True, False, account, fresh
    account, event = opened
    fresh[mint] = float(result.price_usd)
    _record_event(store, meta, event, scan_id, stamp)
    store.add_decision(scan_id, mint, symbol, "opened", event.note, stamp)
    return True, True, account, fresh


def _lp_context(client: JsonClient, rug: dict | None):
    errors: list[tuple[str, str]] = []
    if not isinstance(rug, dict):
        return None, None, None, None, errors
    markets = [m for m in (rug.get("markets") or []) if isinstance(m, dict)]
    if not markets:
        return None, None, None, None, errors

    def usd(market: dict) -> float:
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
        try:
            return float(lp.get("quoteUSD") or 0) + float(lp.get("baseUSD") or 0)
        except (TypeError, ValueError):
            return 0.0

    dominant = max(markets, key=usd)
    lp = dominant.get("lp") if isinstance(dominant.get("lp"), dict) else {}
    lp_mint = dominant.get("mintLP") or lp.get("lpMint")
    rpc_lp = None
    largest = None
    accounts = None
    if isinstance(lp_mint, str) and lp_mint:
        fetched = rpc_account(client, lp_mint)
        if not fetched.ok:
            errors.append(("solana_rpc", fetched.error or "LP mint read failed"))
        rpc_lp = fetched.data if fetched.ok else None
        supply = _rpc_supply(rpc_lp)
        if supply is not None and supply > 0:
            largest_res = rpc_largest_accounts(client, lp_mint)
            if not largest_res.ok or not isinstance(largest_res.data, list):
                errors.append(("solana_rpc", largest_res.error or "LP largest accounts failed"))
            else:
                largest = list(largest_res.data)[:8]
                accounts = []
                for entry in largest:
                    address = entry.get("address") if isinstance(entry, dict) else None
                    if not address:
                        accounts.append(None)
                        continue
                    acct = rpc_account(client, address)
                    accounts.append(acct.data if acct.ok else None)
                    if not acct.ok:
                        errors.append(("solana_rpc", acct.error or "LP holder read failed"))
    locker_accounts = _locker_accounts(client, rug, errors)
    return rpc_lp, largest, accounts, locker_accounts, errors


def _locker_accounts(client: JsonClient, rug: dict, errors: list[tuple[str, str]]):
    raw = rug.get("lockers")
    ids: list[str] = []
    if isinstance(raw, dict) and raw:
        ids = [str(key) for key in list(raw)[:5]]
    elif isinstance(raw, list):
        for item in raw[:5]:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
    if not ids:
        return None
    found: dict = {}
    for account_id in ids:
        fetched = rpc_account(client, account_id)
        found[account_id] = fetched.data if fetched.ok else None
        if not fetched.ok:
            errors.append(("solana_rpc", fetched.error or "locker account read failed"))
    return found


def _rpc_supply(account: object) -> float | None:
    if not isinstance(account, dict):
        return None
    data = account.get("data")
    parsed = data.get("parsed") if isinstance(data, dict) else None
    info = parsed.get("info") if isinstance(parsed, dict) else None
    if not isinstance(info, dict) or "supply" not in info:
        return None
    try:
        return float(info["supply"])
    except (TypeError, ValueError):
        return None


def _commit_event(store, account, meta, event: TradeEvent, scan_id: str, stamp: str):
    from meme_terminal.risk import apply_event

    updated = apply_event(account, event)
    _record_event(store, meta, event, scan_id, stamp)
    return updated


def _record_event(store: Store, meta: dict, event: TradeEvent, scan_id: str, stamp: str) -> None:
    position = event.position
    prior = meta.get(position.trade_id, {})
    opened_at = prior.get("opened_at") or stamp
    closed_at = prior.get("closed_at")
    exit_reason = prior.get("exit_reason")
    if position.status == "closed":
        closed_at = closed_at or stamp
        exit_reason = event.note
    meta[position.trade_id] = {"opened_at": opened_at, "closed_at": closed_at, "exit_reason": exit_reason}
    store.upsert_trade(
        position,
        opened_at=opened_at,
        closed_at=closed_at,
        exit_reason=exit_reason,
        updated_at=stamp,
    )
    if event.kind == "mark" or event.fill is None:
        return
    fill = event.fill
    store.add_event(
        trade_id=position.trade_id,
        scan_id=scan_id,
        at=stamp,
        kind=event.kind,
        qty=fill.qty,
        mid=fill.mid,
        fill=fill.fill,
        fee_usd=fill.fee_usd,
        slippage_bps=fill.slippage_bps,
        cash_delta=fill.cash_delta,
        note=event.note,
    )


def _mint_ok(mint: str) -> bool:
    return 32 <= len(mint) <= 44 and all(ch in _BASE58 for ch in mint)


# Imported for type checkers that look at FetchResult in tests.
_ = FetchResult
