"""Checklist tests driven by mocked API payloads."""

from datetime import timedelta

from meme_terminal.checklist import evaluate
from meme_terminal.config import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

from tests.fixtures import (
    BAD,
    CREATOR,
    GOOD,
    LP,
    NOW,
    POOL,
    STREAMFLOW,
    dex_pair,
    github_ok,
    passing_raw,
    rpc_mint,
    rug_report,
)


def _by_name(result):
    return {check.name: check for check in result.checks}


def test_clean_token_passes_every_check():
    result = evaluate(passing_raw(), now=NOW)
    failed = [(c.name, c.reason) for c in result.failures()]
    assert failed == []
    assert result.passed
    assert result.symbol == "GOOD"
    assert result.price_usd == 0.001


def test_active_mint_authority_fails_even_if_the_other_source_is_clean():
    raw = passing_raw(rpc_mint=rpc_mint(mint_authority="StillMint11111111111111111111111111111111"))
    result = evaluate(raw, now=NOW)
    assert not result.passed
    assert _by_name(result)["mint_authority_revoked"].reason == "mint authority is still active"


def test_missing_rpc_is_a_fail_not_a_pass():
    raw = passing_raw(rpc_mint=None)
    result = evaluate(raw, now=NOW)
    assert not _by_name(result)["mint_authority_revoked"].passed
    assert "cannot verify" in _by_name(result)["mint_authority_revoked"].reason
    assert not _by_name(result)["freeze_authority_revoked"].passed


def test_freeze_authority_still_set_fails():
    report = rug_report(freeze_authority="Freeze1111111111111111111111111111111111")
    result = evaluate(passing_raw(rug_report=report), now=NOW)
    assert not _by_name(result)["freeze_authority_revoked"].passed


def test_token2022_transfer_fee_fails():
    raw = passing_raw(
        rpc_mint=rpc_mint(owner=TOKEN_2022_PROGRAM, extensions=[]),
        rug_report=rug_report(
            token_program=TOKEN_2022_PROGRAM,
            token_extensions={"transferFeeConfig": {"newerTransferFee": {"transferFeeBasisPoints": 500}}},
        ),
    )
    result = evaluate(raw, now=NOW)
    check = _by_name(result)["no_freeze_like_extensions"]
    assert not check.passed
    assert "transfer fee" in check.reason


def test_partial_lock_fails():
    report = rug_report(locked_pct=80)
    result = evaluate(passing_raw(rug_report=report), now=NOW)
    check = _by_name(result)["lp_burned_or_long_locked"]
    assert not check.passed
    assert "80.0%" in check.reason


def test_zero_lp_supply_on_rpc_counts_as_burned():
    result = evaluate(passing_raw(), now=NOW)
    assert _by_name(result)["lp_burned_or_long_locked"].passed


def test_incinerator_holding_counts_as_burned():
    report = rug_report(lp_supply=1000, locked_pct=100, quote_usd=12_000)
    largest = [
        {"address": "BurnAcct11111111111111111111111111111111", "amount": "960"},
        {"address": "OtherAcct1111111111111111111111111111111", "amount": "40"},
    ]
    accounts = [
        rpc_holder("1nc1nerator11111111111111111111111111111111"),
        rpc_holder("Someone1111111111111111111111111111111111"),
    ]
    raw = passing_raw(
        rug_report=report,
        rpc_lp_mint=rpc_mint(supply="1000"),
        rpc_lp_largest=largest,
        rpc_lp_accounts=accounts,
    )
    result = evaluate(raw, now=NOW)
    assert _by_name(result)["lp_burned_or_long_locked"].passed


def test_short_lock_fails_and_a_claim_without_a_timestamp_fails():
    short = {
        "Locker1111111111111111111111111111111111": {
            "programId": STREAMFLOW,
            "name": "Streamflow",
            "unlockDate": int((NOW + timedelta(days=10)).timestamp()),
        }
    }
    result = evaluate(_locked(short, days_ok=False), now=NOW)
    assert not _by_name(result)["lp_burned_or_long_locked"].passed
    assert "shorter than" in _by_name(result)["lp_burned_or_long_locked"].reason

    no_time = {
        "Locker1111111111111111111111111111111111": {
            "programId": STREAMFLOW,
            "name": "Streamflow",
        }
    }
    result = evaluate(_locked(no_time, days_ok=False), now=NOW)
    assert "no unlock time" in _by_name(result)["lp_burned_or_long_locked"].reason


