"""Minimal read-only JSON HTTP client plus a raw-response JSONL cache.

Stdlib only (urllib), matching the pattern the repository's research
workflows already use. Every request is a GET; there is no code path here
that can POST, PUT, PATCH or DELETE.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.l5.config import USER_AGENT


class FetchError(RuntimeError):
    """Raised when an endpoint cannot be read after all retries."""


def _encode(params: dict[str, Any] | None) -> str:
    """Encode params, repeating a key once per value for list-valued params."""
    if not params:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            for item in value:
                pairs.append((key, str(item)))
        elif isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        else:
            pairs.append((key, str(value)))
    return urllib.parse.urlencode(pairs)


@dataclass
class HttpJson:
    timeout: float = 30.0
    max_retries: int = 4
    backoff_base_s: float = 2.0
    min_interval_s: float = 0.12  # be polite to a public endpoint
    _last_call: float = field(default=0.0, repr=False)

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        query = _encode(params)
        full = f"{url}?{query}" if query else url

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            gap = self.min_interval_s - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            request = urllib.request.Request(
                full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._last_call = time.monotonic()
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                self._last_call = time.monotonic()
                last_err = exc
                # 4xx other than rate limiting will not fix themselves.
                if exc.code not in (408, 425, 429) and exc.code < 500:
                    raise FetchError(f"GET {full} -> HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                self._last_call = time.monotonic()
                last_err = exc
            if attempt < self.max_retries:
                time.sleep(self.backoff_base_s * (2**attempt))
        raise FetchError(f"GET {full} failed after {self.max_retries + 1} attempts: {last_err}")


class RawCache:
    """Append-only JSONL cache of raw API pages, one line per response.

    Each line is an envelope -- endpoint, params, fetch time, and the verbatim
    decoded payload -- so a later run can rebuild every derived artifact
    without touching the network.
    """

    def __init__(self, directory: str) -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.directory, name)

    def append(self, name: str, endpoint: str, params: dict[str, Any], payload: Any) -> None:
        envelope = {
            "endpoint": endpoint,
            "params": {k: v for k, v in (params or {}).items() if v is not None},
            "fetched_at": int(time.time()),
            "payload": payload,
        }
        with open(self.path(name), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, separators=(",", ":"), sort_keys=True) + "\n")

    def truncate(self, name: str) -> None:
        open(self.path(name), "w", encoding="utf-8").close()

    def exists(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    def read_envelopes(self, name: str) -> Iterable[dict]:
        path = self.path(name)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError as exc:
                    raise FetchError(f"{path}:{line_no} is not valid JSON: {exc}") from exc

    def read_records(self, name: str) -> list[dict]:
        """Flatten every cached page into a single record list."""
        records: list[dict] = []
        for envelope in self.read_envelopes(name):
            payload = envelope.get("payload")
            for record in _iter_records(payload):
                records.append(record)
        return records


def _iter_records(payload: Any) -> Iterable[dict]:
    """Yield record dicts from a payload that may be a list or a wrapper."""
    if payload is None:
        return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(payload, dict):
        for key in ("data", "results", "events", "markets", "positions", "trades", "activity"):
            inner = payload.get(key)
            if isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict):
                        yield item
                return
        yield payload
