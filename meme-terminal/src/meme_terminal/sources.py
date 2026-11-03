"""Public, read-only market and safety sources.

DexScreener documents token profiles, boosts, and pair lookups at
https://docs.dexscreener.com/api/reference (300 requests/minute on pair routes,
60/minute on the profile and boost lists). RugCheck's full report is
GET https://api.rugcheck.xyz/v1/tokens/{mint}/report — swagger marks it as
authenticated, and a keyless GET still returned 200 when this terminal was
written. If that closes, the checklist fails closed. Solana RPC is limited to
getAccountInfo and getTokenLargestAccounts. Solscan's current holder API is
the paid Pro API, so it is not called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from meme_terminal.checklist import GithubRepoInput
from meme_terminal.config import SOLANA_RPC_URLS
from meme_terminal.http_client import FetchResult

DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz"
GITHUB = "https://api.github.com"


class JsonClient(Protocol):
    def get_json(self, url: str) -> FetchResult: ...

    def post_json(self, url: str, payload: dict[str, Any]) -> FetchResult: ...


@dataclass
class Seed:
    mint: str
    source: str
    profile: dict[str, Any] | None = None


@dataclass
class Discovery:
    seeds: list[Seed] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def profiles_url() -> str:
    return f"{DEX}/token-profiles/latest/v1"


def boosts_url() -> str:
    return f"{DEX}/token-boosts/latest/v1"


def takeovers_url() -> str:
    return f"{DEX}/community-takeovers/latest/v1"


def pairs_url(mint: str) -> str:
    return f"{DEX}/token-pairs/v1/solana/{mint}"


def tokens_url(mints: list[str]) -> str:
    joined = ",".join(mints)
    return f"{DEX}/tokens/v1/solana/{joined}"


def rug_report_url(mint: str) -> str:
    return f"{RUG}/v1/tokens/{mint}/report"


def new_tokens_url() -> str:
    return f"{RUG}/v1/stats/new_tokens"


def github_repo_url(owner: str, repo: str) -> str:
    return f"{GITHUB}/repos/{owner}/{repo}"


def github_commits_url(owner: str, repo: str) -> str:
    return f"{GITHUB}/repos/{owner}/{repo}/commits?per_page=10"


def discover(client: JsonClient) -> Discovery:
    found = Discovery()
    profiles = _rows(client, profiles_url(), "dexscreener_profiles", found)
    boosts = _rows(client, boosts_url(), "dexscreener_boosts", found)
    takeovers = _rows(client, takeovers_url(), "dexscreener_takeovers", found)
    new_tokens = _rows(client, new_tokens_url(), "rugcheck_new_tokens", found)
    ordered: list[Seed] = []
    ordered.extend(_seeds_from_dex(profiles, "dex_profile"))
    ordered.extend(_seeds_from_dex(boosts, "dex_boost"))
    ordered.extend(_seeds_from_dex(takeovers, "dex_takeover"))
    ordered.extend(_seeds_from_rug_new(new_tokens))
    seen: set[str] = set()
    for seed in ordered:
        if seed.mint in seen:
            # Keep the first profile, which is the one most likely to carry links.
            continue
        seen.add(seed.mint)
        found.seeds.append(seed)
    return found


def fetch_pairs(client: JsonClient, mint: str) -> FetchResult:
    return client.get_json(pairs_url(mint))


def fetch_marks(client: JsonClient, mints: list[str]) -> FetchResult:
    if not mints:
        return FetchResult(True, 200, [], None, tokens_url([]))
    return client.get_json(tokens_url(mints))


def fetch_rug(client: JsonClient, mint: str) -> FetchResult:
    return client.get_json(rug_report_url(mint))


def fetch_github(client: JsonClient, owner: str, repo: str) -> GithubRepoInput:
    repo_res = client.get_json(github_repo_url(owner, repo))
    if not repo_res.ok:
        return GithubRepoInput(owner, repo, error=repo_res.error or "github repo request failed")
    commits_res = client.get_json(github_commits_url(owner, repo))
    if not commits_res.ok:
        return GithubRepoInput(
            owner,
            repo,
            repo_json=repo_res.data if isinstance(repo_res.data, dict) else None,
            error=commits_res.error or "github commits request failed",
        )
    return GithubRepoInput(
        owner,
        repo,
        repo_json=repo_res.data if isinstance(repo_res.data, dict) else None,
        commits_json=commits_res.data if isinstance(commits_res.data, (list, dict)) else None,
    )


def rpc_account(client: JsonClient, address: str, urls: tuple[str, ...] = SOLANA_RPC_URLS) -> FetchResult:
    return _rpc(client, "getAccountInfo", [address, {"encoding": "jsonParsed"}], urls)


def rpc_largest_accounts(client: JsonClient, mint: str, urls: tuple[str, ...] = SOLANA_RPC_URLS) -> FetchResult:
    return _rpc(client, "getTokenLargestAccounts", [mint], urls)


def _rpc(client: JsonClient, method: str, params: list[Any], urls: tuple[str, ...]) -> FetchResult:
    last = FetchResult(False, None, None, "no rpc endpoint configured", urls[0] if urls else "")
    for url in urls:
        result = client.post_json(
            url,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        if not result.ok or not isinstance(result.data, dict):
            last = result
            continue
        value = (result.data.get("result") or {}).get("value") if isinstance(result.data.get("result"), dict) else None
        if result.data.get("result") is None and result.data.get("error"):
            last = FetchResult(False, result.status, result.data, "rpc error", url)
            continue
        return FetchResult(True, result.status, value, None, url)
    return last


def _rows(client: JsonClient, url: str, source: str, found: Discovery) -> list[Any]:
    result = client.get_json(url)
    if not result.ok:
        found.errors.append((source, result.error or "request failed"))
        return []
    if isinstance(result.data, list):
        return result.data
    if result.data is None:
        found.errors.append((source, "empty response"))
        return []
    found.errors.append((source, "response was not a list"))
    return []


def _seeds_from_dex(rows: list[Any], source: str) -> list[Seed]:
    seeds: list[Seed] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("chainId") != "solana":
            continue
        mint = row.get("tokenAddress")
        if not isinstance(mint, str):
            continue
        seeds.append(Seed(mint=mint, source=source, profile=row))
    return seeds


def _seeds_from_rug_new(rows: list[Any]) -> list[Seed]:
    seeds: list[Seed] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mint = row.get("mint")
        if isinstance(mint, str):
            seeds.append(Seed(mint=mint, source="rugcheck_new", profile=None))
    return seeds