def test_long_lock_by_a_known_program_passes_when_rpc_owner_matches():
    account = "Locker1111111111111111111111111111111111"
    lockers = {
        account: {
            "programId": STREAMFLOW,
            "name": "Streamflow",
            "unlockDate": int((NOW + timedelta(days=400)).timestamp()),
        }
    }
    raw = _locked(lockers, days_ok=True)
    result = evaluate(raw, now=NOW)
    check = _by_name(result)["lp_burned_or_long_locked"]
    assert check.passed, check.reason
    assert STREAMFLOW in check.reason


def test_unknown_locker_fails():
    account = "Locker1111111111111111111111111111111111"
    lockers = {
        account: {
            "programId": "NotALocker111111111111111111111111111111",
            "name": "Some Blog",
            "unlockDate": int((NOW + timedelta(days=400)).timestamp()),
        }
    }
    result = evaluate(_locked(lockers, days_ok=True), now=NOW)
    assert not _by_name(result)["lp_burned_or_long_locked"].passed
    assert "not a known program" in _by_name(result)["lp_burned_or_long_locked"].reason


def test_pool_is_excluded_from_top10_and_a_concentrated_wallet_fails():
    result = evaluate(passing_raw(), now=NOW)
    assert _by_name(result)["top10_concentration"].passed
    assert _by_name(result)["top10_concentration"].evidence["top10_pct"] == 20.0

    holders = [
        {"address": "Whale11111111111111111111111111111111111", "owner": "WhaleOwner111111111111111111111111111111", "pct": 55.0, "insider": False}
    ]
    result = evaluate(passing_raw(rug_report=rug_report(holders=holders)), now=NOW)
    assert not _by_name(result)["top10_concentration"].passed


def test_dev_and_bundle_thresholds():
    holders = [
        {"address": "DevAcct111111111111111111111111111111111", "owner": CREATOR, "pct": 12.0, "insider": False},
        {"address": "WalletA111111111111111111111111111111111", "owner": "OwnerA1111111111111111111111111111111111", "pct": 2.0, "insider": False},
    ]
    result = evaluate(passing_raw(rug_report=rug_report(holders=holders)), now=NOW)
    assert not _by_name(result)["dev_and_bundle_concentration"].passed
    assert "creator holds" in _by_name(result)["dev_and_bundle_concentration"].reason

    bundled = rug_report(
        networks=[{"id": "net", "currentHolding": 300_000_000_000, "type": "transfer"}]
    )
    result = evaluate(passing_raw(rug_report=bundled), now=NOW)
    # 300e9 / 1000e9 = 30% > 20%
    assert not _by_name(result)["dev_and_bundle_concentration"].passed


def test_missing_insider_data_fails_closed():
    report = rug_report()
    report["insiderNetworks"] = None
    for row in report["topHolders"]:
        row.pop("insider", None)
    result = evaluate(passing_raw(rug_report=report), now=NOW)
    assert "cannot verify" in _by_name(result)["dev_and_bundle_concentration"].reason


def test_liquidity_volume_age_and_socials():
    thin = passing_raw(dex_pairs=[dex_pair(liquidity=1_000)])
    result = evaluate(thin, now=NOW)
    assert not _by_name(result)["min_liquidity"].passed

    quiet = passing_raw(dex_pairs=[dex_pair(volume=100, buys=5, sells=4)])
    result = evaluate(quiet, now=NOW)
    assert not _by_name(result)["real_volume"].passed

    dust = passing_raw(dex_pairs=[dex_pair(volume=20_000, buys=15_000, sells=15_000)])
    result = evaluate(dust, now=NOW)
    assert not _by_name(result)["real_volume"].passed
    assert "dust" in _by_name(result)["real_volume"].reason or "washed" in _by_name(result)["real_volume"].reason

    young = passing_raw(dex_pairs=[dex_pair(age=timedelta(minutes=5))])
    result = evaluate(young, now=NOW)
    assert not _by_name(result)["min_age"].passed

    quiet_social = passing_raw(dex_pairs=[dex_pair(socials=False)], profile={"description": "", "links": []})
    result = evaluate(quiet_social, now=NOW)
    assert not _by_name(result)["has_socials"].passed


