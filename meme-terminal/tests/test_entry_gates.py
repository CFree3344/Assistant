"""Tightened entry gates and the multi-source scorecard. No network."""

from datetime import timedelta

from meme_terminal.checklist import evaluate, render_entry_scorecard

from tests.fixtures import NOW, dex_pair, passing_raw, rug_report


def _by_name(result):
    return {check.name: check for check in result.checks}


def _with_pair(pair, **overrides):
    raw = passing_raw(dex_pairs=[pair], **overrides)
    return evaluate(raw, now=NOW)


def test_clean_token_is_go_and_missing_intel_is_not_applied():
    result = evaluate(passing_raw(), now=NOW)
    assert result.failures() == []
    assert result.verdict == "GO"
    checks = _by_name(result)
    for name in (
        "mint_authority_revoked",
        "freeze_authority_revoked",
        "lp_burned_or_long_locked",
        "min_age",
        "minutes_since_graduation",
        "market_cap_band",
        "sell_exit_impact",
        "dev_and_bundle_concentration",
    ):
        assert checks[name].status == "PASS", checks[name].reason
    for name in (
        "bundle_held_at_launch",
        "linked_early_buyers",
        "coordinated_buys",
        "late_chase",
        "rugcheck_onchain",
        "stalkchain_onchain",
        "fomo_warnings",
        "x_intel",
        "padre_intel",
    ):
        assert checks[name].status == "NOT_APPLIED", (name, checks[name].status, checks[name].reason)
        assert checks[name].passed
    text = render_entry_scorecard(result)
    assert "Verdict: GO" in text
    assert "NOT_APPLIED" in text
    assert "PASS" in text
    assert "| `stalkchain_onchain` | stalkchain | NOT_APPLIED |" in text
    assert "| `fomo_warnings` | fomo | NOT_APPLIED |" in text
    assert "| `x_intel` | x | NOT_APPLIED |" in text
    assert "| `padre_intel` | padre | NOT_APPLIED |" in text
    # A missing tape must not grow a demand sentence or a measured pump.
    assert "%" not in checks["late_chase"].reason
    assert "two-sided" not in checks["late_chase"].reason
    assert "mentions" not in checks["x_intel"].reason


def test_market_cap_band_rejects_outside_and_missing():
    low = _with_pair(dex_pair(market_cap=49_999))
    assert _by_name(low)["market_cap_band"].status == "FAIL"
    assert low.verdict == "NO-GO"

    high = _with_pair(dex_pair(market_cap=250_001))
    assert _by_name(high)["market_cap_band"].status == "FAIL"

    for cap in (50_000, 120_000, 250_000):
        result = _with_pair(dex_pair(market_cap=cap))
        assert _by_name(result)["market_cap_band"].status == "PASS", cap

    pair = dex_pair()
    pair.pop("marketCap", None)
    pair.pop("fdv", None)
    missing = _with_pair(pair)
    assert _by_name(missing)["market_cap_band"].status == "FAIL"
    assert "cannot verify" in _by_name(missing)["market_cap_band"].reason


def test_sell_impact_fails_a_thin_pool_and_a_thin_quote():
    thin_pool = _with_pair(dex_pair(liquidity=12_000))
    check = _by_name(thin_pool)["sell_exit_impact"]
    assert check.status == "FAIL"
    assert "paper ticket" in check.reason
    assert thin_pool.verdict == "NO-GO"

    thin_quote = evaluate(
        passing_raw(rug_report=rug_report(quote_usd=2_000, base_usd=18_000)),
        now=NOW,
    )
    quote = _by_name(thin_quote)["sell_exit_impact"]
    assert quote.status == "FAIL"
    assert "quote-side" in quote.reason

    # Quote depth was not in the report: do not invent it, and still judge pool slippage.
    report = rug_report()
    for market in report["markets"]:
        market["lp"].pop("quoteUSD", None)
    no_quote = evaluate(passing_raw(rug_report=report), now=NOW)
    impact = _by_name(no_quote)["sell_exit_impact"]
    assert impact.status == "PASS", impact.reason
    assert impact.evidence["quote_impact_status"] == "NOT_APPLIED"


