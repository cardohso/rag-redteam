"""HTTP client for the target: paced requests, bounded retries, full response capture."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

# The target answers 429 for its own rate limit and 503 when the LLM provider is rate-limited.
RETRY_STATUS = {429, 502, 503, 504}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@dataclass
class Exchange:
    """One request and what came back. `status` is None when no HTTP response was received."""

    method: str
    path: str
    request_body: str
    status: int | None = None
    text: str = ""
    body: Any = None  # parsed JSON, or None when the body was not JSON
    latency_ms: float | None = None
    attempts: int = 1
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def json(self) -> dict | None:
        return self.body if isinstance(self.body, dict) else None

    @property
    def answer(self) -> str | None:
        data = self.json
        return data.get("answer") if data and isinstance(data.get("answer"), str) else None

    @property
    def refused(self) -> bool | None:
        data = self.json
        return data.get("refused") if data and isinstance(data.get("refused"), bool) else None

    @property
    def citations(self) -> list[dict]:
        data = self.json
        items = data.get("citations") if data else None
        return [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []


def is_local(base_url: str) -> bool:
    host = httpx.URL(base_url).host
    return host in LOCAL_HOSTS or host.endswith(".localhost")


class TargetClient:
    def __init__(
        self,
        base_url: str,
        *,
        ask_path: str = "/ask",
        health_path: str = "/health",
        rpm: float = 10.0,
        max_retries: int = 3,
        backoff: float = 5.0,
        timeout: float = 90.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.ask_path, self.health_path = ask_path, health_path
        self.min_interval = 60.0 / rpm
        self.max_retries, self.backoff = max_retries, backoff
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self._sleep, self._clock = sleep, clock
        self._last_sent: float | None = None

    def close(self) -> None:
        self._http.close()

    def _pace(self) -> None:
        if self._last_sent is not None:
            wait = self.min_interval - (self._clock() - self._last_sent)
            if wait > 0:
                self._sleep(wait)
        self._last_sent = self._clock()

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            try:
                return min(float(response.headers["retry-after"]), 120.0)
            except (KeyError, ValueError):
                pass
        return min(self.backoff * 2**attempt, 60.0)

    def send(self, method: str, path: str, body: str | None = None) -> Exchange:
        """Send a request, retrying rate limits and transient failures. The result always
        describes the last attempt, so a persistent 429 is recorded, not raised."""
        headers = {"content-type": "application/json"} if body is not None else {}
        exchange = Exchange(method, path, body or "")
        for attempt in range(self.max_retries + 1):
            self._pace()
            started = self._clock()
            response: httpx.Response | None = None
            exchange.text, exchange.body, exchange.headers = "", None, {}
            try:
                response = self._http.request(method, path, content=body, headers=headers)
            except httpx.TransportError as exc:
                exchange.error = f"{type(exc).__name__}: {exc}"
                exchange.status = None
            else:
                exchange.error = None
                exchange.status = response.status_code
                exchange.text = response.text
                exchange.headers = {k: v for k, v in response.headers.items()}
                try:
                    exchange.body = response.json()
                except ValueError:
                    exchange.body = None
            exchange.latency_ms = round((self._clock() - started) * 1000, 1)
            exchange.attempts = attempt + 1
            retryable = response is None or response.status_code in RETRY_STATUS
            if not retryable or attempt == self.max_retries:
                break
            self._sleep(self._retry_delay(attempt, response))
        return exchange

    def ask(self, question: str) -> Exchange:
        return self.send("POST", self.ask_path, json.dumps({"question": question}))

    def health(self) -> dict | None:
        exchange = self.send("GET", self.health_path)
        return exchange.json if exchange.status == 200 else None
