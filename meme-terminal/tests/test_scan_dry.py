"""End-to-end paper scan against mocked HTTP. Nothing leaves the process."""

from datetime import datetime, timedelta, timezone

from meme_terminal.http_client import FetchResult, HttpClient
from meme_terminal.reporting import load_daily, load_scorecard, render_daily, render_scorecard
from meme_terminal.scan import run_scan
from meme_terminal.sources import (
    boosts_url,
    new_tokens_url,
    pairs_url,
    profiles_url,
    rug_report_url,
    takeovers_url,
    tokens_url,
)
from meme_terminal.storage import Store

from tests.fixtures import BAD, GOOD, LP, NOW, dex_pair, rpc_mint, rug_report


class FakeClient:
    def __init__(self) -> None:
        self.price = "1.0"
        self.methods: list[str] = []

    def get_json(self, url: str) -> FetchResult:
        if url == profiles_url():
            rows = [
                {"chainId": "solana", "tokenAddress": GOOD, "description": "a dog coin", "links": []},
                {"chainId": "solana", "tokenAddress": BAD, "description": "nope", "links": []},
                {"chainId": "ethereum", "tokenAddress": "0xabc", "description": "", "links": []},
            ]
            return self._ok(url, rows)
        if url in {boosts_url(), takeovers_url(), new_tokens_url()}:
            return self._ok(url, [])
        if url == pairs_url(GOOD):
            return self._ok(url, [dex_pair(GOOD, self.price, name="Good Boy", symbol="GOOD")])
        if url == pairs_url(BAD):
            return self._ok(url, [dex_pair(BAD, self.price, name="Bad Coin", symbol="BAD")])
        if url.startswith(tokens_url([]).rsplit("/", 1)[0]):
            mints = url.rsplit("/", 1)[-1].split(",")
            rows = []
            for mint in mints:
                if mint == GOOD:
                    rows.append(dex_pair(GOOD, self.price, symbol="GOOD"))
                elif mint == BAD:
                    rows.append(dex_pair(BAD, self.price, symbol="BAD"))
            return self._ok(url, rows)
        if url == rug_report_url(GOOD):
            return self._ok(url, rug_report(GOOD))
        if url == rug_report_url(BAD):
            return self._ok(
                url,
                rug_report(BAD, mint_authority="StillMint11111111111111111111111111111111"),
            )
        return FetchResult(False, 404, None, f"unmocked {url}", url)

    def post_json(self, url: str, payload: dict) -> FetchResult:
        method = payload["method"]
        self.methods.append(method)
        if method not in {"getAccountInfo", "getTokenLargestAccounts"}:
            return FetchResult(False, None, None, "blocked", url)
        address = payload["params"][0]
        if method == "getTokenLargestAccounts":
            value: object = []
        elif address == LP:
            value = rpc_mint(supply="0")
        elif address == BAD:
            value = rpc_mint(mint_authority="StillMint11111111111111111111111111111111")
        else:
            value = rpc_mint()
        return self._ok(url, {"jsonrpc": "2.0", "id": 1, "result": {"value": value}})

    @staticmethod
    def _ok(url: str, data) -> FetchResult:
        return FetchResult(True, 200, data, None, url)


def test_scan_opens_only_the_clean_token_and_the_scorecard_says_no_go(tmp_path):
    store = Store(tmp_path / "paper.duckdb")
    client = FakeClient()
    when = NOW
    summary = run_scan(store, client, now=when)
    assert summary.opened == 1
    assert summary.passed == 1
    assert summary.halted is False
    assert summary.equity is not None and summary.equity < 1000  # fee + entry slippage
    trades = store.query("SELECT * FROM paper_trades")
    assert len(trades) == 1
    assert trades[0]["mint"] == GOOD
    assert trades[0]["status"] == "open"
    assert trades[0]["initial_notional_usd"] == 75
    assert trades[0]["mcap_bucket"] == "50-250k"
    assert trades[0]["entry_source"] == "dex_profile"
    skipped = store.query("SELECT reason FROM decisions WHERE mint = ?", [BAD])
    assert skipped and "mint_authority_revoked" in skipped[0]["reason"]
    assert "sendTransaction" not in client.methods

    client.price = "3"
    later = when + timedelta(hours=1)
    run_scan(store, client, now=later)
    updated = store.query("SELECT * FROM paper_trades")[0]
    assert updated["half_sold"] is True or updated["half_sold"] == True
    assert updated["status"] == "open"
    events = store.query("SELECT kind FROM trade_events ORDER BY event_at")
    assert [row["kind"] for row in events] == ["open", "partial"]

    day = when.strftime("%Y-%m-%d")
    report = render_daily(load_daily(store, day))
    assert "Paper only" in report
    assert "GOOD" in report
    assert "BAD" in report
    assert "mint authority is still active" in report
    card = render_scorecard(load_scorecard(store))
    assert "NO-GO" in card
    assert "1 calls" in card or "Calls (entries): 1" in card
    assert "50-250k" in card
    store.close()


def test_http_client_refuses_anything_that_would_sign():
    client = HttpClient(retries=1, intervals={}, sleeper=lambda _s: None)
    blocked = client.post_json(
        "https://api.mainnet-beta.solana.com",
        {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction", "params": []},
    )
    assert blocked.ok is False
    assert "not allowed" in (blocked.error or "")
    try:
        client.get_json("https://example.com/sendTransaction")
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_same_second_equity_snapshots_keep_the_later_mark(tmp_path):
    store = Store(tmp_path / "equity.duckdb")
    stamp = store.query("SELECT snapshot_at FROM equity_snapshots")[0]["snapshot_at"]
    store.con.execute(
        "INSERT INTO equity_snapshots VALUES (?, ?, ?, ?, ?, ?, FALSE)",
        ["scan-1", "scan-1", stamp, 900.0, 50.0, 950.0],
    )
    card = load_scorecard(store)
    assert card["equity"] == 950
    assert card["equity_curve"] == [1000, 950]
    store.close()


def test_report_for_an_empty_day_has_a_headline_and_no_invented_pnl(tmp_path):
    store = Store(tmp_path / "empty.duckdb")
    text = render_daily(load_daily(store, "2020-01-01"))
    assert "No scan was stored" in text
    store.close()