def test_wash_pattern_fails_and_one_sided_flow_does_not():
    washed = dex_pair(volume=200_000, buys=30_000, sells=30_000, liquidity=20_000)
    result = evaluate(passing_raw(dex_pairs=[washed]), now=NOW)
    assert not _by_name(result)["real_volume"].passed

    buyers = dex_pair(volume=200_000, buys=40_000, sells=5_000, liquidity=20_000)
    result = evaluate(passing_raw(dex_pairs=[buyers]), now=NOW)
    # Average print is $200k/45k ≈ $4.44, above the $1 dust floor, and the flow is not balanced.
    assert _by_name(result)["real_volume"].passed


def test_github_is_required_only_for_a_product_claim():
    meme = evaluate(passing_raw(), now=NOW)
    assert _by_name(meme)["github_if_product"].passed

    claimed = passing_raw(
        dex_pairs=[dex_pair(name="Agent Framework", symbol="AGENT")],
        profile={"description": "an ai agent launchpad", "links": []},
    )
    result = evaluate(claimed, now=NOW)
    assert not _by_name(result)["github_if_product"].passed
    assert "no GitHub" in _by_name(result)["github_if_product"].reason

    linked = passing_raw(
        dex_pairs=[dex_pair(name="Agent Framework", symbol="AGENT")],
        profile={
            "description": "an ai agent launchpad",
            "links": [{"type": "website", "url": "https://github.com/acme/agent"}],
        },
        github=[github_ok()],
    )
    result = evaluate(linked, now=NOW)
    assert _by_name(result)["github_if_product"].passed, _by_name(result)["github_if_product"].reason


def test_fork_shell_and_missing_repo_fail():
    shell = github_ok()
    shell.repo_json = dict(shell.repo_json)
    shell.repo_json["fork"] = True
    shell.repo_json["created_at"] = (NOW - timedelta(days=1)).isoformat()
    # Commit dates are several days older than the fork, so none of them are the fork's own work.
    raw = passing_raw(
        dex_pairs=[dex_pair(name="Trading Bot", symbol="BOT")],
        profile={"description": "trading bot", "links": [{"url": "https://github.com/acme/agent"}]},
        github=[shell],
    )
    result = evaluate(raw, now=NOW)
    assert "fork shell" in _by_name(result)["github_if_product"].reason

    missing = github_ok()
    missing.repo_json = {"message": "Not Found"}
    missing.commits_json = None
    raw = passing_raw(
        dex_pairs=[dex_pair(name="Trading Bot", symbol="BOT")],
        profile={"description": "trading bot", "links": [{"url": "https://github.com/acme/agent"}]},
        github=[missing],
    )
    result = evaluate(raw, now=NOW)
    assert not _by_name(result)["github_if_product"].passed


def test_dex_outage_fails_closed():
    raw = passing_raw(dex_pairs=None)
    result = evaluate(raw, now=NOW)
    assert not result.passed
    assert "cannot verify" in _by_name(result)["min_liquidity"].reason
    assert result.mint == GOOD


def rpc_holder(owner: str):
    return {
        "owner": TOKEN_PROGRAM,
        "data": {"parsed": {"type": "account", "info": {"owner": owner, "mint": LP}}},
    }


def _locked(lockers: dict, days_ok: bool):
    report = rug_report(lp_supply=1000, locked_pct=100, lockers=lockers)
    account_id = next(iter(lockers))
    program = lockers[account_id].get("programId")
    return passing_raw(
        rug_report=report,
        rpc_lp_mint=rpc_mint(supply="1000"),
        rpc_lp_largest=[{"address": "Holder111111111111111111111111111111111", "amount": "1000"}],
        rpc_lp_accounts=[rpc_holder("Someone1111111111111111111111111111111111")],
        rpc_locker_accounts={account_id: {"owner": program, "executable": False, "data": {}}},
    )
