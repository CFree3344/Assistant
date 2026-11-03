"""HTTPS JSON client for public read APIs.

RPC methods are allowlisted. Anything that would sign or submit a transaction
is rejected before a socket opens.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from meme_terminal.config import (
    DEX_MIN_INTERVAL_S,
    GITHUB_MIN_INTERVAL_S,
    HTTP_RETRIES,
    HTTP_TIMEOUT_S,
    RPC_MIN_INTERVAL_S,
    RUG_MIN_INTERVAL_S,
)

ALLOWED_RPC_METHODS = frozenset({"getAccountInfo", "getTokenLargestAccounts", "getMultipleAccounts"})
FORBIDDEN_MARKERS = (
    "sendtransaction",
    "signtransaction",
    "keypair",
    "privatekey",
    "private-key",
    "secretkey",
)

USER_AGENT = "meme-terminal-paper/0.1 (dry-run; read-only)"


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    status: int | None
    data: Any
    error: str | None
    url: str


class HttpClient:
    """Polite client. One instance per scan so the rate-limit clock is shared."""

    def __init__(
        self,
        *,
        timeout: float = HTTP_TIMEOUT_S,
        retries: int = HTTP_RETRIES,
        intervals: dict[str, float] | None = None,
        github_token: str | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.intervals = intervals or {
            "api.dexscreener.com": DEX_MIN_INTERVAL_S,
            "api.rugcheck.xyz": RUG_MIN_INTERVAL_S,
            "api.mainnet-beta.solana.com": RPC_MIN_INTERVAL_S,
            "solana-rpc.publicnode.com": RPC_MIN_INTERVAL_S,
            "api.github.com": GITHUB_MIN_INTERVAL_S,
        }
        self.github_token = github_token
        self._sleeper = sleeper
        self._last: dict[str, float] = {}

    def get_json(self, url: str) -> FetchResult:
        return self._request(url, None, None)

    def post_json(self, url: str, payload: dict[str, Any]) -> FetchResult:
        method = payload.get("method")
        if method is not None and method not in ALLOWED_RPC_METHODS:
            return FetchResult(False, None, None, f"RPC method {method} is not allowed", url)
        return self._request(url, payload, method if isinstance(method, str) else None)

    def _request(self, url: str, payload: dict[str, Any] | None, rpc_method: str | None) -> FetchResult:
        self._reject_forbidden(url, payload)
        if not url.startswith("https://"):
            return FetchResult(False, None, None, "only https URLs are allowed", url)
        host = urlparse(url).netloc
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if host == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            if self.github_token:
                headers["Authorization"] = f"Bearer {self.github_token}"
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        last = FetchResult(False, None, None, "no attempt", url)
        for attempt in range(self.retries):
            self._wait(host)
            req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    status = resp.status
                data = json.loads(raw.decode("utf-8")) if raw else None
                last = self._interpret(url, status, data, rpc_method)
                if last.ok or status not in {429, 500, 502, 503, 504}:
                    return last
            except urllib.error.HTTPError as exc:
                detail = _read_error(exc)
                last = FetchResult(False, exc.code, None, detail, url)
                if exc.code not in {429, 500, 502, 503, 504}:
                    return last
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last = FetchResult(False, None, None, f"{type(exc).__name__}: {exc}", url)
            self._sleeper(min(8.0, 0.5 * (2**attempt)))
        return last

    def _interpret(self, url: str, status: int, data: Any, rpc_method: str | None) -> FetchResult:
        if rpc_method and isinstance(data, dict) and isinstance(data.get("error"), dict):
            message = data["error"].get("message") or "rpc error"
            return FetchResult(False, status, data, str(message), url)
        if status >= 400:
            return FetchResult(False, status, data, f"HTTP {status}", url)
        return FetchResult(True, status, data, None, url)

    def _wait(self, host: str) -> None:
        interval = self.intervals.get(host, 0.0)
        if interval <= 0:
            return
        now = time.monotonic()
        earliest = self._last.get(host, 0.0) + interval
        if now < earliest:
            self._sleeper(earliest - now)
        self._last[host] = time.monotonic()

    @staticmethod
    def _reject_forbidden(url: str, payload: dict[str, Any] | None) -> None:
        blob = url.lower()
        if payload is not None:
            blob += json.dumps(payload).lower()
        for marker in FORBIDDEN_MARKERS:
            if marker in blob:
                raise RuntimeError(f"refusing a request that looks like signing or order entry ({marker})")


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        body = ""
    message = f"HTTP {exc.code}"
    if body:
        message = f"{message}: {body}"
    return message
