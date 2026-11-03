"""Mocked DexScreener, RugCheck, RPC, and GitHub payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meme_terminal.checklist import GithubRepoInput, RawInputs
from meme_terminal.config import (
    TOKEN_PROGRAM,
    WSOL_MINT,
    KNOWN_LOCKER_PROGRAMS,
)

NOW = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)
GOOD = "GoodMint11111111111111111111111111111111"
BAD = "BadMint111111111111111111111111111111111"
CREATOR = "Creator111111111111111111111111111111111"
POOL = "AmmAcct111111111111111111111111111111111"
LP = "LpMint1111111111111111111111111111111111"
PAIR = "PairAcct11111111111111111111111111111111"
STREAMFLOW = "strmRqUCoQUgGUan5YhzUZa6KqdzwX5L6FpUxfmKg5m"

assert STREAMFLOW in KNOWN_LOCKER_PROGRAMS


def _b58_ok(value: str) -> None:
    alphabet = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    assert 32 <= len(value) <= 44, len(value)
    assert set(value) <= alphabet


for _mint in (GOOD, BAD, CREATOR, POOL, LP, PAIR):
    _b58_ok(_mint)


def rpc_mint(supply: str = "1000000000000", mint_authority=None, freeze_authority=None, extensions=None, owner=TOKEN_PROGRAM):
    info = {
        "mintAuthority": mint_authority,
        "freezeAuthority": freeze_authority,
        "supply": supply,
        "decimals": 6,
        "isInitialized": True,
    }
    if extensions is not None:
        info["extensions"] = extensions
    return {
        "owner": owner,
        "executable": False,
        "data": {"parsed": {"type": "mint", "info": info}, "program": "spl-token"},
    }


def dex_pair(
    mint: str = GOOD,
    price: str = "0.001",
    *,
    symbol: str = "GOOD",
    name: str = "Good Boy",
    liquidity: float = 20_000,
    volume: float = 15_000,
    buys: int = 80,
    sells: int = 70,
    age: timedelta = timedelta(hours=2),
    socials: bool = True,
):
    created = int((NOW - age).timestamp() * 1000)
    pair = {
        "chainId": "solana",
        "dexId": "pumpswap",
        "pairAddress": PAIR,
        "baseToken": {"address": mint, "name": name, "symbol": symbol},
        "quoteToken": {"address": WSOL_MINT, "name": "Wrapped SOL", "symbol": "SOL"},
        "priceUsd": price,
        "liquidity": {"usd": liquidity, "base": 1, "quote": 1},
        "volume": {"m5": volume, "h1": volume, "h6": volume, "h24": volume},
        "txns": {
            "m5": {"buys": buys, "sells": sells},
            "h1": {"buys": buys, "sells": sells},
            "h6": {"buys": buys, "sells": sells},
            "h24": {"buys": buys, "sells": sells},
        },
        "pairCreatedAt": created,
        "info": {"socials": [], "websites": []},
    }
    if socials:
        pair["info"] = {
            "socials": [{"type": "twitter", "url": "https://x.com/goodboy"}],
            "websites": [{"url": "https://good.example"}],
        }
    return pair


def rug_report(
    mint: str = GOOD,
    *,
    mint_authority=None,
    freeze_authority=None,
    locked_pct: float = 100,
    quote_usd: float = 12_000,
    base_usd: float = 8_000,
    lp_supply: float = 0,
    extra_market: dict | None = None,
    holders: list | None = None,
    creator: str | None = CREATOR,
    creator_balance: float = 0,
    networks: list | None = None,
    lockers: dict | None = None,
    rugged: bool = False,
    token_program: str = TOKEN_PROGRAM,
    token_extensions: dict | None = None,
):
    if holders is None:
        holders = [{"address": "LiqA111111111111111111111111111111111111", "owner": POOL, "pct": 30.0, "insider": False}]
        holders += [
            {
                "address": f"Wallet{i}111111111111111111111111111111111",
                "owner": f"Owner{i}1111111111111111111111111111111111",
                "pct": 2.0,
                "insider": False,
            }
            for i in range(10)
        ]
    market = {
        "pubkey": POOL,
        "marketType": "pump_fun_amm",
        "mintLP": LP,
        "liquidityA": "LiqA111111111111111111111111111111111111",
        "liquidityB": "LiqB111111111111111111111111111111111111",
        "mintLPAccount": {"supply": lp_supply, "decimals": 9, "mintAuthority": None, "freezeAuthority": None},
        "lp": {
            "lpMint": LP,
            "quoteUSD": quote_usd,
            "baseUSD": base_usd,
            "lpLockedPct": locked_pct,
            "lpUnlocked": 0,
            "lpCurrentSupply": lp_supply,
        },
    }
    markets = [market]
    if extra_market:
        markets.append(extra_market)
    report = {
        "mint": mint,
        "tokenProgram": token_program,
        "creator": creator,
        "creatorBalance": creator_balance,
        "mintAuthority": mint_authority,
        "freezeAuthority": freeze_authority,
        "rugged": rugged,
        "token": {
            "mintAuthority": mint_authority,
            "freezeAuthority": freeze_authority,
            "supply": 1_000_000_000_000,
            "decimals": 6,
        },
        "topHolders": holders,
        "insiderNetworks": [] if networks is None else networks,
        "markets": markets,
        "knownAccounts": {POOL: {"name": "Pump Fun AMM", "type": "AMM"}},
        "lockers": {} if lockers is None else lockers,
    }
    if token_extensions is not None:
        report["token_extensions"] = token_extensions
    return report


def passing_raw(**overrides) -> RawInputs:
    raw = RawInputs(
        mint=GOOD,
        dex_pairs=[dex_pair()],
        profile={"chainId": "solana", "tokenAddress": GOOD, "description": "a dog coin", "links": []},
        rug_report=rug_report(),
        rpc_mint=rpc_mint(),
        rpc_lp_mint=rpc_mint(supply="0"),
    )
    for key, value in overrides.items():
        setattr(raw, key, value)
    return raw


def github_ok(owner: str = "acme", repo: str = "agent") -> GithubRepoInput:
    commits = []
    for i in range(1, 6):
        commits.append(
            {
                "commit": {
                    "message": f"fix the parser path {i}",
                    "author": {"date": (NOW - timedelta(days=i)).isoformat()},
                }
            }
        )
    return GithubRepoInput(
        owner=owner,
        repo=repo,
        repo_json={
            "id": 1,
            "full_name": f"{owner}/{repo}",
            "private": False,
            "fork": False,
            "archived": False,
            "disabled": False,
            "size": 240,
            "created_at": (NOW - timedelta(days=400)).isoformat(),
            "pushed_at": (NOW - timedelta(days=1)).isoformat(),
        },
        commits_json=commits,
    )