def test_late_chase_rejects_vertical_moves_and_weak_flow_only_when_present():
    vertical = dex_pair()
    vertical["priceChange"] = {"m5": 90}
    result = _with_pair(vertical)
    assert _by_name(result)["late_chase"].status == "FAIL"
    assert "already vertical" in _by_name(result)["late_chase"].reason
    assert result.verdict == "NO-GO"

    one_minute = dex_pair()
    one_minute["priceChange"] = {"m1": 55, "m5": 5}
    result = _with_pair(one_minute)
    assert _by_name(result)["late_chase"].status == "FAIL"

    weak = dex_pair()
    weak["priceChange"] = {"m5": 50}
    weak["txns"]["m5"] = {"buys": 30, "sells": 2}
    weak["volume"]["m5"] = 8_000
    result = _with_pair(weak)
    assert _by_name(result)["late_chase"].status == "FAIL"
    assert "weak organic flow" in _by_name(result)["late_chase"].reason

    organic = dex_pair()
    organic["priceChange"] = {"m5": 50}
    result = _with_pair(organic)
    assert _by_name(result)["late_chase"].status == "PASS", _by_name(result)["late_chase"].reason

    calm = dex_pair()
    calm["priceChange"] = {"m5": 10}
    result = _with_pair(calm)
    assert _by_name(result)["late_chase"].status == "PASS"

    unknown = dex_pair()
    unknown["priceChange"] = {"m5": 50}
    unknown["txns"].pop("m5")
    unknown["volume"].pop("m5")
    result = _with_pair(unknown)
    chase = _by_name(result)["late_chase"]
    assert chase.status == "NOT_APPLIED"
    assert "no demand was assumed" in chase.reason
    assert result.verdict == "GO"

    absent = evaluate(passing_raw(), now=NOW)
    assert _by_name(absent)["late_chase"].status == "NOT_APPLIED"


def test_launch_bundle_uses_only_a_launch_field():
    current = rug_report(networks=[{"id": "net", "currentHolding": 300_000_000_000, "type": "transfer"}])
    result = evaluate(passing_raw(rug_report=current), now=NOW)
    checks = _by_name(result)
    assert checks["dev_and_bundle_concentration"].status == "FAIL"
    assert checks["bundle_held_at_launch"].status == "NOT_APPLIED"
    assert "launch_bundle_pct" not in checks["bundle_held_at_launch"].evidence

    launched = rug_report(
        networks=[{"id": "net", "currentHolding": 0, "initialHolding": 200_000_000_000}]
    )
    result = evaluate(passing_raw(rug_report=launched), now=NOW)
    launch = _by_name(result)["bundle_held_at_launch"]
    assert launch.status == "FAIL"
    assert launch.evidence["launch_bundle_pct"] == 20.0
    assert _by_name(result)["dev_and_bundle_concentration"].status == "PASS"

    explicit = rug_report()
    explicit["bundleHeldAtLaunchPct"] = 16
    result = evaluate(passing_raw(rug_report=explicit), now=NOW)
    assert _by_name(result)["bundle_held_at_launch"].status == "FAIL"

    fine = rug_report()
    fine["bundleHeldAtLaunchPct"] = 10
    result = evaluate(passing_raw(rug_report=fine), now=NOW)
    assert _by_name(result)["bundle_held_at_launch"].status == "PASS"


def test_coordinated_buys_follow_the_early_buyer_cap_when_present():
    absent = evaluate(passing_raw(), now=NOW)
    assert _by_name(absent)["coordinated_buys"].status == "NOT_APPLIED"

    hot = rug_report()
    hot["coordinatedBuyPct"] = 30
    result = evaluate(passing_raw(rug_report=hot), now=NOW)
    assert _by_name(result)["coordinated_buys"].status == "FAIL"
    assert result.verdict == "NO-GO"

    fine = rug_report()
    fine["coordinatedBuyPct"] = 10
    result = evaluate(passing_raw(rug_report=fine), now=NOW)
    assert _by_name(result)["coordinated_buys"].status == "PASS"

    early = rug_report()
    early["linkedEarlyBuyersPct"] = 30
    result = evaluate(passing_raw(rug_report=early), now=NOW)
    assert _by_name(result)["linked_early_buyers"].status == "FAIL"
    assert _by_name(result)["coordinated_buys"].status == "NOT_APPLIED"


