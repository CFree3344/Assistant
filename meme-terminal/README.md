# Meme terminal (paper only)

A dry-run screener for new Solana memecoins. It reads public market data, applies a fixed safety checklist, and writes simulated entries and exits to a local DuckDB file.

It does not create a wallet, hold a private key, sign a transaction, or send an order. There is no code path that can. The go/no-go scorecard is a way to decide, after two weeks of paper results, whether a later live phase is even worth designing.

## What one scan does

1. Refreshes marks on open paper positions and applies the exit rules.
2. Pulls a small set of Solana candidates from DexScreener (latest profiles, boosts, community takeovers) and RugCheck's recently detected tokens.
3. Runs the checklist. Anything that cannot be verified is a fail, and the token is skipped with the reason stored.
4. Opens at most one new paper position per passing token, under the position cap.

Exits are evaluated at scan time. The trail only sees peaks from scans you actually ran, so a few scans a day will miss intraday spikes. That is recorded in the report rather than papered over.

## Rules

All of them live in `src/meme_terminal/config.py`.

| Rule | Value |
| --- | --- |
| Paper bankroll | $1,000 |
| Live-equivalent book | $500. Position size is the bet on that book, not a scaled-up bet on the $1,000. P&L rescales by 500/1000. |
| Position size | $75, and never outside $50–$100 |
| Max open | 3 |
| Stop | -25% from the entry fill, then an extra 10% modeled gap |
| Take profit | sell half at 1.5x |
| Trail | sell the rest on a 25% drop from the highest mark seen after the half sale |
| Max hold | 240 minutes, then the remainder is sold |
| Halt | $350 on the $500 live book (30% drawdown). Paper halt is the same fraction: $700. |
| Fee | $0.95 per fill (entry, half sale, final exit, halt) |
| Slippage | 2.5% plus a liquidity impact term, capped at 8% |
| Copy signals | Tracked-wallet and FOMO-copy alerts are information only. They never open a trade. |

Checklist, all required:

