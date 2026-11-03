"""Paper risk rules. No network."""

from meme_terminal.config import RULES, PAPER_BANKROLL_USD, HALT_EQUITY_USD, FLAT_FEE_USD
from meme_terminal.reporting import decide, max_drawdown
from meme_terminal.risk import (
    Account,
    can_open,
    entry_fill,
    equity,
    exit_fill,
    flatten,
    mark_position,
    needs_halt,
    open_position,
    position_size_usd,
    slippage_bps,
)


def _account(cash=PAPER_BANKROLL_USD):
    return Account(cash_usd=cash, halted=False, positions=())


def _open(mid=1.0, liquidity=20_000, cash=PAPER_BANKROLL_USD):
    account, event = open_position(
        _account(cash),
        trade_id="t1",
        mint="Mint",
        symbol="GOOD",
        pair_address="pair",
        mid=mid,
        liquidity_usd=liquidity,
    )
    assert event.fill is not None
    return account, event


def test_constants_match_the_phase1_rules():
    assert RULES.paper_bankroll_usd == 1000
    assert RULES.live_equivalent_bankroll_usd == 300
    assert RULES.position_size_min_usd <= RULES.position_size_usd <= RULES.position_size_max_usd
    assert RULES.position_size_usd == 75
    assert RULES.max_open_positions == 3
    assert RULES.stop_loss_pct == -0.25
    assert RULES.take_profit_multiple == 2
    assert RULES.trail_drop_from_peak == 0.30
    assert RULES.halt_equity_usd == HALT_EQUITY_USD == 400
    assert RULES.flat_fee_usd == FLAT_FEE_USD == 0.95


def test_slippage_grows_as_the_pool_gets_thinner_and_never_exceeds_the_cap():
    small = slippage_bps(75, 100_000)
    thin = slippage_bps(75, 10_000)
    empty = slippage_bps(75, 0)
    assert small < thin < empty
    assert empty == RULES.max_slippage_bps
    assert thin >= RULES.base_slippage_bps


def test_entry_charges_the_flat_fee_and_buys_fewer_tokens_than_the_mid():
    fill = entry_fill(1.0, 75, 20_000)
    assert fill.fee_usd == 0.95
    assert fill.cash_delta == -(75 + 0.95)
    assert fill.fill > 1.0
    assert fill.qty == 75 / fill.fill
    account, event = _open()
    assert account.cash_usd == 1000 - 75 - 0.95
    assert event.position.realized_pnl_usd == -0.95


def test_size_stays_inside_the_band():
    assert position_size_usd(1000) == 75
    assert position_size_usd(60) == 60 - 0.95
    assert position_size_usd(40) is None


def test_stop_loss_is_worse_than_25_percent_after_fees_and_exit_slippage():
    account, event = _open(mid=1.0, liquidity=20_000)
    position = event.position
    stop_mid = position.entry_fill * 0.75
    action = mark_position(position, stop_mid, 20_000)
    assert action is not None
    assert action.kind == "stop"
    assert action.position.status == "closed"
    # Round trip: sale proceeds after the exit fee, minus notional, minus the entry fee.
    assert action.position.realized_pnl_usd == pytest_approx(
        action.fill.cash_delta - position.initial_notional_usd - RULES.flat_fee_usd
    )
    assert action.position.realized_pnl_usd < -0.25 * 75
    assert action.position.fees_usd == 0.95 * 2


def test_half_is_sold_at_2x_and_the_trail_does_not_fire_before_that():
    account, event = _open(mid=1.0)
    position = event.position
    # A 50% rally is not a 2x on the fill, and it is not a stop.
    hold = mark_position(position, position.entry_fill * 1.5, 20_000)
    assert hold is not None and hold.kind == "mark" and hold.fill is None
    assert hold.position.half_sold is False

    two_x = position.entry_fill * 2
    partial = mark_position(position, two_x, 20_000)
    assert partial.kind == "partial"
    assert partial.position.half_sold
    assert partial.position.status == "open"
    assert partial.fill.qty == pytest_approx(position.qty * 0.5)
    assert partial.position.remaining_qty == pytest_approx(position.qty * 0.5)

    # Same print must not also trail out.
    assert partial.position.status == "open"

    peak = two_x
    still = mark_position(partial.position, peak * 0.8, 20_000)
    assert still.kind == "mark"
    trailed = mark_position(partial.position, peak * 0.7, 20_000)
    assert trailed.kind == "trail"
    assert trailed.position.status == "closed"
    # Full round trip identity, both exit fills.
    net_proceeds = partial.fill.cash_delta + trailed.fill.cash_delta
    assert trailed.position.realized_pnl_usd == pytest_approx(
        net_proceeds - position.initial_notional_usd - RULES.flat_fee_usd
    )
    assert trailed.position.fees_usd == pytest_approx(0.95 * 3)


def test_a_missing_mark_does_not_stop_out_or_halt():
    account, event = _open()
    assert mark_position(event.position, None, 20_000) is None
    marks = {event.position.mint: None}
    assert equity(account, marks) is None
    assert needs_halt(account, marks) is False


def test_halt_flattens_and_blocks_new_entries_at_400():
    # A single $75 bet cannot drag a $1,000 book to $400, so start from a book
    # that is already near the line and then mark the position down.
    account, event = _open(mid=1.0, liquidity=20_000, cash=450)
    marks = {event.position.mint: 0.01}
    assert equity(account, marks) < 400
    assert needs_halt(account, marks)
    flat, events = flatten(account, marks)
    assert flat.halted
    assert events[0].kind == "halt"
    assert flat.open_positions() == []
    assert can_open(flat) == "global halt is active"
    rejected, reason = open_position(
        flat,
        trade_id="nope",
        mint="Other",
        symbol="NO",
        pair_address=None,
        mid=1,
        liquidity_usd=50_000,
    )
    assert rejected is None
    assert "halt" in reason

    broke = Account(cash_usd=350, halted=False, positions=())
    assert needs_halt(broke, {})
    flat_cash, no_events = flatten(broke, {})
    assert flat_cash.halted and no_events == []


def test_three_open_positions_is_the_cap():
    account = _account()
    for i in range(3):
        account, _event = open_position(
            account,
            trade_id=f"t{i}",
            mint=f"M{i}",
            symbol=f"S{i}",
            pair_address=None,
            mid=1,
            liquidity_usd=20_000,
        )
    assert can_open(account).startswith("already at 3")


def test_exit_fill_is_below_the_mid():
    fill = exit_fill(2.0, 10, 20_000)
    assert fill.fill < 2.0
    assert fill.cash_delta == pytest_approx(10 * fill.fill - 0.95)


def test_memecoin_prices_keep_digits_below_one_cent():
    from meme_terminal.reporting import _px

    assert _px(4.17996e-05) == "$0.0000418"
    assert _px(1.25) == "$1.2500"


def test_scorecard_bar():
    assert decide(29, 10, 30)[0] == "NO-GO"
    assert decide(30, 0, 30)[0] == "NO-GO"
    assert decide(30, -1, 30)[0] == "NO-GO"
    assert decide(30, 0.01, 30)[0] == "GO"
    drop, pct = max_drawdown([1000, 1100, 800, 900])
    assert drop == 300
    assert pct == pytest_approx(300 / 1100)


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