def test_rugcheck_danger_skips_and_a_missing_risk_list_does_not_invent_one():
    danger = rug_report()
    danger["risks"] = [{"level": "danger", "name": "mutable mint"}]
    result = evaluate(passing_raw(rug_report=danger), now=NOW)
    assert _by_name(result)["rugcheck_onchain"].status == "FAIL"
    assert result.verdict == "NO-GO"
    text = render_entry_scorecard(result)
    assert "Verdict: NO-GO" in text
    assert "FAIL" in text

    warned = rug_report()
    warned["risks"] = [{"level": "warn", "name": "new token"}]
    result = evaluate(passing_raw(rug_report=warned), now=NOW)
    assert _by_name(result)["rugcheck_onchain"].status == "PASS"

    rugged = rug_report(rugged=True)
    result = evaluate(passing_raw(rug_report=rugged), now=NOW)
    assert _by_name(result)["rugcheck_onchain"].status == "FAIL"


def test_optional_sources_fail_only_on_fields_they_actually_carry():
    stalk_bad = evaluate(passing_raw(stalkchain={"mint_authority_revoked": False}), now=NOW)
    assert _by_name(stalk_bad)["stalkchain_onchain"].status == "FAIL"
    assert stalk_bad.verdict == "NO-GO"

    stalk_bundle = evaluate(passing_raw(stalkchain={"bundle_held_at_launch_pct": 20}), now=NOW)
    assert _by_name(stalk_bundle)["stalkchain_onchain"].status == "FAIL"

    stalk_ok = evaluate(
        passing_raw(stalkchain={"mint_authority_revoked": True, "bundle_held_at_launch_pct": 10}),
        now=NOW,
    )
    assert _by_name(stalk_ok)["stalkchain_onchain"].status == "PASS"
    assert stalk_ok.verdict == "GO"

    # A mention count is not a safety reading and is not turned into demand.
    mentions = evaluate(passing_raw(stalkchain={"mentions": 400}), now=NOW)
    slot = _by_name(mentions)["stalkchain_onchain"]
    assert slot.status == "NOT_APPLIED"
    assert "400" not in slot.reason

    fomo_bad = evaluate(
        passing_raw(fomo={"warnings": [{"level": "danger", "message": "bundled launch"}]}),
        now=NOW,
    )
    fomo = _by_name(fomo_bad)["fomo_warnings"]
    assert fomo.status == "FAIL"
    assert fomo.evidence["auto_open"] is False
    assert fomo_bad.verdict == "NO-GO"

    fomo_info = evaluate(
        passing_raw(fomo={"warnings": [{"level": "info", "message": "on a list"}]}),
        now=NOW,
    )
    assert _by_name(fomo_info)["fomo_warnings"].status == "PASS"
    assert _by_name(fomo_info)["fomo_warnings"].evidence["auto_open"] is False
    assert fomo_info.verdict == "GO"

    copy_only = evaluate(passing_raw(fomo={"copy_signal": True, "leaderboard": True}), now=NOW)
    copied = _by_name(copy_only)["fomo_warnings"]
    assert copied.status == "NOT_APPLIED"
    assert copied.evidence["auto_open"] is False
    assert copy_only.verdict == "GO"

    inorganic = evaluate(passing_raw(x_intel={"organic": False}), now=NOW)
    assert _by_name(inorganic)["x_intel"].status == "FAIL"

    quiet_x = evaluate(passing_raw(x_intel={"handle": "inu"}), now=NOW)
    x_slot = _by_name(quiet_x)["x_intel"]
    assert x_slot.status == "NOT_APPLIED"
    assert "mentions" not in x_slot.reason
    assert "mentions" not in x_slot.evidence

    organic = evaluate(passing_raw(x_intel={"organic": True}), now=NOW)
    assert _by_name(organic)["x_intel"].status == "PASS"
    assert "mentions" not in _by_name(organic)["x_intel"].evidence

    padre_bad = evaluate(passing_raw(padre={"bundle_held_at_launch_pct": 18}), now=NOW)
    assert _by_name(padre_bad)["padre_intel"].status == "FAIL"
    assert padre_bad.verdict == "NO-GO"

    padre_empty = evaluate(passing_raw(padre={}), now=NOW)
    assert _by_name(padre_empty)["padre_intel"].status == "NOT_APPLIED"
    assert padre_empty.verdict == "GO"


def test_non_pump_graduation_stays_not_applied():
    plain = rug_report()
    for market in plain["markets"]:
        market["marketType"] = "raydium"
    result = evaluate(
        passing_raw(
            dex_pairs=[dex_pair(dex_id="raydium", age=timedelta(hours=3))],
            rug_report=plain,
        ),
        now=NOW,
    )
    check = _by_name(result)["minutes_since_graduation"]
    assert check.status == "NOT_APPLIED"
    assert check.passed
    assert result.verdict == "GO"