- Mint authority revoked, on RugCheck and on a parsed Solana RPC mint account.
- Freeze authority revoked, same two sources.
- No Token-2022 freeze, transfer fee, transfer hook, pause, or permanent delegate.
- At least 95% of liquidity burned, or locked at least 180 days by Streamflow, UNCX, or Jupiter Lock, with the locker account's owner confirmed on RPC. A lock claim with no unlock time fails. A short lock fails.
- Top 10 wallets (pools, burn addresses, and lockers removed) at or under 40%.
- Creator at or under 10%, and insider/bundle concentration at or under 15%.
- Linked early buyers at or under 25% when RugCheck actually reports that percentage. The public report has no such field today, so the gate is recorded as not applied instead of inventing a number from insider networks.
- At least $10,000 liquidity and $8,000 of volume that is not dust and does not look washed.
- Pair at least 20 minutes old.
- Pump.fun graduations (the pumpswap pair's creation time, when DexScreener or RugCheck shows one) must be at least 60 minutes old. A bonding-curve-only pump token is not bought. Tokens that are not pump.fun skip this gate.
- A website or social link on the DexScreener profile or pair.
- If the name, description, or links present the token as a launchpad, tool, or AI agent, every linked GitHub repo must exist, be non-trivial, and show recent commits. A fork with no commits of its own fails.

The scorecard says **GO** only after 30 entries and a positive net result after fees (open positions marked at the bid). Anything else is **NO-GO**.

## Setup

Python 3.11 or newer.

```bash
cd meme-terminal
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Optional: `GITHUB_TOKEN` raises the GitHub rate limit for product-style tokens. The scan runs without it. The token is sent only to `api.github.com` and is never written to the database.

## Run

```bash
python -m meme_terminal scan
python -m meme_terminal report --out sample-reports/daily.md
python -m meme_terminal scorecard --out sample-reports/scorecard.md
```

`scan` prints one summary line. The DuckDB file is `data/terminal.duckdb` (gitignored). Override it with `--db` or `MEME_TERMINAL_DB`.

`report` defaults to today's UTC date. Pass `--date 2026-11-03` for another day. The headline (equity, closed P&L, fees, skip count, API errors) is followed by the rows that add up to it. Failed checks and API errors are separate sections.

## Schedule

Four times a day is enough. This is not a sniper; the checklist skips anything younger than 20 minutes, and the trail only updates when a scan runs.

```cron
0 9,13,17,21 * * * cd /path/to/meme-terminal && .venv/bin/python -m meme_terminal scan >> data/scan.log 2>&1
5 21 * * * cd /path/to/meme-terminal && .venv/bin/python -m meme_terminal report --out data/daily-$(date -u +\%F).md
```

Run `scorecard` at the end of the two weeks, or any time you want the running tally.

## Data sources

Checked while this was written:

- DexScreener public API, no key: `GET /token-profiles/latest/v1`, `/token-boosts/latest/v1`, `/community-takeovers/latest/v1`, `/token-pairs/v1/solana/{mint}`, `/tokens/v1/solana/{mints}`. Pair routes are documented at 300 requests/minute; the list routes at 60. Docs: https://docs.dexscreener.com/api/reference
- RugCheck, no key on the reads used here: `GET /v1/stats/new_tokens` and `GET /v1/tokens/{mint}/report`. Swagger lists the full report as authenticated, and a keyless GET returned 200. If that stops, those checks fail closed. https://api.rugcheck.xyz/swagger/index.html
- Solana RPC `getAccountInfo` and `getTokenLargestAccounts` only, against `api.mainnet-beta.solana.com` and `solana-rpc.publicnode.com`.
- GitHub REST: `GET /repos/{owner}/{repo}` and `GET /repos/{owner}/{repo}/commits`.

The client sleeps between calls, retries 429 and 5xx, and refuses any RPC method outside the two account reads (plus `getMultipleAccounts`, which is unused by the scan). Solscan's holder API is the paid Pro API, so it is not called.

## What free APIs could not prove

These fail closed, on purpose:

- Team Finance, PinkLock, Fluxbeam, and Meteora lockers are not on the program allowlist. Their program ids were not confirmed for this build. A lock there fails until the id is added to `KNOWN_LOCKER_PROGRAMS` in `config.py`.
- Unlock times come from RugCheck's locker object. This program does not decode Streamflow or UNCX account bytes itself. A locker row with no timestamp is a fail, even if a human would call it locked.
- Holder and insider figures come from RugCheck's report. RPC is used to confirm mint authority, freeze authority, and the LP mint (supply, burn-address share, locker owner). The public RPC largest-accounts call is not a full holder census.
- Wash trading is a heuristic: too few prints, sub-dollar average size, or balanced micro-prints with huge volume versus liquidity. It is not a unique-wallet proof. Thin or odd volume fails.
- "Presents as a launchpad, tool, or AI agent" is detected from the DexScreener name, description, and links. The website body is not fetched. A claim that exists only on the site, with none of those phrases and no GitHub link, is not caught.
- RugCheck's full report may start requiring a key. When a request fails, the token is skipped.
- Prices between scans are invisible. Stops and trails fire on the next scan's mark, not on the path in between. Stop exits add a 10% gap on top of slippage to model that.
- RugCheck's public report has no linked-early-buyer or sniper percentage (checked against a live report: holder rows expose `pct` and `insider` only). `MAX_LINKED_EARLY_BUYER_PCT` is wired for an explicit field and is not filled in from bundle size.
- Tracked wallets and FOMO copy-trade feeds are not connected. Nothing in this repo stores FOMO credentials, and a copy-labeled signal cannot open a paper trade.

## Tests

```bash
pytest
```

Checklist and risk tests use mocked DexScreener, RugCheck, RPC, and GitHub payloads. One test runs a full scan against an in-memory fake client and checks that a clean token is paper-bought with a market-cap bucket, a token with a live mint authority is skipped, a later mark above 1.5x sells half, and the scorecard is NO-GO. Nothing in `src/` references signing, keypairs, or `sendTransaction`.
