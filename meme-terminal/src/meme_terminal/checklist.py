"""Safety checklist and per-mint entry scorecard.

Required safety checks fail closed: a missing mint, freeze, pool, or RugCheck
report is a FAIL and the name is skipped. Optional fields (held-at-launch
bundle, linked early buyers, coordinated buys, the 1m/5m tape, and the
StalkChain, FOMO, X, and Padre slots) are NOT_APPLIED when the payload does
not contain them. A missing optional field is never filled in with demand,
flow, or a concentration number.

The entry verdict is GO only when no gate is FAIL. NOT_APPLIED does not vote
and does not open a trade. Nothing here sends a transaction or contacts the
network. FOMO and leaderboard payloads are information only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from meme_terminal.config import (
    BURN_OWNERS,
    GITHUB_BOILERPLATE_MESSAGES,
    KNOWN_LOCKER_NAMES,
    KNOWN_LOCKER_PROGRAMS,
    MAJOR_QUOTES,
    PRODUCT_PHRASES,
    REVOKED_AUTHORITY_SENTINELS,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    Rules,
    RULES,
    mcap_bucket,
)
from meme_terminal.risk import slippage_bps

PUMP_AMM_DEX_IDS = frozenset({"pumpswap"})
PUMP_BONDING_DEX_IDS = frozenset({"pumpfun", "pump.fun", "pump-fun"})
_EARLY_PCT_KEYS = (
    "linkedEarlyBuyersPct",
    "linked_early_buyer_pct",
    "earlyBuyerPct",
    "sniperPct",
    "snipersPct",
)
_EARLY_BLOCK_KEYS = ("linkedEarlyBuyers", "earlyBuyers", "snipers")
_COORD_PCT_KEYS = (
    "coordinatedBuyPct",
    "coordinated_buy_pct",
    "coordinatedBuysPct",
    "coordinatedPct",
)
_COORD_BLOCK_KEYS = ("coordinatedBuys", "coordinatedBuyers", "coordinated")
_LAUNCH_PCT_KEYS = (
    "bundleHeldAtLaunchPct",
    "heldAtLaunchPct",
    "launchBundlePct",
    "bundleAtLaunchPct",
    "launchBundleHeldPct",
)
_LAUNCH_HOLDING_KEYS = ("initialHolding", "launchHolding", "heldAtLaunch", "tokensAtLaunch")
_LAUNCH_ROW_FLAGS = ("bundledAtLaunch", "launchBundle", "heldAtLaunch")
_M1_KEYS = ("m1", "1m")
_M5_KEYS = ("m5", "5m")
_HARD_LEVELS = frozenset({"danger", "critical", "error", "fail", "failed", "high"})
_FAIL_STATUSES = frozenset({"fail", "failed", "danger", "rug", "rugged", "high", "critical"})
_STALK_BOOL_FAILS = (
    ("mint_authority_revoked", "mint authority still active"),
    ("freeze_authority_revoked", "freeze authority still active"),
    ("lp_burned", "LP is not burned"),
    ("lp_locked", "LP is not locked"),
)
_STALK_PCTS = (
    ("bundle_held_at_launch_pct", "max_bundle_pct", "bundle held at launch"),
    ("bundleHeldAtLaunchPct", "max_bundle_pct", "bundle held at launch"),
    ("sniper_pct", "max_linked_early_buyer_pct", "snipers"),
    ("sniperPct", "max_linked_early_buyer_pct", "snipers"),
    ("linked_early_buyer_pct", "max_linked_early_buyer_pct", "linked early buyers"),
    ("linkedEarlyBuyersPct", "max_linked_early_buyer_pct", "linked early buyers"),
    ("coordinated_buy_pct", "max_linked_early_buyer_pct", "coordinated buys"),
    ("coordinatedBuyPct", "max_linked_early_buyer_pct", "coordinated buys"),
)

GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
SOCIAL_TYPES = frozenset({"twitter", "telegram", "discord", "website"})
SKIP_GITHUB_OWNERS = frozenset({"orgs", "topics", "features", "settings", "marketplace", "sponsors"})


@dataclass(frozen=True)
class Check:
    """One gate.

    `passed` is True for PASS and NOT_APPLIED. Only FAIL blocks the entry
    verdict. NOT_APPLIED means the field was absent; it is not a demand reading.
    """

    name: str
    passed: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = "FAIL"
    source: str = "onchain"


@dataclass(frozen=True)
class ChecklistResult:
    mint: str
    symbol: str | None
    name: str | None
    passed: bool
    checks: tuple[Check, ...]
    price_usd: float | None
    liquidity_usd: float | None
    pair_address: str | None
    market_cap_usd: float | None = None
    mcap_bucket: str = "unknown"

    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == "FAIL"]

    @property
    def verdict(self) -> str:
        """Single entry go/no-go. NOT_APPLIED gates do not vote."""
        return "GO" if self.passed else "NO-GO"


@dataclass
class GithubRepoInput:
    """One repo's GitHub API payloads. `error` set means the fetch failed."""

    owner: str
    repo: str
    repo_json: dict[str, Any] | None = None
    commits_json: list[dict[str, Any]] | dict[str, Any] | None = None
    error: str | None = None


@dataclass
class RawInputs:
    """Mockable API payloads for one mint. None means that source was not fetched."""

    mint: str
    dex_pairs: list[dict[str, Any]] | dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    rug_report: dict[str, Any] | None = None
    rpc_mint: dict[str, Any] | None = None
    rpc_lp_mint: dict[str, Any] | None = None
    rpc_lp_largest: list[dict[str, Any]] | None = None
    rpc_lp_accounts: list[dict[str, Any] | None] | None = None
    rpc_locker_accounts: dict[str, dict[str, Any] | None] | None = None
    github: list[GithubRepoInput] | None = None
    # Optional intel. None means that source was not fetched. The scanner does
    # not fill these in, and a missing payload is NOT_APPLIED rather than a guess.
    stalkchain: dict[str, Any] | None = None
    fomo: dict[str, Any] | None = None
    x_intel: dict[str, Any] | None = None
    padre: dict[str, Any] | None = None


def evaluate(raw: RawInputs, rules: Rules = RULES, now: datetime | None = None) -> ChecklistResult:
    now = now or datetime.now(timezone.utc)
    market = _parse_market(raw.mint, raw.dex_pairs, raw.profile, now)
    rug = _parse_rug(raw.rug_report)
    rpc = _parse_rpc_mint(raw.rpc_mint)
    checks = (
        _check_rugcheck_onchain(rug),
        _check_mint_authority(rug, rpc),
        _check_freeze_authority(rug, rpc),
        _check_extensions(rug, rpc),
        _check_lp(rug, raw, rules, now),
        _check_top10(rug, rules),
        _check_dev_and_bundle(rug, rules),
        _check_bundle_at_launch(rug, rules),
        _check_linked_early_buyers(rug, rules),
        _check_coordinated_buys(rug, rules),
        _check_liquidity(market, rules),
        _check_volume(market, rules),
        _check_sell_exit_impact(market, rug, rules),
        _check_market_cap(market, rules),
        _check_age(market, rules, now),
        _check_graduation(market, rug, rules, now),
        _check_late_chase(market, rules),
        _check_socials(market),
        _check_github(market, raw.github, rules, now),
        _check_stalkchain(raw.stalkchain, rules),
        _check_fomo(raw.fomo),
        _check_x_intel(raw.x_intel),
        _check_padre(raw.padre, rules),
    )
    passed = not any(c.status == "FAIL" for c in checks)
    return ChecklistResult(
        mint=raw.mint,
        symbol=market.get("symbol"),
        name=market.get("name"),
        passed=passed,
        checks=checks,
        price_usd=market.get("price"),
        liquidity_usd=market.get("liquidity"),
        pair_address=market.get("pair_address"),
        market_cap_usd=market.get("market_cap"),
        mcap_bucket=mcap_bucket(market.get("market_cap")),
    )


