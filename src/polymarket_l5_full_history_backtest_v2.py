"""Resilient wrapper for the l5 full-history Polymarket backtest.

Adds retry/backoff around public Data API activity reads so transient DNS,
connection resets, timeouts, 429s, and 5xx responses do not abort a months-long
read-only reconstruction. The underlying accounting logic remains in
polymarket_l5_full_history_backtest.py.
"""
from __future__ import annotations

import random
import time
from typing import Any

import httpx

from src import polymarket_l5_full_history_backtest as base


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def resilient_fetch_page(
    http: httpx.Client,
    *,
    kind: str,
    start: int,
    end: int,
    offset: int,
    limit: int = 500,
) -> list[dict[str, Any]]:
    params = {
        "user": base.WALLET,
        "type": kind,
        "start": int(start),
        "end": int(end),
        "limit": min(max(int(limit), 1), 500),
        "offset": max(0, int(offset)),
        "sortBy": "TIMESTAMP",
        "sortDirection": "ASC",
    }

    max_attempts = 14
    for attempt in range(1, max_attempts + 1):
        try:
            response = http.get(f"{base.DATA_API}/activity", params=params)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                retry_after = _retry_after_seconds(response)
                if retry_after is None:
                    retry_after = min(20.0, 0.6 * (2 ** min(attempt - 1, 5)))
                sleep_s = retry_after + random.uniform(0.05, 0.35)
                print(
                    f"L5_BACKTEST_HTTP_RETRY kind={kind} status={response.status_code} "
                    f"attempt={attempt}/{max_attempts} sleep={sleep_s:.2f}s",
                    flush=True,
                )
                time.sleep(sleep_s)
                continue
            response.raise_for_status()
            payload = response.json()
            return [x for x in payload if isinstance(x, dict)] if isinstance(payload, list) else []
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.PoolTimeout,
        ) as exc:
            if attempt >= max_attempts:
                raise
            sleep_s = min(20.0, 0.5 * (2 ** min(attempt - 1, 5))) + random.uniform(0.05, 0.35)
            print(
                f"L5_BACKTEST_NETWORK_RETRY kind={kind} error={type(exc).__name__} "
                f"attempt={attempt}/{max_attempts} sleep={sleep_s:.2f}s",
                flush=True,
            )
            time.sleep(sleep_s)

    raise RuntimeError(f"exhausted activity retries for {kind} {start}-{end} offset={offset}")


def main() -> None:
    base._fetch_page = resilient_fetch_page
    base.main()


if __name__ == "__main__":
    main()
