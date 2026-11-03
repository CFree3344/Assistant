"""Auditable paper-trading rules.

Every threshold the checklist and the simulator use lives here. Change a rule
by editing this module, not by scattering literals through the engine.

Phase-1 sizing
--------------
The paper book is $1,000 so a two-week sample can absorb losers. Position
size stays inside the $50–$100 band you would actually use on a $300 live
bankroll. The paper book is not permission to bet more. A live-equivalent
P&L column in the scorecard rescales results by 300/1000; the go/no-go rule
uses the simulated dollars themselves.

This module has no network, no keypair, and no transaction code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


# --- Bankroll and bet size (paper dollars) ---------------------------------

PAPER_BANKROLL_USD = 1000.0
LIVE_EQUIVALENT_BANKROLL_USD = 300.0
POSITION_SIZE_USD = 75.0
POSITION_SIZE_MIN_USD = 50.0
POSITION_SIZE_MAX_USD = 100.0
MAX_OPEN_POSITIONS = 3
HALT_EQUITY_USD = 400.0

# --- Exits ------------------------------------------------------------------

STOP_LOSS_PCT = -0.25
TAKE_PROFIT_MULTIPLE = 2.0
TRAIL_DROP_FROM_PEAK = 0.30

# Flat fee charged on every fill (entry, partial take-profit, final exit, halt).
FLAT_FEE_USD = 0.95

# Slippage in basis points: a 1.5% base plus impact proportional to
# notional / pool liquidity. Capped so a thin remaining pool cannot
# invent an infinite fill.
BASE_SLIPPAGE_BPS = 150
IMPACT_SLIPPAGE_COEFF = 10_000.0
MAX_SLIPPAGE_BPS = 800

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

# Holder gates. Pool, burn, and locker accounts are removed before the top-10 sum.
MAX_TOP10_HOLDER_PCT = 40.0
MAX_DEV_HOLDER_PCT = 10.0
MAX_BUNDLE_PCT = 20.0

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
    halt_equity_usd: float = HALT_EQUITY_USD
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_multiple: float = TAKE_PROFIT_MULTIPLE
    trail_drop_from_peak: float = TRAIL_DROP_FROM_PEAK
    flat_fee_usd: float = FLAT_FEE_USD
    base_slippage_bps: int = BASE_SLIPPAGE_BPS
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
    max_top10_holder_pct: float = MAX_TOP10_HOLDER_PCT
    max_dev_holder_pct: float = MAX_DEV_HOLDER_PCT
    max_bundle_pct: float = MAX_BUNDLE_PCT
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
    if rules.stop_loss_pct >= 0 or rules.stop_loss_pct <= -1:
        raise RuntimeError("stop-loss must be a negative fraction above -100%")
    if rules.take_profit_multiple < 2:
        raise RuntimeError("take-profit multiple must be at least 2x")
    if not 0 < rules.trail_drop_from_peak < 1:
        raise RuntimeError("trail drop must be a fraction between 0 and 1")
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
    """Rescale a paper P&L onto the $300 live book. Informational only."""
    return paper_pnl * (rules.live_equivalent_bankroll_usd / rules.paper_bankroll_usd)
