"""Auditable paper-trading rules.

Every threshold the checklist and the simulator use lives here. Change a rule
by editing this module, not by scattering literals through the engine.

Sizing
-------
The paper book stays $1,000 so a sample can absorb losers. The dedicated
live book Chad authorized is $500. The $50–$100 band sizes paper tickets
only. It is not a live sizing rule and this package does not place live
orders. A live-equivalent P&L column rescales paper results by 500/1000.
The two-week go/no-go rule still uses the simulated paper dollars.

The live halt is $350, a 30% drawdown from $500. The paper engine tracks a
$1,000 book, so its halt is the same fraction remaining: $700.

Exits are unchanged: -25% stop, sell half at 1.5x, then a 25% trail off the
observed peak, flatten at 4 hours, at most 3 open positions.

This module has no network, no keypair, and no transaction code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


# --- Bankroll and bet size (paper dollars) ---------------------------------

PAPER_BANKROLL_USD = 1000.0
LIVE_EQUIVALENT_BANKROLL_USD = 500.0
POSITION_SIZE_USD = 75.0
POSITION_SIZE_MIN_USD = 50.0
POSITION_SIZE_MAX_USD = 100.0
MAX_OPEN_POSITIONS = 3
# Live halt: $350 left on the $500 book (30% drawdown). Paper halt maps that
# same remaining fraction onto the $1,000 book.
LIVE_HALT_EQUITY_USD = 350.0
HALT_EQUITY_USD = LIVE_HALT_EQUITY_USD * (PAPER_BANKROLL_USD / LIVE_EQUIVALENT_BANKROLL_USD)

# Tracked-wallet and FOMO-copy alerts are information only. This flag is
# audited False at import. The entry path also refuses those sources even if
# a caller constructs a Rules copy with the flag flipped. There is no FOMO
# credential, wallet, or auto-buy path in this package.
ENTRY_SOURCE_COPY_ALLOWED = False
COPY_ENTRY_SOURCES = frozenset(
    {
        "fomo_copy",
        "tracked_wallet",
        "wallet_copy",
        "copy_trade",
        "copy",
    }
)

# --- Exits ------------------------------------------------------------------

STOP_LOSS_PCT = -0.25
# 1.5x partial is the edge rule. The old audit required 2x; that floor is gone.
TAKE_PROFIT_MULTIPLE = 1.5
TRAIL_DROP_FROM_PEAK = 0.25
# Flatten whatever is left after four hours, measured from the paper entry.
MAX_HOLD_MINUTES = 240

# Flat fee charged on every fill (entry, partial take-profit, final exit, halt).
FLAT_FEE_USD = 0.95

# Slippage in basis points: a 2.5% base plus impact proportional to
# notional / pool liquidity. Capped so a thin remaining pool cannot
# invent an infinite fill.
BASE_SLIPPAGE_BPS = 250
IMPACT_SLIPPAGE_COEFF = 10_000.0
MAX_SLIPPAGE_BPS = 800
# Extra adverse move applied only on stop exits, on top of slippage.
# Models a gap through the stop when the next scan is the first mark we see.
STOP_GAP_EXTRA_PCT = 0.10

# --- Safety checklist --------------------------------------------------------

MIN_LIQUIDITY_USD = 10_000.0
MIN_VOLUME_USD = 8_000.0
MIN_TXNS = 40
# Average print below this is treated as dust, regardless of buy/sell balance.
MIN_AVG_TRADE_USD = 1.0
# Balanced micro-prints plus huge turnover versus liquidity look like wash volume.
WASH_AVG_TRADE_USD = 5.0
WASH_VOLUME_TO_LIQUIDITY = 5.0
WASH_BUY_SELL_LOW = 0.75
WASH_BUY_SELL_HIGH = 1.33

# Do not compete with launch snipers.
MIN_AGE_MINUTES = 20
# No buy within an hour of a detectable pump.fun graduation (the pumpswap
# pair's creation time, or the earliest migrated pair when RugCheck marks
# the mint as pump.fun). Not applied to tokens that are not pump.fun.
MIN_MINUTES_SINCE_GRADUATION = 60

# Holder gates. Pool, burn, and locker accounts are removed before the top-10 sum.
MAX_TOP10_HOLDER_PCT = 40.0
MAX_DEV_HOLDER_PCT = 10.0
MAX_BUNDLE_PCT = 15.0
# Applied only when RugCheck (or the pair payload) actually includes a
# linked-early-buyer, sniper, or coordinated-buy percentage. The public
# report did not carry that field when this was written, and insider-network
# size is not used as a stand-in. The same cap covers coordinated buys.
MAX_LINKED_EARLY_BUYER_PCT = 25.0

# Graduated names outside this band are skipped. Missing market cap fails
# closed; it is not filled in.
MCAP_PREF_MIN_USD = 50_000.0
MCAP_PREF_MAX_USD = 250_000.0

# Late chase, in percent. A 1m or 5m print at the extreme level is already
# vertical. A print at the large level fails only when that same window's
# flow is present and weak. A missing tape is not a flow reading.
LATE_CHASE_M1_PCT = 25.0
LATE_CHASE_M5_PCT = 40.0
LATE_CHASE_EXTREME_M1_PCT = 50.0
LATE_CHASE_EXTREME_M5_PCT = 80.0
LATE_CHASE_MIN_PRINTS = 20
LATE_CHASE_MAX_BUY_SELL = 4.0

# Sell-side check for the paper ticket. Stricter than the 8% fill cap so a
# pool that only clears the liquidity floor can still be too thin to exit.
# Quote-side USD is applied only when a market actually reports it.
MAX_REASONABLE_EXIT_BPS = 300
MAX_SELL_QUOTE_IMPACT = 0.015

# The $50–$100 band is paper scoring only. Live ticket size is not taken from it.
PAPER_TICKET_BAND_APPLIES_TO_LIVE = False

# LP must be burned, or locked at least this long by a known program.
LP_MIN_LOCKED_PCT = 95.0
MIN_LOCK_DAYS = 180

# GitHub bar for projects that present as a launchpad, tool, or AI agent.
GITHUB_RECENT_DAYS = 120
GITHUB_MIN_SIZE_KB = 30
GITHUB_MIN_COMMITS = 3
GITHUB_MIN_FORK_OWN_COMMITS = 3

# How many new mints one scan will price and check. Keeps free-tier rate limits.
MAX_CANDIDATES_PER_SCAN = 12

# Pre-registered go / no-go bar for the scorecard.
SCORECARD_MIN_CALLS = 30

# Polite spacing between calls to each host, in seconds.
DEX_MIN_INTERVAL_S = 0.25
RUG_MIN_INTERVAL_S = 0.40
RPC_MIN_INTERVAL_S = 0.20
GITHUB_MIN_INTERVAL_S = 1.10

HTTP_TIMEOUT_S = 25.0
HTTP_RETRIES = 3

# Public read-only Solana RPC endpoints. Tried in order.
SOLANA_RPC_URLS = (
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
)

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MAJOR_QUOTES = frozenset({WSOL_MINT, USDC_MINT, USDT_MINT})

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# System program is what revoked authorities are sometimes serialized as.
REVOKED_AUTHORITY_SENTINELS = frozenset(
    {
        "",
        "none",
        "null",
        "11111111111111111111111111111111",
    }
)

# Incinerator address. Owning a token account here means those tokens are burned.
# Verified as a well-formed pubkey against mainnet RPC (the account itself holds no SOL).
BURN_OWNERS = frozenset(
    {
        "1nc1nerator11111111111111111111111111111111",
    }
)

# Locker programs confirmed executable on mainnet via getMultipleAccounts
# during development of this terminal:
#   Streamflow  strmRqUCoQUgGUan5YhzUZa6KqdzwX5L6FpUxfmKg5m
#   UNCX Raydium AMM v4 / smart / CPMM / CLMM (docs.uncx.network)
#   Jupiter Lock LocpQgucEQHbqNABEYvBvwoxCPsSbG91A1QaQhQQqjn (dev.jup.ag)
KNOWN_LOCKER_PROGRAMS = frozenset(
    {
        "strmRqUCoQUgGUan5YhzUZa6KqdzwX5L6FpUxfmKg5m",
        "GsSCS3vPWrtJ5Y9aEVVT65fmrex5P5RGHXdZvsdbWgfo",
        "UNCX77nZrA3TdAxMEggqG18xxpgiNGT6iqyynPwpoxN",
        "UNCXdvMRxvz91g3HqFmpZ5NgmL77UH4QRM4NfeL4mQB",
        "UNCXrB8cZXnmtYM1aSo1Wx3pQaeSZYuF2jCTesXvECs",
        "LocpQgucEQHbqNABEYvBvwoxCPsSbG91A1QaQhQQqjn",
    }
)

# Name fragments accepted only together with an allowlisted program id.
KNOWN_LOCKER_NAMES = frozenset(
    {
        "streamflow",
        "uncx",
        "unicrypt",
        "jupiter lock",
        "jupiter-lock",
    }
)

PRODUCT_PHRASES = (
    "launchpad",
    "launch pad",
    "ai agent",
    "ai-agent",
    "autonomous agent",
    "trading bot",
    "developer sdk",
    "dev sdk",
    "agent framework",
    "trading terminal",
    "trading tool",
    "ai tool",
    "onchain tool",
    "on-chain tool",
)

GITHUB_BOILERPLATE_MESSAGES = frozenset(
    {
        "initial commit",
        "first commit",
        "update readme",
        "update readme.md",
        "create readme",
        "create readme.md",
        "add readme",
        "add readme.md",
    }
)


@dataclass(frozen=True)
class Rules:
    """Snapshot of the constants. Tests build a copy when they need one change."""

    paper_bankroll_usd: float = PAPER_BANKROLL_USD
    live_equivalent_bankroll_usd: float = LIVE_EQUIVALENT_BANKROLL_USD
    position_size_usd: float = POSITION_SIZE_USD
    position_size_min_usd: float = POSITION_SIZE_MIN_USD
    position_size_max_usd: float = POSITION_SIZE_MAX_USD
    max_open_positions: int = MAX_OPEN_POSITIONS
    live_halt_equity_usd: float = LIVE_HALT_EQUITY_USD
    halt_equity_usd: float = HALT_EQUITY_USD
    entry_source_copy_allowed: bool = ENTRY_SOURCE_COPY_ALLOWED
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_multiple: float = TAKE_PROFIT_MULTIPLE
    trail_drop_from_peak: float = TRAIL_DROP_FROM_PEAK
    max_hold_minutes: int = MAX_HOLD_MINUTES
    flat_fee_usd: float = FLAT_FEE_USD
    base_slippage_bps: int = BASE_SLIPPAGE_BPS
    stop_gap_extra_pct: float = STOP_GAP_EXTRA_PCT
    impact_slippage_coeff: float = IMPACT_SLIPPAGE_COEFF
    max_slippage_bps: int = MAX_SLIPPAGE_BPS
    min_liquidity_usd: float = MIN_LIQUIDITY_USD
    min_volume_usd: float = MIN_VOLUME_USD
    min_txns: int = MIN_TXNS
    min_avg_trade_usd: float = MIN_AVG_TRADE_USD
    wash_avg_trade_usd: float = WASH_AVG_TRADE_USD
    wash_volume_to_liquidity: float = WASH_VOLUME_TO_LIQUIDITY
    wash_buy_sell_low: float = WASH_BUY_SELL_LOW
    wash_buy_sell_high: float = WASH_BUY_SELL_HIGH
    min_age_minutes: int = MIN_AGE_MINUTES
    min_minutes_since_graduation: int = MIN_MINUTES_SINCE_GRADUATION
    max_top10_holder_pct: float = MAX_TOP10_HOLDER_PCT
    max_dev_holder_pct: float = MAX_DEV_HOLDER_PCT
    max_bundle_pct: float = MAX_BUNDLE_PCT
    max_linked_early_buyer_pct: float = MAX_LINKED_EARLY_BUYER_PCT
    mcap_pref_min_usd: float = MCAP_PREF_MIN_USD
    mcap_pref_max_usd: float = MCAP_PREF_MAX_USD
    late_chase_m1_pct: float = LATE_CHASE_M1_PCT
    late_chase_m5_pct: float = LATE_CHASE_M5_PCT
    late_chase_extreme_m1_pct: float = LATE_CHASE_EXTREME_M1_PCT
    late_chase_extreme_m5_pct: float = LATE_CHASE_EXTREME_M5_PCT
    late_chase_min_prints: int = LATE_CHASE_MIN_PRINTS
    late_chase_max_buy_sell: float = LATE_CHASE_MAX_BUY_SELL
    max_reasonable_exit_bps: int = MAX_REASONABLE_EXIT_BPS
    max_sell_quote_impact: float = MAX_SELL_QUOTE_IMPACT
    paper_ticket_band_applies_to_live: bool = PAPER_TICKET_BAND_APPLIES_TO_LIVE
    lp_min_locked_pct: float = LP_MIN_LOCKED_PCT
    min_lock_days: int = MIN_LOCK_DAYS
    github_recent_days: int = GITHUB_RECENT_DAYS
    github_min_size_kb: int = GITHUB_MIN_SIZE_KB
    github_min_commits: int = GITHUB_MIN_COMMITS
    github_min_fork_own_commits: int = GITHUB_MIN_FORK_OWN_COMMITS
    max_candidates_per_scan: int = MAX_CANDIDATES_PER_SCAN
    scorecard_min_calls: int = SCORECARD_MIN_CALLS


RULES = Rules()


def _audit_rules(rules: Rules) -> None:
    if not (rules.position_size_min_usd <= rules.position_size_usd <= rules.position_size_max_usd):
        raise RuntimeError("position size must sit inside the configured min/max band")
    if rules.halt_equity_usd >= rules.paper_bankroll_usd:
        raise RuntimeError("halt equity must be below the paper bankroll")
    if rules.live_halt_equity_usd >= rules.live_equivalent_bankroll_usd:
        raise RuntimeError("live halt must be below the live bankroll")
    live_left = rules.live_halt_equity_usd / rules.live_equivalent_bankroll_usd
    paper_left = rules.halt_equity_usd / rules.paper_bankroll_usd
    if abs(live_left - paper_left) > 1e-9:
        raise RuntimeError("paper halt must match the live halt as a fraction of the book")
    if rules.entry_source_copy_allowed:
        raise RuntimeError("tracked-wallet copy signals must not auto-buy")
    if rules.stop_loss_pct >= 0 or rules.stop_loss_pct <= -1:
        raise RuntimeError("stop-loss must be a negative fraction above -100%")
    if rules.take_profit_multiple <= 1:
        raise RuntimeError("take-profit multiple must be above 1x")
    if not 0 < rules.trail_drop_from_peak < 1:
        raise RuntimeError("trail drop must be a fraction between 0 and 1")
    if rules.max_hold_minutes <= 0:
        raise RuntimeError("max hold must be a positive number of minutes")
    if not 0 <= rules.stop_gap_extra_pct < 0.5:
        raise RuntimeError("stop gap must be a fraction from 0 up to 50%")
    if rules.min_minutes_since_graduation < rules.min_age_minutes:
        raise RuntimeError("graduation wait must be at least the minimum pair age")
    if rules.paper_ticket_band_applies_to_live:
        raise RuntimeError("the $50–$100 ticket band is paper scoring only, not a live sizing rule")
    if rules.mcap_pref_min_usd <= 0 or rules.mcap_pref_min_usd >= rules.mcap_pref_max_usd:
        raise RuntimeError("graduated market-cap band is inverted")
    if rules.late_chase_m1_pct <= 0 or rules.late_chase_m1_pct >= rules.late_chase_extreme_m1_pct:
        raise RuntimeError("1m chase threshold must sit below the vertical threshold")
    if rules.late_chase_m5_pct <= 0 or rules.late_chase_m5_pct >= rules.late_chase_extreme_m5_pct:
        raise RuntimeError("5m chase threshold must sit below the vertical threshold")
    if rules.late_chase_min_prints <= 0 or rules.late_chase_max_buy_sell <= 1:
        raise RuntimeError("late-chase flow thresholds are not usable")
    if rules.max_reasonable_exit_bps >= rules.max_slippage_bps:
        raise RuntimeError("sell-exit impact gate must be stricter than the fill slippage cap")
    if not 0 < rules.max_sell_quote_impact < 0.25:
        raise RuntimeError("quote-side impact cap must be a fraction under 25%")
    if rules.flat_fee_usd < 0:
        raise RuntimeError("fee cannot be negative")
    if rules.live_equivalent_bankroll_usd <= 0 or rules.paper_bankroll_usd <= 0:
        raise RuntimeError("bankrolls must be positive")
    if rules.min_lock_days < 30:
        raise RuntimeError("minimum lock is too short to count as long-term")


_audit_rules(RULES)


def rules_dict(rules: Rules = RULES) -> dict:
    """JSON-safe copy stored with each scan so a report can show the rules used."""
    return asdict(rules)


def live_equivalent_pnl(paper_pnl: float, rules: Rules = RULES) -> float:
    """Rescale a paper P&L onto the $500 live book. Informational only."""
    return paper_pnl * (rules.live_equivalent_bankroll_usd / rules.paper_bankroll_usd)


def mcap_bucket(market_cap_usd: float | None) -> str:
    """Bucket a USD market cap. 50–250k is called out because that was the edge cohort."""
    if market_cap_usd is None or market_cap_usd <= 0:
        return "unknown"
    if market_cap_usd < 50_000:
        return "<50k"
    if market_cap_usd < 250_000:
        return "50-250k"
    if market_cap_usd < 1_000_000:
        return "250k-1m"
    if market_cap_usd < 10_000_000:
        return "1m-10m"
    return ">=10m"


def is_copy_entry_source(source: str | None) -> bool:
    """True for tracked-wallet / FOMO-copy labels. Those must not open a trade."""
    if not source:
        return False
    return source.strip().lower() in COPY_ENTRY_SOURCES
