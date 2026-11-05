"""Paper position sizing, fills, and exits.

Prices are marks from a public API. Fills exist only inside this process.
There is no order type that leaves the machine.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

from meme_terminal.config import RULES, Rules, is_copy_entry_source, mcap_bucket

EventKind = Literal["open", "partial", "stop", "trail", "halt", "max_hold", "mark"]


@dataclass(frozen=True)
class Fill:
    mid: float
    fill: float
    qty: float
    notional_usd: float
    fee_usd: float
    slippage_bps: int
    cash_delta: float


@dataclass(frozen=True)
class Position:
    trade_id: str
    mint: str
    symbol: str
    pair_address: str | None
    qty: float
    remaining_qty: float
    entry_fill: float
    initial_notional_usd: float
    cost_remaining_usd: float
    cash_in_usd: float
    fees_usd: float
    realized_pnl_usd: float
    peak_mid: float
    half_sold: bool
    status: str
    liquidity_usd: float
    last_mid: float | None = None
    opened_at: datetime | None = None
    market_cap_usd: float | None = None
    mcap_bucket: str | None = None
    entry_source: str = "checklist"

    @property
    def open(self) -> bool:
        return self.status == "open" and self.remaining_qty > 1e-12


@dataclass(frozen=True)
class TradeEvent:
    kind: EventKind
    position: Position
    fill: Fill | None
    note: str


@dataclass(frozen=True)
class Account:
    cash_usd: float
    halted: bool
    positions: tuple[Position, ...] = ()

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.open]


def slippage_bps(notional_usd: float, liquidity_usd: float, rules: Rules = RULES) -> int:
    """Base half-spread plus a linear impact term, capped."""
    if notional_usd <= 0:
        return rules.base_slippage_bps
    if liquidity_usd <= 0:
        return rules.max_slippage_bps
    impact = rules.impact_slippage_coeff * (notional_usd / liquidity_usd)
    raw = int(round(rules.base_slippage_bps + impact))
    return max(0, min(rules.max_slippage_bps, raw))


def entry_fill(mid: float, notional_usd: float, liquidity_usd: float, rules: Rules = RULES) -> Fill:
    if mid <= 0:
        raise ValueError("entry mark must be positive")
    if notional_usd <= 0:
        raise ValueError("notional must be positive")
    bps = slippage_bps(notional_usd, liquidity_usd, rules)
    slip = bps / 10_000.0
    fill = mid * (1.0 + slip)
    qty = notional_usd / fill
    fee = rules.flat_fee_usd
    return Fill(
        mid=mid,
        fill=fill,
        qty=qty,
        notional_usd=notional_usd,
        fee_usd=fee,
        slippage_bps=bps,
        cash_delta=-(notional_usd + fee),
    )


def exit_fill(
    mid: float,
    qty: float,
    liquidity_usd: float,
    rules: Rules = RULES,
    extra_adverse_pct: float = 0.0,
) -> Fill:
    if mid <= 0 or qty <= 0:
        raise ValueError("exit mark and quantity must be positive")
    # Impact uses the dollar size of the sale at the mid, before slippage.
    notional = qty * mid
    bps = slippage_bps(notional, liquidity_usd, rules)
    slip = bps / 10_000.0
    gap = extra_adverse_pct if extra_adverse_pct > 0 else 0.0
    fill = mid * (1.0 - slip) * (1.0 - gap)
    proceeds = qty * fill
    fee = rules.flat_fee_usd
    return Fill(
        mid=mid,
        fill=fill,
        qty=qty,
        notional_usd=proceeds,
        fee_usd=fee,
        slippage_bps=bps,
        cash_delta=proceeds - fee,
    )


def position_size_usd(cash_usd: float, rules: Rules = RULES) -> float | None:
    """Fixed size, or the floor of the band when cash cannot cover the target."""
    target = rules.position_size_usd
    fee = rules.flat_fee_usd
    if cash_usd >= target + fee:
        return target
    if cash_usd >= rules.position_size_min_usd + fee:
        return min(rules.position_size_max_usd, cash_usd - fee)
    return None


def can_open(account: Account, rules: Rules = RULES) -> str | None:
    """Return a rejection reason, or None when a new paper position is allowed."""
    if account.halted:
        return "global halt is active"
    if len(account.open_positions()) >= rules.max_open_positions:
        return f"already at {rules.max_open_positions} open positions"
    if position_size_usd(account.cash_usd, rules) is None:
        return "cash is below the minimum position size plus fee"
    return None


def open_position(
    account: Account,
    *,
    trade_id: str,
    mint: str,
    symbol: str,
    pair_address: str | None,
    mid: float,
    liquidity_usd: float,
    rules: Rules = RULES,
    entry_source: str = "checklist",
    market_cap_usd: float | None = None,
    opened_at: datetime | None = None,
) -> tuple[Account, TradeEvent] | tuple[None, str]:
    # Copy / FOMO wallet alerts never open a position. The flag is audited
    # False; this refusal does not consult it, so flipping the flag is not enough.
    if is_copy_entry_source(entry_source):
        return None, "tracked-wallet copy signal is information only and cannot open a position"
    reason = can_open(account, rules)
    if reason:
        return None, reason
    if mid <= 0 or liquidity_usd < rules.min_liquidity_usd:
        return None, "mark or liquidity is not tradable"
    notional = position_size_usd(account.cash_usd, rules)
    assert notional is not None
    fill = entry_fill(mid, notional, liquidity_usd, rules)
    when = opened_at or datetime.now(timezone.utc)
    position = Position(
        trade_id=trade_id,
        mint=mint,
        symbol=symbol or mint[:6],
        pair_address=pair_address,
        qty=fill.qty,
        remaining_qty=fill.qty,
        entry_fill=fill.fill,
        initial_notional_usd=notional,
        cost_remaining_usd=notional,
        cash_in_usd=0.0,
        fees_usd=fill.fee_usd,
        realized_pnl_usd=-fill.fee_usd,
        peak_mid=mid,
        half_sold=False,
        status="open",
        liquidity_usd=liquidity_usd,
        last_mid=mid,
        opened_at=when,
        market_cap_usd=market_cap_usd,
        mcap_bucket=mcap_bucket(market_cap_usd),
        entry_source=entry_source,
    )
    updated = Account(
        cash_usd=account.cash_usd + fill.cash_delta,
        halted=account.halted,
        positions=account.positions + (position,),
    )
    return updated, TradeEvent("open", position, fill, f"paper buy ${notional:.2f} at {fill.fill:.8f}")


def _apply_exit(
    position: Position,
    *,
    kind: EventKind,
    qty: float,
    mid: float,
    liquidity_usd: float,
    note: str,
    rules: Rules,
    close: bool,
    half_sold: bool,
) -> TradeEvent:
    """Book an exit.

    The entry fee is recognized when the position opens (`realized_pnl_usd`
    starts at -fee). Each exit adds sale proceeds after the exit fee, minus
    the cost basis of the quantity sold. A full round trip therefore equals
    net sale proceeds minus the entry notional minus the entry fee.
    """
    extra = rules.stop_gap_extra_pct if kind == "stop" else 0.0
    fill = exit_fill(mid, qty, liquidity_usd, rules, extra_adverse_pct=extra)
    cost = position.cost_remaining_usd * (qty / position.remaining_qty)
    remaining = position.remaining_qty - qty
    realized = position.realized_pnl_usd + fill.cash_delta - cost
    new = replace(
        position,
        remaining_qty=max(0.0, remaining),
        cost_remaining_usd=max(0.0, position.cost_remaining_usd - cost),
        cash_in_usd=position.cash_in_usd + fill.cash_delta,
        fees_usd=position.fees_usd + fill.fee_usd,
        realized_pnl_usd=realized,
        peak_mid=max(position.peak_mid, mid),
        half_sold=half_sold or position.half_sold,
        status="closed" if close or remaining <= 1e-9 else "open",
        liquidity_usd=liquidity_usd,
        last_mid=mid,
    )
    return TradeEvent(kind, new, fill, note)


def mark_position(
    position: Position,
    mid: float | None,
    liquidity_usd: float | None,
    rules: Rules = RULES,
    now: datetime | None = None,
) -> TradeEvent | None:
    """Apply stop, partial, trail, or the max-hold flatten. None means hold."""
    if not position.open:
        return None
    if mid is None or mid <= 0:
        return None
    liquidity = liquidity_usd if liquidity_usd and liquidity_usd > 0 else position.liquidity_usd
    peak = max(position.peak_mid, mid)
    updated = replace(position, peak_mid=peak, last_mid=mid, liquidity_usd=liquidity)
    stop_px = updated.entry_fill * (1.0 + rules.stop_loss_pct)
    if not updated.half_sold and mid <= stop_px:
        return _apply_exit(
            updated,
            kind="stop",
            qty=updated.remaining_qty,
            mid=mid,
            liquidity_usd=liquidity,
            note="stop-loss at -25% from the entry fill, plus the modeled stop gap",
            rules=rules,
            close=True,
            half_sold=False,
        )
    if _held_too_long(updated, now, rules):
        return _apply_exit(
            updated,
            kind="max_hold",
            qty=updated.remaining_qty,
            mid=mid,
            liquidity_usd=liquidity,
            note=f"max hold of {rules.max_hold_minutes} minutes",
            rules=rules,
            close=True,
            half_sold=updated.half_sold,
        )
    if not updated.half_sold and mid >= updated.entry_fill * rules.take_profit_multiple:
        qty = min(updated.remaining_qty, updated.qty * 0.5)
        close = qty >= updated.remaining_qty - 1e-12
        return _apply_exit(
            updated,
            kind="partial",
            qty=qty,
            mid=mid,
            liquidity_usd=liquidity,
            note=f"sold half at {rules.take_profit_multiple:g}x",
            rules=rules,
            close=close,
            half_sold=True,
        )
    if updated.half_sold and mid <= peak * (1.0 - rules.trail_drop_from_peak):
        return _apply_exit(
            updated,
            kind="trail",
            qty=updated.remaining_qty,
            mid=mid,
            liquidity_usd=liquidity,
            note=f"trailed out on a {rules.trail_drop_from_peak:.0%} drop from the observed peak",
            rules=rules,
            close=True,
            half_sold=True,
        )
    if updated.peak_mid != position.peak_mid or updated.last_mid != position.last_mid:
        return TradeEvent("mark", updated, None, "mark updated")
    return None


def _held_too_long(position: Position, now: datetime | None, rules: Rules) -> bool:
    if now is None or position.opened_at is None:
        return False
    opened = position.opened_at
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    minutes = (now - opened).total_seconds() / 60.0
    return minutes >= rules.max_hold_minutes


def apply_event(account: Account, event: TradeEvent) -> Account:
    """Replace the matching position and apply the cash delta."""
    positions = []
    for current in account.positions:
        if current.trade_id == event.position.trade_id:
            positions.append(event.position)
        else:
            positions.append(current)
    cash = account.cash_usd
    if event.fill is not None and event.kind != "open":
        cash += event.fill.cash_delta
    return Account(cash_usd=cash, halted=account.halted, positions=tuple(positions))


def mark_to_bid(position: Position, mid: float, rules: Rules = RULES) -> float:
    bps = slippage_bps(position.remaining_qty * mid, position.liquidity_usd, rules)
    return position.remaining_qty * mid * (1.0 - bps / 10_000.0)


def equity(account: Account, marks: dict[str, float | None], rules: Rules = RULES) -> float | None:
    """Paper equity. None when an open position has no fresh mark (do not pretend it is zero)."""
    total = account.cash_usd
    for position in account.open_positions():
        mid = marks.get(position.mint)
        if mid is None or mid <= 0:
            return None
        total += mark_to_bid(position, mid, rules)
    return total


def flatten(account: Account, marks: dict[str, float | None], rules: Rules = RULES) -> tuple[Account, list[TradeEvent]]:
    """Close every priced position. Used only after the halt line is hit."""
    events: list[TradeEvent] = []
    current = account
    for position in list(account.open_positions()):
        mid = marks.get(position.mint)
        if mid is None or mid <= 0:
            continue
        liquidity = position.liquidity_usd
        event = _apply_exit(
            position,
            kind="halt",
            qty=position.remaining_qty,
            mid=mid,
            liquidity_usd=liquidity,
            note="flattened because equity reached the global halt",
            rules=rules,
            close=True,
            half_sold=position.half_sold,
        )
        current = apply_event(current, event)
        events.append(event)
    return replace(current, halted=True), events


def needs_halt(account: Account, marks: dict[str, float | None], rules: Rules = RULES) -> bool:
    value = equity(account, marks, rules)
    if value is None:
        return False
    return value <= rules.halt_equity_usd
