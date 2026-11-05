"""Paper risk rules. No network."""

from datetime import datetime, timedelta, timezone

from meme_terminal.config import RULES, PAPER_BANKROLL_USD, FLAT_FEE_USD, mcap_bucket
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


def test_constants_match_the_edge_rules():
    assert RULES.paper_bankroll_usd == 1000
    assert RULES.live_equivalent_bankroll_usd == 500
    assert RULES.position_size_min_usd <= RULES.position_size_usd <= RULES.position_size_max_usd
    assert RULES.position_size_usd == 75
    assert RULES.max_open_positions == 3
    assert RULES.stop_loss_pct == -0.25
    assert RULES.take_profit_multiple == 1.5
    assert RULES.trail_drop_from_peak == 0.25
    assert RULES.max_hold_minutes == 240
    assert RULES.live_halt_equity_usd == 350
    assert RULES.halt_equity_usd == 700
    assert RULES.halt_equity_usd / RULES.paper_bankroll_usd == pytest_approx(350 / 500)
    assert RULES.entry_source_copy_allowed is False
    assert RULES.base_slippage_bps == 250
    assert RULES.stop_gap_extra_pct == pytest_approx(0.10)
    assert RULES.min_age_minutes == 20
    assert RULES.min_minutes_since_graduation == 60
    assert RULES.max_bundle_pct == 15
    assert RULES.max_linked_early_buyer_pct == 25
    assert RULES.flat_fee_usd == FLAT_FEE_USD == 0.95
    assert mcap_bucket(120_000) == "50-250k"
    assert mcap_bucket(49_999) == "<50k"
    assert mcap_bucket(250_000) == "250k-1m"


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
    plain = exit_fill(stop_mid, position.qty, 20_000)
    assert action.fill.fill == pytest_approx(plain.fill * (1.0 - RULES.stop_gap_extra_pct))


def test_half_is_sold_at_1_5x_and_the_trail_is_25_percent():
    account, event = _open(mid=1.0)
    position = event.position
    hold = mark_position(position, position.entry_fill * 1.2, 20_000)
    assert hold is not None and hold.kind == "mark" and hold.fill is None
    assert hold.position.half_sold is False

    target = position.entry_fill * 1.5
    partial = mark_position(position, target, 20_000)
    assert partial.kind == "partial"
    assert partial.position.half_sold
    assert partial.position.status == "open"
    assert partial.fill.qty == pytest_approx(position.qty * 0.5)
    assert partial.position.remaining_qty == pytest_approx(position.qty * 0.5)

    peak = target
    still = mark_position(partial.position, peak * 0.80, 20_000)
    assert still.kind == "mark"
    trailed = mark_position(partial.position, peak * 0.75, 20_000)
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


def test_halt_maps_the_350_live_line_onto_the_paper_book():
    # Paper halt is $700, the same 30% drawdown as $350 on the $500 live book.
    assert needs_halt(Account(cash_usd=800, halted=False, positions=()), {}) is False
    account, event = _open(mid=1.0, liquidity=20_000, cash=450)
    marks = {event.position.mint: 0.01}
    assert equity(account, marks) < RULES.halt_equity_usd
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


def test_copy_signals_never_open_even_if_the_flag_is_forced_on():
    from dataclasses import replace

    forced = replace(RULES, entry_source_copy_allowed=True)
    account, reason = open_position(
        _account(),
        trade_id="copy",
        mint="Mint",
        symbol="COPY",
        pair_address="pair",
        mid=1.0,
        liquidity_usd=20_000,
        rules=forced,
        entry_source="fomo_copy",
        market_cap_usd=120_000,
    )
    assert account is None
    assert "information only" in reason


def test_max_hold_flattens_the_remainder_after_240_minutes():
    _account_state, event = _open(mid=1.0)
    opened = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    position = event.position
    position = type(position)(**{**position.__dict__, "opened_at": opened})
    still = mark_position(position, position.entry_fill, 20_000, now=opened + timedelta(minutes=239))
    assert still is None or still.kind == "mark"
    expired = mark_position(position, position.entry_fill, 20_000, now=opened + timedelta(minutes=240))
    assert expired is not None
    assert expired.kind == "max_hold"
    assert expired.position.status == "closed"


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