def mark_from_pairs(
    mint: str,
    payload: list[dict[str, Any]] | dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Price and liquidity for an existing paper position. None if the mark is missing."""
    market = _parse_market(mint, payload, None, now or datetime.now(timezone.utc))
    price = market.get("price")
    if price is None or price <= 0:
        return None
    return {
        "price": price,
        "liquidity": market.get("liquidity"),
        "pair_address": market.get("pair_address"),
        "symbol": market.get("symbol"),
    }


def github_targets(raw: RawInputs) -> list[tuple[str, str]]:
    """Repos that must be fetched before `evaluate`, if the token presents as a product."""
    market = _parse_market(raw.mint, raw.dex_pairs, raw.profile, datetime.now(timezone.utc))
    if not market.get("product_claim"):
        return []
    return list(market.get("github_repos") or [])


def render_entry_scorecard(result: ChecklistResult) -> str:
    """One mint, one verdict, and a PASS / FAIL / NOT_APPLIED row per gate."""
    lines = [
        f"# Entry scorecard — {result.symbol or result.mint}",
        "",
        f"Verdict: {result.verdict}",
        "",
        "GO means every gate is PASS or NOT_APPLIED. It does not open a trade. "
        "NOT_APPLIED means that field was missing; no demand, flow, or concentration number was filled in.",
        "",
        "| Gate | Source | Status |",
        "| --- | --- | --- |",
    ]
    for check in result.checks:
        lines.append(f"| `{check.name}` | {check.source} | {check.status} |")
    lines.append("")
    fails = result.failures()
    if fails:
        lines.append("## Failures")
        lines.append("")
        for check in fails:
            lines.append(f"- `{check.name}` ({check.source}): {check.reason}")
        lines.append("")
    return "\n".join(lines)


def _gate(name: str, status: str, reason: str, source: str = "onchain", **evidence: Any) -> Check:
    if status not in {"PASS", "FAIL", "NOT_APPLIED"}:
        raise ValueError(f"unknown gate status {status}")
    body = dict(evidence)
    body["status"] = status
    body["source"] = source
    return Check(
        name=name,
        passed=status != "FAIL",
        reason=reason,
        evidence=body,
        status=status,
        source=source,
    )


def _fail(name: str, reason: str, source: str = "onchain", **evidence: Any) -> Check:
    return _gate(name, "FAIL", reason, source, **evidence)


def _ok(name: str, reason: str, source: str = "onchain", **evidence: Any) -> Check:
    return _gate(name, "PASS", reason, source, **evidence)


def _na(name: str, reason: str, source: str = "onchain", **evidence: Any) -> Check:
    return _gate(name, "NOT_APPLIED", reason, source, **evidence)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _authority_active(value: Any) -> bool | None:
    """True if an authority is set, False if revoked, None if the field is absent entirely.

    Callers that require a field treat None as unverified. A JSON null is revoked.
    """
    if value is None:
        return False
    if not isinstance(value, str):
        return True
    if value.strip().lower() in REVOKED_AUTHORITY_SENTINELS:
        return False
    return True


def _parse_time(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_time(int(text))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _pair_list(payload: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        pairs = payload.get("pairs")
        if isinstance(pairs, list):
            return [p for p in pairs if isinstance(p, dict)]
        if "pairAddress" in payload:
            return [payload]
    return []


def _window_for_age(age_hours: float | None) -> str:
    if age_hours is None:
        return "h1"
    if age_hours < 6:
        return "h1"
    if age_hours < 24:
        return "h6"
    return "h24"


def _collect_links(profile: dict[str, Any] | None, pair: dict[str, Any] | None) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if profile:
        for link in profile.get("links") or []:
            if isinstance(link, dict) and link.get("url"):
                found.append(
                    {
                        "type": str(link.get("type") or link.get("label") or "").lower(),
                        "url": str(link["url"]),
                        "label": str(link.get("label") or ""),
                    }
                )
        if profile.get("description"):
            found.append({"type": "description", "url": "", "label": str(profile.get("description"))})
    info = (pair or {}).get("info") or {}
    for social in info.get("socials") or []:
        if isinstance(social, dict) and social.get("url"):
            found.append(
                {
                    "type": str(social.get("type") or "").lower(),
                    "url": str(social["url"]),
                    "label": str(social.get("type") or ""),
                }
            )
    for site in info.get("websites") or []:
        if isinstance(site, dict) and site.get("url"):
            found.append({"type": "website", "url": str(site["url"]), "label": str(site.get("label") or "website")})
        elif isinstance(site, str):
            found.append({"type": "website", "url": site, "label": "website"})
    return found


def _github_repos_from_links(links: list[dict[str, str]]) -> list[tuple[str, str]]:
    repos: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        match = GITHUB_REPO_RE.search(link.get("url") or "")
        if not match:
            match = GITHUB_REPO_RE.search(link.get("label") or "")
        if not match:
            continue
        owner, repo = match.group(1), match.group(2)
        if owner.lower() in SKIP_GITHUB_OWNERS:
            continue
        repo = re.sub(r"\.git$", "", repo, flags=re.IGNORECASE)
        key = (owner, repo)
        if key not in seen:
            seen.add(key)
            repos.append(key)
    return repos


def _is_social(link: dict[str, str]) -> bool:
    kind = (link.get("type") or "").lower()
    url = (link.get("url") or "").lower()
    if not url.startswith("http"):
        return False
    if "dexscreener.com" in url:
        return False
    if kind in SOCIAL_TYPES or kind in {"twitter", "x", "telegram", "discord", "website"}:
        return True
    if "twitter.com" in url or "x.com" in url or "t.me" in url or "discord" in url:
        return True
    if kind in {"website", "docs", "link"} or url.startswith("http"):
        # A real external site counts as a social presence. GitHub alone does too.
        return True
    return False


def _parse_market(
    mint: str,
    dex_pairs: list[dict[str, Any]] | dict[str, Any] | None,
    profile: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "symbol": None,
        "name": None,
        "price": None,
        "liquidity": None,
        "volume": None,
        "buys": None,
        "sells": None,
        "window": None,
        "pair_created": None,
        "social_links": [],
        "text": "",
        "github_repos": [],
        "product_claim": False,
        "pair_address": None,
        "dex_id": None,
        "dex_ids": [],
        "market_cap": None,
        "graduation_at": None,
        "bonding_curve_only": False,
        "price_change_m1": None,
        "price_change_m5": None,
        "price_change_bad": [],
        "flow_m1": None,
        "flow_m5": None,
        "error": None,
    }
    pairs = _pair_list(dex_pairs)
    if dex_pairs is None:
        view["error"] = "DexScreener pairs were not fetched"
        _apply_profile_text(view, profile, None)
        return view
    if not pairs:
        view["error"] = "DexScreener returned no pairs"
        _apply_profile_text(view, profile, None)
        return view

    created: list[datetime] = []
    eligible: list[tuple[float, dict[str, Any]]] = []
    for pair in pairs:
        base = ((pair.get("baseToken") or {}).get("address"))
        quote = ((pair.get("quoteToken") or {}).get("address"))
        created_at = _parse_time(pair.get("pairCreatedAt"))
        if created_at is not None and (base == mint or quote == mint):
            created.append(created_at)
        if base == mint and quote in MAJOR_QUOTES:
            liq = _num(((pair.get("liquidity") or {}).get("usd")))
            eligible.append((liq or 0.0, pair))
    if created:
        view["pair_created"] = min(created)
    if not eligible:
        view["error"] = "no pair quoted in SOL, USDC, or USDT"
        _apply_profile_text(view, profile, pairs[0])
        return view
    _liq, pair = max(eligible, key=lambda item: item[0])
    base_token = pair.get("baseToken") or {}
    view["symbol"] = base_token.get("symbol") or (profile or {}).get("symbol")
    view["name"] = base_token.get("name") or (profile or {}).get("name")
    view["price"] = _num(pair.get("priceUsd"))
    view["liquidity"] = _num(((pair.get("liquidity") or {}).get("usd")))
    view["pair_address"] = pair.get("pairAddress")
    age_hours = None
    if view["pair_created"] is not None:
        age_hours = (now - view["pair_created"]).total_seconds() / 3600.0
    window = _window_for_age(age_hours)
    volume = (pair.get("volume") or {}).get(window)
    txns = (pair.get("txns") or {}).get(window) or {}
    view["window"] = window
    view["volume"] = _num(volume)
    view["buys"] = txns.get("buys") if isinstance(txns, dict) else None
    view["sells"] = txns.get("sells") if isinstance(txns, dict) else None
    view["dex_id"] = pair.get("dexId")
    view["market_cap"] = _num(pair.get("marketCap"))
    if view["market_cap"] is None:
        view["market_cap"] = _num(pair.get("fdv"))
    dex_ids = {str(item.get("dexId") or "").lower() for _liq, item in eligible}
    view["dex_ids"] = sorted(dex_ids)
    grad_times = [
        _parse_time(item.get("pairCreatedAt"))
        for _liq, item in eligible
        if str(item.get("dexId") or "").lower() in PUMP_AMM_DEX_IDS
    ]
    grad_times = [stamp for stamp in grad_times if stamp is not None]
    view["graduation_at"] = min(grad_times) if grad_times else None
    view["bonding_curve_only"] = bool(dex_ids & PUMP_BONDING_DEX_IDS) and not grad_times
    changes = _short_window_changes(pair)
    view["price_change_m1"] = changes["m1"]
    view["price_change_m5"] = changes["m5"]
    view["price_change_bad"] = changes["bad"]
    view["flow_m1"] = _short_flow(pair, "m1")
    view["flow_m5"] = _short_flow(pair, "m5")
    _apply_profile_text(view, profile, pair)
    return view


def _apply_profile_text(view: dict[str, Any], profile: dict[str, Any] | None, pair: dict[str, Any] | None) -> None:
    links = _collect_links(profile, pair)
    socials = [link["url"] for link in links if _is_social(link)]
    description = ""
    if profile and profile.get("description"):
        description = str(profile.get("description"))
    text_parts = [
        str(view.get("name") or ""),
        str(view.get("symbol") or ""),
        description,
        " ".join(link.get("label") or "" for link in links),
        " ".join(link.get("url") or "" for link in links),
    ]
    text = " ".join(text_parts).lower()
    view["social_links"] = socials
    view["text"] = text
    view["github_repos"] = _github_repos_from_links(links)
    view["product_claim"] = any(phrase in text for phrase in PRODUCT_PHRASES)


def _parse_rug(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("error"):
        return {"ok": False, "error": "RugCheck report missing" if report is None else str(report.get("error") or "RugCheck report missing")}
    if "mint" not in report and "token" not in report and "topHolders" not in report:
        return {"ok": False, "error": "RugCheck report has no token fields"}
    return {"ok": True, "error": None, "report": report}


def _parse_rpc_mint(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"ok": False, "error": "Solana RPC mint account was not fetched"}
    data = value.get("data")
    parsed = data.get("parsed") if isinstance(data, dict) else None
    info = parsed.get("info") if isinstance(parsed, dict) else None
    if not isinstance(info, dict) or parsed.get("type") != "mint":
        return {"ok": False, "error": "RPC account is not a parsed mint"}
    return {
        "ok": True,
        "error": None,
        "owner": value.get("owner"),
        "info": info,
        "mint_authority": info.get("mintAuthority"),
        "freeze_authority": info.get("freezeAuthority"),
        "supply": _num(info.get("supply")),
        "extensions": info.get("extensions") if isinstance(info.get("extensions"), list) else None,
    }


def _rug_token(rug: dict[str, Any]) -> dict[str, Any]:
    if not rug.get("ok"):
        return {}
    return (rug["report"].get("token") or {}) if isinstance(rug["report"].get("token"), dict) else {}


def _check_mint_authority(rug: dict[str, Any], rpc: dict[str, Any]) -> Check:
    name = "mint_authority_revoked"
    if not rpc.get("ok"):
        return _fail(name, f"cannot verify: {rpc.get('error')}")
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    rpc_active = _authority_active(rpc.get("mint_authority"))
    token = _rug_token(rug)
    report = rug["report"]
    # RugCheck uses JSON null when the authority is revoked. A missing key is unverified.
    if "mintAuthority" not in token and "mintAuthority" not in report:
        return _fail(name, "cannot verify: RugCheck report omitted mint authority")
    rug_values = []
    if "mintAuthority" in token:
        rug_values.append(token.get("mintAuthority"))
    if "mintAuthority" in report:
        rug_values.append(report.get("mintAuthority"))
    rug_active = any(_authority_active(v) for v in rug_values)
    if rpc_active or rug_active:
        return _fail(
            name,
            "mint authority is still active",
            rpc=rpc.get("mint_authority"),
            rug=rug_values,
        )
    return _ok(name, "mint authority is revoked on RugCheck and on the parsed mint account")


def _check_freeze_authority(rug: dict[str, Any], rpc: dict[str, Any]) -> Check:
    name = "freeze_authority_revoked"
    if not rpc.get("ok"):
        return _fail(name, f"cannot verify: {rpc.get('error')}")
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    token = _rug_token(rug)
    report = rug["report"]
    if "freezeAuthority" not in token and "freezeAuthority" not in report:
        return _fail(name, "cannot verify: RugCheck report omitted freeze authority")
    rug_values = []
    if "freezeAuthority" in token:
        rug_values.append(token.get("freezeAuthority"))
    if "freezeAuthority" in report:
        rug_values.append(report.get("freezeAuthority"))
    if _authority_active(rpc.get("freeze_authority")) or any(_authority_active(v) for v in rug_values):
        return _fail(name, "freeze authority is still active", rpc=rpc.get("freeze_authority"), rug=rug_values)
    return _ok(name, "freeze authority is revoked on RugCheck and on the parsed mint account")


def _extension_dangers(extensions: Any, token_extensions: Any) -> list[str]:
    dangers: list[str] = []
    if isinstance(token_extensions, dict):
        if token_extensions.get("nonTransferable"):
            dangers.append("non-transferable")
        fee = token_extensions.get("transferFeeConfig")
        if isinstance(fee, dict):
            newer = fee.get("newerTransferFee") or fee
            bps = _num(newer.get("transferFeeBasisPoints") if isinstance(newer, dict) else None)
            if bps and bps > 0:
                dangers.append(f"transfer fee {bps} bps")
        if token_extensions.get("permanentDelegate"):
            dangers.append("permanent delegate")
        pausable = token_extensions.get("pausableConfig")
        if isinstance(pausable, dict) and (pausable.get("authority") or pausable.get("paused")):
            dangers.append("pausable")
        hook = token_extensions.get("transferHook")
        if isinstance(hook, dict) and hook.get("authority") or (isinstance(hook, str) and hook):
            dangers.append("transfer hook")
        if token_extensions.get("mintCloseAuthority"):
            dangers.append("mint close authority")
        state = token_extensions.get("defaultAccountState")
        if state not in (None, "", "initialized", "uninitialized", 0, "0"):
            dangers.append("default account state is not unlocked")
    if isinstance(extensions, list):
        for ext in extensions:
            if not isinstance(ext, dict):
                continue
            kind = str(ext.get("extension") or "")
            state = ext.get("state") if isinstance(ext.get("state"), dict) else {}
            if kind == "permanentDelegate" and state.get("delegate"):
                dangers.append("permanent delegate")
            if kind == "transferFeeConfig":
                newer = state.get("newerTransferFee") or {}
                bps = _num(newer.get("transferFeeBasisPoints"))
                if bps and bps > 0:
                    dangers.append(f"transfer fee {bps} bps")
            if kind == "transferHook" and (state.get("programId") or state.get("authority")):
                dangers.append("transfer hook")
            if kind == "pausableConfig" and (state.get("authority") or state.get("paused")):
                dangers.append("pausable")
            if kind == "mintCloseAuthority" and state.get("closeAuthority"):
                dangers.append("mint close authority")
            if kind == "nonTransferable":
                dangers.append("non-transferable")
            if kind == "defaultAccountState" and state.get("state") not in (None, "initialized", 0, "0"):
                dangers.append("default account state is not unlocked")
    # Dedupe, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for item in dangers:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _check_extensions(rug: dict[str, Any], rpc: dict[str, Any]) -> Check:
    name = "no_freeze_like_extensions"
    if not rpc.get("ok"):
        return _fail(name, f"cannot verify: {rpc.get('error')}")
    program = rpc.get("owner")
    rug_program = (rug["report"].get("tokenProgram") if rug.get("ok") else None)
    if rug_program and program and rug_program != program:
        return _fail(name, "token program on RPC does not match RugCheck", rpc=program, rug=rug_program)
    if program == TOKEN_PROGRAM:
        return _ok(name, "classic SPL token program has no Token-2022 hooks")
    if program != TOKEN_2022_PROGRAM:
        return _fail(name, f"cannot verify: unrecognized token program {program}")
    token_extensions = rug["report"].get("token_extensions") if rug.get("ok") else None
    rpc_ext = rpc.get("extensions")
    if token_extensions is None and rpc_ext is None:
        return _fail(name, "cannot verify: Token-2022 extensions were not returned")
    dangers = _extension_dangers(rpc_ext, token_extensions)
    if dangers:
        return _fail(name, "token has a freeze-like extension: " + ", ".join(dangers))
    return _ok(name, "Token-2022 extensions show no freeze, fee, hook, or pause authority")


def _markets(rug: dict[str, Any]) -> list[dict[str, Any]]:
    if not rug.get("ok"):
        return []
    markets = rug["report"].get("markets")
    if not isinstance(markets, list):
        return []
    return [m for m in markets if isinstance(m, dict)]


def _market_liquidity_usd(market: dict[str, Any]) -> float:
    lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
    quote = _num(lp.get("quoteUSD")) or 0.0
    base = _num(lp.get("baseUSD")) or 0.0
    return quote + base


def _lp_supply(info: dict[str, Any] | None) -> float | None:
    if not isinstance(info, dict):
        return None
    data = info.get("data")
    parsed = data.get("parsed") if isinstance(data, dict) else None
    body = parsed.get("info") if isinstance(parsed, dict) else None
    if not isinstance(body, dict):
        return None
    return _num(body.get("supply"))


def _iter_lockers(report: dict[str, Any]) -> list[dict[str, Any]]:
    raw = report.get("lockers")
    items: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", key)
                items.append(item)
            elif value not in (None, 0, "0"):
                items.append({"id": key, "value": value})
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, dict):
                items.append(value)
    return items


def _locker_unlock(item: dict[str, Any]) -> datetime | None:
    for key in (
        "unlockDate",
        "unlock_date",
        "unlockTime",
        "unlockTimestamp",
        "unlockAt",
        "endDate",
        "end",
        "expiry",
        "unlock_time",
    ):
        if key in item:
            found = _parse_time(item.get(key))
            if found is not None:
                return found
    return None


def _locker_program(item: dict[str, Any]) -> str | None:
    for key in ("programId", "program", "lockerProgram", "ownerProgram"):
        value = item.get(key)
        if isinstance(value, str) and value in KNOWN_LOCKER_PROGRAMS:
            return value
    return None


def _locker_name_ok(item: dict[str, Any]) -> bool:
    blob = " ".join(str(item.get(k) or "") for k in ("name", "type", "provider", "locker", "protocol")).lower()
    return any(name in blob for name in KNOWN_LOCKER_NAMES)


def _check_lp(rug: dict[str, Any], raw: RawInputs, rules: Rules, now: datetime) -> Check:
    name = "lp_burned_or_long_locked"
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    report = rug["report"]
    if report.get("rugged") is True:
        return _fail(name, "RugCheck marks this token as rugged")
    markets = _markets(rug)
    if not markets:
        return _fail(name, "cannot verify: RugCheck report has no markets")
    ranked = sorted(markets, key=_market_liquidity_usd, reverse=True)
    dominant = ranked[0]
    total_usd = sum(_market_liquidity_usd(m) for m in markets)
    locked_usd = 0.0
    for market in markets:
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
        pct = _num(lp.get("lpLockedPct"))
        usd = _market_liquidity_usd(market)
        if pct is None:
            return _fail(name, "cannot verify: a market is missing lpLockedPct")
        locked_usd += usd * (pct / 100.0)
    if total_usd <= 0:
        return _fail(name, "cannot verify: market liquidity is zero")
    locked_pct = locked_usd / total_usd * 100.0
    if locked_pct + 1e-6 < rules.lp_min_locked_pct:
        return _fail(
            name,
            f"only {locked_pct:.1f}% of liquidity is locked or burned (need {rules.lp_min_locked_pct:.0f}%)",
            locked_pct=round(locked_pct, 2),
        )

    lp = dominant.get("lp") if isinstance(dominant.get("lp"), dict) else {}
    lp_mint = dominant.get("mintLP") or lp.get("lpMint")
    rug_supply = _num((dominant.get("mintLPAccount") or {}).get("supply"))
    if rug_supply is None:
        rug_supply = _num(lp.get("lpCurrentSupply"))
    rpc_supply = _lp_supply(raw.rpc_lp_mint)
    if raw.rpc_lp_mint is None or rpc_supply is None:
        return _fail(name, "cannot verify: LP mint was not read from Solana RPC")
    if rug_supply is not None and abs(rug_supply - rpc_supply) > 1:
        return _fail(
            name,
            "LP mint supply on RPC does not match RugCheck",
            rug_supply=rug_supply,
            rpc_supply=rpc_supply,
        )

    quote_usd = _num(lp.get("quoteUSD")) or 0.0
    if rpc_supply == 0 and quote_usd > 0:
        return _ok(
            name,
            "LP mint supply is 0 on-chain while the pool still holds liquidity, so the LP tokens are burned",
            lp_mint=lp_mint,
            quote_usd=quote_usd,
        )

    burned = _burned_lp_share(raw, rpc_supply)
    if burned is None:
        # Fall through to locker proof. A missing largest-accounts read is fine
        # only when a locker independently proves the lock.
        burned_error = "LP burn share was not read from RPC"
    elif burned + 1e-9 >= rules.lp_min_locked_pct / 100.0:
        return _ok(
            name,
            f"{burned * 100:.1f}% of LP tokens are held by the incinerator",
            lp_mint=lp_mint,
        )
    else:
        burned_error = f"only {burned * 100:.1f}% of LP supply is burned"

    locker = _long_lock_proof(report, raw.rpc_locker_accounts, rules, now)
    if locker.passed:
        return locker
    return _fail(
        name,
        f"{burned_error}; {locker.reason}",
        lp_mint=lp_mint,
        rpc_supply=rpc_supply,
    )


def _burned_lp_share(raw: RawInputs, supply: float) -> float | None:
    if supply <= 0:
        return None
    if raw.rpc_lp_largest is None or raw.rpc_lp_accounts is None:
        return None
    if len(raw.rpc_lp_accounts) != len(raw.rpc_lp_largest):
        return None
    burned = 0.0
    for entry, account in zip(raw.rpc_lp_largest, raw.rpc_lp_accounts):
        amount = _num((entry or {}).get("amount"))
        if amount is None or not isinstance(account, dict):
            return None
        data = account.get("data")
        parsed = data.get("parsed") if isinstance(data, dict) else None
        info = parsed.get("info") if isinstance(parsed, dict) else None
        owner = info.get("owner") if isinstance(info, dict) else None
        if owner in BURN_OWNERS:
            burned += amount
    return burned / supply


def _long_lock_proof(
    report: dict[str, Any],
    rpc_accounts: dict[str, dict[str, Any] | None] | None,
    rules: Rules,
    now: datetime,
) -> Check:
    name = "lp_burned_or_long_locked"
    lockers = _iter_lockers(report)
    if not lockers:
        return _fail(name, "no burned LP and no locker record to verify")
    deadline = now + timedelta(days=rules.min_lock_days)
    reasons: list[str] = []
    for item in lockers:
        unlock = _locker_unlock(item)
        program = _locker_program(item)
        named = _locker_name_ok(item)
        if unlock is None:
            reasons.append("locker record has no unlock time")
            continue
        if unlock < deadline:
            reasons.append(
                f"lock unlocks {unlock.date().isoformat()}, shorter than {rules.min_lock_days} days"
            )
            continue
        if program is None:
            reasons.append("locker is not a known program (Streamflow, UNCX, or Jupiter Lock)")
            continue
        if not named and program not in KNOWN_LOCKER_PROGRAMS:
            reasons.append("locker name is not on the known-locker list")
            continue
        account_id = str(item.get("id") or item.get("address") or item.get("pubkey") or "")
        if rpc_accounts is None or account_id not in rpc_accounts or not isinstance(rpc_accounts.get(account_id), dict):
            reasons.append("locker account was not found on Solana RPC")
            continue
        owner = rpc_accounts[account_id].get("owner")
        if owner != program:
            reasons.append("locker account owner does not match the claimed program")
            continue
        return _ok(
            name,
            f"LP locked until {unlock.date().isoformat()} by {program}",
            unlock=unlock.isoformat(),
            program=program,
        )
    return _fail(name, "; ".join(reasons) if reasons else "no verifiable long-term lock")


def _excluded_holder_ids(report: dict[str, Any]) -> set[str]:
    excluded: set[str] = set()
    known = report.get("knownAccounts") or {}
    if isinstance(known, dict):
        for address, meta in known.items():
            if isinstance(meta, dict) and str(meta.get("type") or "").upper() in {"AMM", "LOCKER", "BURN"}:
                excluded.add(address)
    for market in report.get("markets") or []:
        if not isinstance(market, dict):
            continue
        for key in ("pubkey", "liquidityA", "liquidityB", "mintLP"):
            if market.get(key):
                excluded.add(str(market[key]))
        for side in ("liquidityAAccount", "liquidityBAccount"):
            acct = market.get(side)
            if isinstance(acct, dict) and acct.get("owner"):
                excluded.add(str(acct["owner"]))
    excluded.update(BURN_OWNERS)
    return excluded


def _holder_rows(report: dict[str, Any]) -> list[dict[str, Any]] | None:
    rows = report.get("topHolders")
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def _check_top10(rug: dict[str, Any], rules: Rules) -> Check:
    name = "top10_concentration"
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    report = rug["report"]
    rows = _holder_rows(report)
    if not rows:
        return _fail(name, "cannot verify: RugCheck did not return top holders")
    excluded = _excluded_holder_ids(report)
    kept = []
    for row in rows:
        address = str(row.get("address") or "")
        owner = str(row.get("owner") or "")
        if address in excluded or owner in excluded:
            continue
        pct = _num(row.get("pct"))
        if pct is None:
            return _fail(name, "cannot verify: a holder row has no percentage")
        kept.append(pct)
    top = kept[:10]
    if len(top) < min(10, len(rows)):
        # All remaining rows were pools. That is fine if we still summed wallets.
        pass
    if not top:
        return _fail(name, "cannot verify: every holder row was a pool or locker")
    total = sum(top)
    if total > rules.max_top10_holder_pct:
        return _fail(
            name,
            f"top wallets hold {total:.1f}% (limit {rules.max_top10_holder_pct:.0f}%, pools excluded)",
            top10_pct=round(total, 2),
        )
    return _ok(
        name,
        f"top wallets hold {total:.1f}% after excluding pools and lockers",
        top10_pct=round(total, 2),
        wallets=len(top),
    )


def _check_dev_and_bundle(rug: dict[str, Any], rules: Rules) -> Check:
    name = "dev_and_bundle_concentration"
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    report = rug["report"]
    creator = report.get("creator")
    if not isinstance(creator, str) or not creator:
        return _fail(name, "cannot verify: creator wallet was not reported")
    rows = _holder_rows(report)
    if rows is None:
        return _fail(name, "cannot verify: holder list missing")
    dev_pct = 0.0
    insider_pct = 0.0
    for row in rows:
        pct = _num(row.get("pct")) or 0.0
        owner = str(row.get("owner") or "")
        address = str(row.get("address") or "")
        if owner == creator or address == creator:
            dev_pct += pct
        if row.get("insider") is True:
            insider_pct += pct
    supply = _num((report.get("token") or {}).get("supply")) if isinstance(report.get("token"), dict) else None
    creator_balance = _num(report.get("creatorBalance"))
    if supply and creator_balance is not None and 0 <= creator_balance <= supply:
        dev_pct = max(dev_pct, creator_balance / supply * 100.0)
    if dev_pct > rules.max_dev_holder_pct:
        return _fail(
            name,
            f"creator holds {dev_pct:.1f}% (limit {rules.max_dev_holder_pct:.0f}%)",
            dev_pct=round(dev_pct, 2),
        )

    networks = report.get("insiderNetworks")
    if networks is None and not any(isinstance(r, dict) and "insider" in r for r in rows):
        return _fail(name, "cannot verify: insider or bundle data was not returned")
    network_pcts: list[float] = []
    if isinstance(networks, list):
        if supply is None or supply <= 0:
            if networks:
                return _fail(name, "cannot verify: supply missing, cannot size insider networks")
        else:
            for net in networks:
                if not isinstance(net, dict):
                    continue
                holding = _num(net.get("currentHolding"))
                if holding is None:
                    return _fail(name, "cannot verify: an insider network has no current holding")
                network_pcts.append(holding / supply * 100.0)
    elif networks is not None:
        return _fail(name, "cannot verify: insider network payload is not a list")
    network_sum = sum(network_pcts)
    if network_sum > 100.0:
        # Overlap between networks would invent a number above 100%. Use the
        # largest single network instead of failing on the double count.
        bundle_pct = max(network_pcts) if network_pcts else insider_pct
        bundle_note = "largest insider network (networks overlap)"
    else:
        bundle_pct = max(network_sum, insider_pct)
        bundle_note = "insider networks plus flagged holders"
    if bundle_pct > rules.max_bundle_pct:
        return _fail(
            name,
            f"bundle concentration is {bundle_pct:.1f}% (limit {rules.max_bundle_pct:.0f}%)",
            bundle_pct=round(bundle_pct, 2),
            dev_pct=round(dev_pct, 2),
        )
    return _ok(
        name,
        f"creator holds {dev_pct:.1f}% and bundle concentration is {bundle_pct:.1f}% ({bundle_note})",
        dev_pct=round(dev_pct, 2),
        bundle_pct=round(bundle_pct, 2),
    )


def _explicit_early_buyer_pct(report: dict[str, Any]) -> float | None | str:
    """A percentage, None when the payload has no such field, or 'bad' when the field is unreadable."""
    for key in _EARLY_PCT_KEYS:
        if key in report:
            value = _num(report.get(key))
            return value if value is not None else "bad"
    for key in _EARLY_BLOCK_KEYS:
        if key not in report:
            continue
        block = report.get(key)
        if isinstance(block, dict):
            value = _num(block.get("pct"))
            return value if value is not None else "bad"
        if isinstance(block, list):
            total = 0.0
            for row in block:
                if not isinstance(row, dict) or "pct" not in row:
                    return "bad"
                value = _num(row.get("pct"))
                if value is None:
                    return "bad"
                total += value
            return total
        return "bad"
    rows = report.get("topHolders")
    if isinstance(rows, list):
        flagged = False
        total = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if any(key in row for key in ("earlyBuyer", "sniper", "linkedEarly")):
                flagged = True
                if row.get("earlyBuyer") or row.get("sniper") or row.get("linkedEarly"):
                    value = _num(row.get("pct"))
                    if value is None:
                        return "bad"
                    total += value
        if flagged:
            return total
    return None


def _check_linked_early_buyers(rug: dict[str, Any], rules: Rules) -> Check:
    name = "linked_early_buyers"
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    found = _explicit_early_buyer_pct(rug["report"])
    if found is None:
        return _na(
            name,
            "RugCheck did not report linked early-buyer concentration; gate not applied",
            applied=False,
        )
    if found == "bad":
        return _fail(name, "cannot verify: linked early-buyer field was present but not a percentage")
    pct = float(found)
    if pct > rules.max_linked_early_buyer_pct:
        return _fail(
            name,
            f"linked early buyers hold {pct:.1f}% (limit {rules.max_linked_early_buyer_pct:.0f}%)",
            early_pct=round(pct, 2),
        )
    return _ok(
        name,
        f"linked early buyers hold {pct:.1f}%",
        early_pct=round(pct, 2),
        applied=True,
    )


def _block_pct(report: dict[str, Any], pct_keys: tuple[str, ...], block_keys: tuple[str, ...]) -> float | None | str:
    """A percentage, None when no such field exists, or 'bad' when the field is unreadable."""
    for key in pct_keys:
        if key in report:
            value = _num(report.get(key))
            return value if value is not None else "bad"
    for key in block_keys:
        if key not in report:
            continue
        block = report.get(key)
        if isinstance(block, dict):
            if "pct" not in block:
                return "bad"
            value = _num(block.get("pct"))
            return value if value is not None else "bad"
        if isinstance(block, list):
            total = 0.0
            saw = False
            for row in block:
                if not isinstance(row, dict) or "pct" not in row:
                    return "bad"
                value = _num(row.get("pct"))
                if value is None:
                    return "bad"
                total += value
                saw = True
            return total if saw else 0.0
        return "bad"
    return None


def _check_coordinated_buys(rug: dict[str, Any], rules: Rules) -> Check:
    name = "coordinated_buys"
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    found = _block_pct(rug["report"], _COORD_PCT_KEYS, _COORD_BLOCK_KEYS)
    if found is None:
        return _na(
            name,
            "no coordinated-buy field was reported; gate not applied",
            applied=False,
        )
    if found == "bad":
        return _fail(name, "cannot verify: coordinated-buy field was present but not a percentage")
    pct = float(found)
    limit = rules.max_linked_early_buyer_pct
    if pct > limit:
        return _fail(
            name,
            f"coordinated buys hold {pct:.1f}% (limit {limit:.0f}%)",
            coordinated_pct=round(pct, 2),
            applied=True,
        )
    return _ok(
        name,
        f"coordinated buys hold {pct:.1f}%",
        coordinated_pct=round(pct, 2),
        applied=True,
    )


def _launch_holding_readings(report: dict[str, Any]) -> float | None | str:
    """Max launch-bundle percent from fields that actually exist. None if none do."""
    readings: list[float] = []
    found = False
    supply = None
    token = report.get("token")
    if isinstance(token, dict):
        supply = _num(token.get("supply"))
    networks = report.get("insiderNetworks")
    if isinstance(networks, list):
        holdings: list[float] = []
        for net in networks:
            if not isinstance(net, dict):
                continue
            key = next((item for item in _LAUNCH_HOLDING_KEYS if item in net), None)
            if key is None:
                continue
            found = True
            holding = _num(net.get(key))
            if holding is None or supply is None or supply <= 0:
                return "bad"
            holdings.append(holding / supply * 100.0)
        if holdings:
            total = sum(holdings)
            readings.append(max(holdings) if total > 100.0 else total)
    rows = report.get("topHolders")
    if isinstance(rows, list):
        flagged = False
        total = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not any(key in row for key in _LAUNCH_ROW_FLAGS):
                continue
            flagged = True
            if any(row.get(key) for key in _LAUNCH_ROW_FLAGS):
                value = _num(row.get("pct"))
                if value is None:
                    return "bad"
                total += value
        if flagged:
            found = True
            readings.append(total)
    if not found:
        return None
    if not readings:
        return None
    return max(readings)


def _explicit_launch_bundle_pct(report: dict[str, Any]) -> float | None | str:
    explicit: float | None = None
    for key in _LAUNCH_PCT_KEYS:
        if key not in report:
            continue
        value = _num(report.get(key))
        if value is None:
            return "bad"
        explicit = value
        break
    derived = _launch_holding_readings(report)
    if derived == "bad":
        return "bad"
    if explicit is None:
        return derived
    if derived is None:
        return explicit
    return max(explicit, float(derived))


def _check_bundle_at_launch(rug: dict[str, Any], rules: Rules) -> Check:
    name = "bundle_held_at_launch"
    if not rug.get("ok"):
        return _fail(name, f"cannot verify: {rug.get('error')}")
    found = _explicit_launch_bundle_pct(rug["report"])
    if found is None:
        return _na(
            name,
            "no held-at-launch bundle field; current holdings were not used as a substitute",
            applied=False,
        )
    if found == "bad":
        return _fail(name, "cannot verify: held-at-launch bundle field was present but not a percentage")
    pct = float(found)
    if pct > rules.max_bundle_pct:
        return _fail(
            name,
            f"bundle held at launch is {pct:.1f}% (limit {rules.max_bundle_pct:.0f}%)",
            launch_bundle_pct=round(pct, 2),
            applied=True,
        )
    return _ok(
        name,
        f"bundle held at launch is {pct:.1f}%",
        launch_bundle_pct=round(pct, 2),
        applied=True,
    )


def _check_rugcheck_onchain(rug: dict[str, Any]) -> Check:
    name = "rugcheck_onchain"
    if not rug.get("ok"):
        return _fail(name, f"RugCheck/onchain fail: {rug.get('error')}")
    report = rug["report"]
    if report.get("rugged") is True:
        return _fail(name, "RugCheck marks this token as rugged")
    for key in ("status", "result"):
        if key not in report:
            continue
        status = str(report.get(key) or "").strip().lower()
        if status in _FAIL_STATUSES:
            return _fail(name, f"RugCheck status is {status}")
    if "risks" not in report:
        return _na(
            name,
            "RugCheck report had no risk list; no extra risk was invented",
            rugged=False,
        )
    risks = report.get("risks")
    if not isinstance(risks, list):
        return _fail(name, "cannot verify: RugCheck risks payload was not a list")
    hard: list[str] = []
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        level = str(risk.get("level") or risk.get("severity") or "").strip().lower()
        if level in _HARD_LEVELS:
            hard.append(str(risk.get("name") or risk.get("description") or level))
    if hard:
        return _fail(name, "RugCheck danger: " + ", ".join(hard))
    return _ok(name, "RugCheck risk list has no danger-level item", risks=len(risks))


def _best_quote_usd(rug: dict[str, Any]) -> float | None:
    """Largest single-pool quote side. None when no market reports quoteUSD."""
    if not rug.get("ok"):
        return None
    found = False
    best = 0.0
    for market in _markets(rug):
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
        if "quoteUSD" not in lp:
            continue
        found = True
        value = _num(lp.get("quoteUSD"))
        if value is not None:
            best = max(best, value)
    if not found:
        return None
    return best


def _check_sell_exit_impact(market: dict[str, Any], rug: dict[str, Any], rules: Rules) -> Check:
    name = "sell_exit_impact"
    liquidity = market.get("liquidity")
    if liquidity is None:
        detail = market.get("error") or "pool liquidity in USD was missing"
        return _fail(name, f"cannot verify: {detail}")
    ticket = rules.position_size_usd
    bps = slippage_bps(ticket, float(liquidity), rules)
    reasons: list[str] = []
    if bps > rules.max_reasonable_exit_bps:
        reasons.append(
            f"modeled sell slippage is {bps} bps, above {rules.max_reasonable_exit_bps} "
            f"for a ${ticket:.0f} paper ticket"
        )
    quote = _best_quote_usd(rug)
    quote_status = "NOT_APPLIED"
    impact = None
    if quote is None:
        quote_status = "NOT_APPLIED"
    elif quote <= 0:
        reasons.append("quote-side liquidity is zero, so the paper ticket cannot be sold")
        quote_status = "FAIL"
    else:
        impact = ticket / quote
        quote_status = "PASS"
        if impact > rules.max_sell_quote_impact:
            reasons.append(
                f"selling the ${ticket:.0f} paper ticket moves {impact:.1%} of quote-side liquidity "
                f"(limit {rules.max_sell_quote_impact:.1%})"
            )
            quote_status = "FAIL"
    if reasons:
        return _fail(
            name,
            "; ".join(reasons),
            slippage_bps=bps,
            quote_impact_status=quote_status,
            quote_impact=None if impact is None else round(impact, 4),
        )
    quote_note = (
        "quote-side USD was not in the report"
        if quote is None
        else f"quote impact {impact:.1%} of ${quote:,.0f}"
    )
    return _ok(
        name,
        f"paper-ticket sell slippage is {bps} bps; {quote_note}. "
        "The $50–$100 band is paper scoring only.",
        slippage_bps=bps,
        quote_impact_status=quote_status,
        quote_impact=None if impact is None else round(impact, 4),
    )


def _check_market_cap(market: dict[str, Any], rules: Rules) -> Check:
    name = "market_cap_band"
    cap = market.get("market_cap")
    if cap is None or cap <= 0:
        detail = market.get("error") or "market cap was missing"
        return _fail(name, f"cannot verify: {detail}")
    low = rules.mcap_pref_min_usd
    high = rules.mcap_pref_max_usd
    if cap < low or cap > high:
        return _fail(
            name,
            f"market cap ${cap:,.0f} is outside the ${low:,.0f}–${high:,.0f} graduated band",
            market_cap=cap,
        )
    return _ok(
        name,
        f"market cap ${cap:,.0f} is inside the ${low:,.0f}–${high:,.0f} graduated band",
        market_cap=cap,
    )


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> tuple[bool, Any]:
    for key in keys:
        if key in mapping:
            return True, mapping.get(key)
    return False, None


def _short_window_changes(pair: dict[str, Any]) -> dict[str, Any]:
    raw = pair.get("priceChange") if isinstance(pair.get("priceChange"), dict) else {}
    out: dict[str, Any] = {"m1": None, "m5": None, "bad": []}
    for label, keys in (("m1", _M1_KEYS), ("m5", _M5_KEYS)):
        present, value = _first_present(raw, keys)
        if not present:
            continue
        number = _num(value)
        if number is None:
            out["bad"].append(label)
        else:
            out[label] = number
    return out


def _short_flow(pair: dict[str, Any], label: str) -> dict[str, Any] | None:
    keys = _M1_KEYS if label == "m1" else _M5_KEYS
    volume = pair.get("volume") if isinstance(pair.get("volume"), dict) else {}
    txns = pair.get("txns") if isinstance(pair.get("txns"), dict) else {}
    vol_present, vol_value = _first_present(volume, keys)
    txn_present, txn_value = _first_present(txns, keys)
    if not vol_present and not txn_present:
        return None
    buys = sells = None
    if isinstance(txn_value, dict):
        buys = txn_value.get("buys")
        sells = txn_value.get("sells")
    return {
        "volume": _num(vol_value) if vol_present else None,
        "buys": buys,
        "sells": sells,
        "volume_present": vol_present,
        "txns_present": txn_present and isinstance(txn_value, dict),
    }


def _organic_flow(flow: dict[str, Any] | None, liquidity: float | None, rules: Rules) -> str:
    """Return weak, organic, or unknown. Unknown is not a demand reading."""
    if not flow or not flow.get("txns_present") or not flow.get("volume_present"):
        return "unknown"
    volume = flow.get("volume")
    try:
        buys = int(flow.get("buys"))
        sells = int(flow.get("sells"))
    except (TypeError, ValueError):
        return "unknown"
    if volume is None or buys < 0 or sells < 0:
        return "unknown"
    txns = buys + sells
    if txns < rules.late_chase_min_prints:
        return "weak"
    if sells <= 0 or (buys / sells) >= rules.late_chase_max_buy_sell:
        return "weak"
    average = volume / txns if txns else 0.0
    if average < rules.min_avg_trade_usd:
        return "weak"
    pool = liquidity or 0.0
    turnover = (volume / pool) if pool else 99.0
    if average < rules.wash_avg_trade_usd and turnover >= rules.wash_volume_to_liquidity:
        return "weak"
    return "organic"


def _check_late_chase(market: dict[str, Any], rules: Rules) -> Check:
    name = "late_chase"
    bad = market.get("price_change_bad") or []
    if bad:
        return _fail(name, "cannot verify: " + " and ".join(bad) + " price change was present but not a number")
    m1 = market.get("price_change_m1")
    m5 = market.get("price_change_m5")
    if m1 is None and m5 is None:
        return _na(
            name,
            "no 1m or 5m price change was on the pair; chase and organic flow were not invented",
            applied=False,
        )
    failures: list[str] = []
    unknown = False
    notes: list[str] = []
    liquidity = market.get("liquidity")
    windows = (
        ("1m", m1, market.get("flow_m1"), rules.late_chase_m1_pct, rules.late_chase_extreme_m1_pct),
        ("5m", m5, market.get("flow_m5"), rules.late_chase_m5_pct, rules.late_chase_extreme_m5_pct),
    )
    for label, change, flow, large, extreme in windows:
        if change is None:
            continue
        if change >= extreme:
            failures.append(f"{label} move is {change:.0f}%, already vertical")
            continue
        if change >= large:
            quality = _organic_flow(flow if isinstance(flow, dict) else None, liquidity, rules)
            if quality == "unknown":
                unknown = True
                continue
            if quality == "weak":
                failures.append(f"{label} move is {change:.0f}% with weak organic flow")
            else:
                notes.append(f"{label} {change:.0f}% still has two-sided flow")
        else:
            notes.append(f"{label} {change:.0f}% is not a chase")
    evidence = {"m1": m1, "m5": m5, "applied": True}
    if failures:
        return _fail(name, "; ".join(failures), **evidence)
    if unknown:
        return _na(
            name,
            "short-window pump is large but organic-flow counts were missing, so no demand was assumed",
            applied=False,
            m1=m1,
            m5=m5,
        )
    return _ok(name, "; ".join(notes) or "short-window move is not a late chase", **evidence)


def _payload_status_fail(payload: dict[str, Any]) -> str | None:
    if payload.get("error"):
        return str(payload.get("error"))
    if payload.get("ok") is False:
        return "source reported ok=false"
    for key in ("status", "result"):
        if key not in payload:
            continue
        status = str(payload.get(key) or "").strip().lower()
        if status in _FAIL_STATUSES:
            return status
    return None


def _hard_warnings(warnings: Any) -> list[str] | None:
    """Danger texts, or None when the warning list itself is unreadable."""
    if not isinstance(warnings, list):
        return None
    hard: list[str] = []
    for item in warnings:
        if isinstance(item, dict):
            level = str(item.get("level") or item.get("severity") or "").strip().lower()
            if level in _HARD_LEVELS:
                hard.append(str(item.get("message") or item.get("code") or item.get("name") or level))
    return hard


def _check_stalkchain(payload: dict[str, Any] | None, rules: Rules) -> Check:
    name = "stalkchain_onchain"
    source = "stalkchain"
    if payload is None:
        return _na(
            name,
            "StalkChain payload was not supplied; no holder, bundle, or demand figure was invented",
            source=source,
        )
    if not isinstance(payload, dict):
        return _fail(name, "cannot verify: StalkChain payload was not an object", source=source)
    failed = _payload_status_fail(payload)
    if failed:
        return _fail(name, f"StalkChain/onchain fail: {failed}", source=source)
    problems: list[str] = []
    applied = False
    for key, message in _STALK_BOOL_FAILS:
        if key not in payload:
            continue
        applied = True
        value = payload.get(key)
        if value is False:
            problems.append(message)
        elif value is not True:
            return _fail(name, f"cannot verify: StalkChain {key} was present but not a boolean", source=source)
    for key, limit_name, label in _STALK_PCTS:
        if key not in payload:
            continue
        applied = True
        value = _num(payload.get(key))
        if value is None:
            return _fail(name, f"cannot verify: StalkChain {key} was present but not a percentage", source=source)
        limit = getattr(rules, limit_name)
        if value > limit:
            problems.append(f"{label} {value:.1f}% (limit {limit:.0f}%)")
    if problems:
        return _fail(name, "StalkChain/onchain fail: " + "; ".join(problems), source=source, applied=True)
    if not applied:
        return _na(
            name,
            "StalkChain payload had no safety fields to apply; nothing was filled in",
            source=source,
        )
    return _ok(name, "StalkChain safety fields that were present are inside the caps", source=source, applied=True)


def _check_fomo(payload: dict[str, Any] | None) -> Check:
    name = "fomo_warnings"
    source = "fomo"
    note = "copy and leaderboard signals do not open a buy"
    if payload is None:
        return _na(
            name,
            f"FOMO warnings were not supplied; {note}, and no demand was invented",
            source=source,
            auto_open=False,
        )
    if not isinstance(payload, dict):
        return _fail(name, "cannot verify: FOMO payload was not an object", source=source, auto_open=False)
    failed = _payload_status_fail(payload)
    if failed:
        return _fail(name, f"FOMO warning fail: {failed}", source=source, auto_open=False)
    if "warnings" not in payload and "warning" not in payload:
        return _na(
            name,
            f"FOMO payload had no warning list; {note}, and no leaderboard buy was invented",
            source=source,
            auto_open=False,
            copy_signal=bool(payload.get("copy_signal")),
        )
    warnings = payload.get("warnings") if "warnings" in payload else payload.get("warning")
    hard = _hard_warnings(warnings)
    if hard is None:
        return _fail(name, "cannot verify: FOMO warnings were present but not a list", source=source, auto_open=False)
    if hard:
        return _fail(
            name,
            "FOMO warning: " + "; ".join(hard) + f". {note}.",
            source=source,
            auto_open=False,
        )
    return _ok(
        name,
        f"{len(warnings)} FOMO warning(s), none marked danger. {note}.",
        source=source,
        auto_open=False,
        warnings=len(warnings),
    )


def _check_x_intel(payload: dict[str, Any] | None) -> Check:
    name = "x_intel"
    source = "x"
    if payload is None:
        return _na(name, "X intel was not supplied; mention counts and demand were not invented", source=source)
    if not isinstance(payload, dict):
        return _fail(name, "cannot verify: X intel was not an object", source=source)
    if payload.get("bot_spam") is True or payload.get("inorganic") is True:
        return _fail(name, "X intel marked the flow as inorganic", source=source)
    if "organic" not in payload and payload.get("bot_spam") is not False and payload.get("inorganic") is not False:
        return _na(name, "X intel had no organic-flow flag; demand was not invented", source=source)
    if "organic" in payload:
        organic = payload.get("organic")
        if organic is False:
            return _fail(name, "X intel marked the flow as inorganic", source=source)
        if organic is not True:
            return _fail(name, "cannot verify: X organic flag was present but not a boolean", source=source)
    evidence: dict[str, Any] = {}
    if "mentions" in payload:
        evidence["mentions"] = payload.get("mentions")
    return _ok(name, "X intel did not flag inorganic flow", source=source, **evidence)


def _check_padre(payload: dict[str, Any] | None, rules: Rules) -> Check:
    name = "padre_intel"
    source = "padre"
    if payload is None:
        return _na(name, "Padre intel was not supplied; no bundle or demand figure was invented", source=source)
    if not isinstance(payload, dict):
        return _fail(name, "cannot verify: Padre intel was not an object", source=source)
    failed = _payload_status_fail(payload)
    if failed:
        return _fail(name, f"Padre intel fail: {failed}", source=source)
    problems: list[str] = []
    applied = False
    for key, limit_name, label in _STALK_PCTS:
        if key not in payload:
            continue
        applied = True
        value = _num(payload.get(key))
        if value is None:
            return _fail(name, f"cannot verify: Padre {key} was present but not a percentage", source=source)
        limit = getattr(rules, limit_name)
        if value > limit:
            problems.append(f"{label} {value:.1f}% (limit {limit:.0f}%)")
    if "warnings" in payload:
        applied = True
        hard = _hard_warnings(payload.get("warnings"))
        if hard is None:
            return _fail(name, "cannot verify: Padre warnings were present but not a list", source=source)
        problems.extend(hard)
    if problems:
        return _fail(name, "Padre intel fail: " + "; ".join(problems), source=source, applied=True)
    if not applied:
        return _na(name, "Padre intel had no safety fields to apply; nothing was filled in", source=source)
    return _ok(name, "Padre fields that were present are inside the caps", source=source, applied=True)


def _is_pump_launch(market: dict[str, Any], rug: dict[str, Any]) -> bool:
    dex_ids = {str(item).lower() for item in market.get("dex_ids") or []}
    if market.get("dex_id"):
        dex_ids.add(str(market["dex_id"]).lower())
    if dex_ids & (PUMP_AMM_DEX_IDS | PUMP_BONDING_DEX_IDS):
        return True
    report = rug["report"] if rug.get("ok") else {}
    launch = report.get("launchpad") if isinstance(report, dict) else None
    if isinstance(launch, dict):
        blob = f"{launch.get('platform') or ''} {launch.get('name') or ''}".lower()
        if "pump" in blob:
            return True
    markets = report.get("markets") if isinstance(report, dict) else None
    if isinstance(markets, list):
        for item in markets:
            if isinstance(item, dict) and "pump" in str(item.get("marketType") or "").lower():
                return True
    return False


def _check_graduation(market: dict[str, Any], rug: dict[str, Any], rules: Rules, now: datetime) -> Check:
    name = "minutes_since_graduation"
    if not _is_pump_launch(market, rug):
        return _na(name, "not a pump.fun launch; graduation wait not applied")
    if market.get("bonding_curve_only"):
        return _fail(name, "still on the pump.fun bonding curve; not graduated")
    graduated = market.get("graduation_at") or market.get("pair_created")
    if graduated is None:
        return _fail(name, "cannot verify: pump.fun graduation time was not on the pair")
    if graduated > now + timedelta(minutes=2):
        return _fail(name, "pump.fun graduation time is in the future")
    age_min = (now - graduated).total_seconds() / 60.0
    if age_min < rules.min_minutes_since_graduation:
        return _fail(
            name,
            f"graduated {age_min:.0f} minutes ago; waiting until {rules.min_minutes_since_graduation} minutes",
            age_minutes=round(age_min, 1),
        )
    return _ok(
        name,
        f"pump.fun graduation was {age_min / 60:.1f} hours ago",
        age_minutes=round(age_min, 1),
    )


def _check_liquidity(market: dict[str, Any], rules: Rules) -> Check:
    name = "min_liquidity"
    if market.get("error") and market.get("liquidity") is None:
        return _fail(name, f"cannot verify: {market['error']}")
    liquidity = market.get("liquidity")
    if liquidity is None:
        return _fail(name, "cannot verify: pool liquidity in USD was missing")
    if liquidity < rules.min_liquidity_usd:
        return _fail(
            name,
            f"liquidity ${liquidity:,.0f} is below ${rules.min_liquidity_usd:,.0f}",
            liquidity=liquidity,
        )
    return _ok(name, f"liquidity ${liquidity:,.0f} on {market.get('pair_address')}", liquidity=liquidity)


def _check_volume(market: dict[str, Any], rules: Rules) -> Check:
    name = "real_volume"
    if market.get("error") and market.get("volume") is None:
        return _fail(name, f"cannot verify: {market['error']}")
    volume = market.get("volume")
    buys = market.get("buys")
    sells = market.get("sells")
    window = market.get("window") or "h1"
    if volume is None or buys is None or sells is None:
        return _fail(name, f"cannot verify: {window} volume or transaction counts were missing")
    try:
        buys_n = int(buys)
        sells_n = int(sells)
    except (TypeError, ValueError):
        return _fail(name, "cannot verify: transaction counts were not numbers")
    txns = buys_n + sells_n
    if volume < rules.min_volume_usd:
        return _fail(
            name,
            f"{window} volume ${volume:,.0f} is below ${rules.min_volume_usd:,.0f}",
            volume=volume,
            txns=txns,
        )
    if txns < rules.min_txns:
        return _fail(
            name,
            f"only {txns} prints in {window}; too few to treat the volume as real",
            volume=volume,
            txns=txns,
        )
    avg = volume / txns
    liquidity = market.get("liquidity") or 0.0
    if avg < rules.min_avg_trade_usd:
        return _fail(
            name,
            f"average print is ${avg:.2f}, which looks like dust rather than real flow",
            avg=round(avg, 4),
            txns=txns,
        )
    ratio = (buys_n / sells_n) if sells_n else 99.0
    turnover = (volume / liquidity) if liquidity else 99.0
    balanced = rules.wash_buy_sell_low <= ratio <= rules.wash_buy_sell_high
    if avg < rules.wash_avg_trade_usd and balanced and turnover >= rules.wash_volume_to_liquidity:
        return _fail(
            name,
            f"volume looks washed: ${avg:.2f} average print, buy/sell {ratio:.2f}, "
            f"volume is {turnover:.1f}x liquidity",
            avg=round(avg, 4),
            ratio=round(ratio, 3),
            turnover=round(turnover, 2),
        )
    return _ok(
        name,
        f"{window} volume ${volume:,.0f} across {txns} prints (avg ${avg:.2f})",
        volume=volume,
        txns=txns,
        avg=round(avg, 2),
    )


def _check_age(market: dict[str, Any], rules: Rules, now: datetime) -> Check:
    name = "min_age"
    created = market.get("pair_created")
    if created is None:
        reason = market.get("error") or "pair creation time was missing"
        return _fail(name, f"cannot verify: {reason}")
    if created > now + timedelta(minutes=2):
        return _fail(name, "pair creation time is in the future")
    age_min = (now - created).total_seconds() / 60.0
    if age_min < rules.min_age_minutes:
        return _fail(
            name,
            f"pair is {age_min:.0f} minutes old; waiting until {rules.min_age_minutes} minutes",
            age_minutes=round(age_min, 1),
        )
    return _ok(name, f"pair is {age_min / 60:.1f} hours old", age_minutes=round(age_min, 1))


def _check_socials(market: dict[str, Any]) -> Check:
    name = "has_socials"
    if market.get("error") and not market.get("social_links") and market.get("price") is None:
        return _fail(name, f"cannot verify: {market['error']}")
    links = market.get("social_links") or []
    if not links:
        return _fail(name, "no website, X, Telegram, or Discord link on the DexScreener profile or pair")
    return _ok(name, f"{len(links)} social or website link(s)", links=links[:6])


def _check_github(
    market: dict[str, Any],
    github: list[GithubRepoInput] | None,
    rules: Rules,
    now: datetime,
) -> Check:
    name = "github_if_product"
    if not market.get("product_claim"):
        return _ok(name, "token does not present as a launchpad, tool, or AI-agent project")
    repos = market.get("github_repos") or []
    if not repos:
        return _fail(name, "project presents as a launchpad, tool, or AI agent but links no GitHub repo")
    if github is None:
        return _fail(name, "cannot verify: GitHub was not queried")
    by_key = {(item.owner, item.repo): item for item in github}
    notes: list[str] = []
    for owner, repo in repos:
        item = by_key.get((owner, repo))
        if item is None or item.error or not isinstance(item.repo_json, dict):
            detail = item.error if item and item.error else "no response"
            return _fail(name, f"cannot verify github.com/{owner}/{repo}: {detail}")
        verdict = _assess_repo(item, rules, now)
        if not verdict.passed:
            return verdict
        notes.append(verdict.reason)
    return _ok(name, " ".join(notes))


def _assess_repo(item: GithubRepoInput, rules: Rules, now: datetime) -> Check:
    name = "github_if_product"
    repo = item.repo_json or {}
    if repo.get("message") and "id" not in repo:
        return _fail(name, f"github.com/{item.owner}/{item.repo} is not a usable repo ({repo.get('message')})")
    full = f"github.com/{item.owner}/{item.repo}"
    if repo.get("private") or repo.get("disabled"):
        return _fail(name, f"{full} is private or disabled")
    if repo.get("archived"):
        return _fail(name, f"{full} is archived")
    size = _num(repo.get("size"))
    if size is None:
        return _fail(name, f"cannot verify: {full} response has no size")
    commits = item.commits_json
    if isinstance(commits, dict):
        return _fail(name, f"cannot verify commits for {full}: {commits.get('message') or 'unexpected payload'}")
    if not isinstance(commits, list) or not commits:
        return _fail(name, f"{full} has no commit history")
    messages: list[str] = []
    dates: list[datetime] = []
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        body = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
        message = str(body.get("message") or "").strip().lower().splitlines()[0] if body else ""
        messages.append(message)
        author = body.get("author") if isinstance(body.get("author"), dict) else {}
        when = _parse_time(author.get("date"))
        if when is not None:
            dates.append(when)
    if not dates:
        return _fail(name, f"cannot verify: {full} commits have no dates")
    latest = max(dates)
    if latest < now - timedelta(days=rules.github_recent_days):
        return _fail(name, f"{full} last commit is older than {rules.github_recent_days} days")
    distinct = {m for m in messages if m}
    boilerplate = distinct and distinct <= GITHUB_BOILERPLATE_MESSAGES
    if len(commits) < rules.github_min_commits or size < rules.github_min_size_kb or boilerplate or len(distinct) < 2:
        return _fail(
            name,
            f"{full} looks trivial (size {size:.0f} KB, {len(commits)} recent commits shown)",
            size_kb=size,
        )
    if repo.get("fork"):
        created = _parse_time(repo.get("created_at"))
        if created is None:
            return _fail(name, f"cannot verify: fork {full} has no created_at")
        own = [d for d in dates if d > created + timedelta(hours=1)]
        if len(own) < rules.github_min_fork_own_commits:
            return _fail(
                name,
                f"{full} is a fork shell ({len(own)} commits after the fork was created)",
            )
    return _ok(
        name,
        f"{full} exists, is non-trivial ({size:.0f} KB), and was committed to recently",
        size_kb=size,
        latest=latest.date().isoformat(),
    )
