"""Fail-closed entrypoint for the bounded Gemini combo q=1 FOK executor.

Two production problems are handled here without weakening trade gates:

1. A DISARMED deployment must not spend private Gemini nonces merely to prove that
   it is disarmed.  We load only the public active-combo catalog and exit cleanly.
2. Gemini's active-orders endpoint accepts an omitted symbol filter.  Querying it
   once and filtering locally avoids one private nonce per candidate/component.
   If the response is full at the 100-order cap, the scan fails closed rather than
   assuming there are no additional relevant orders.

When the exact campaign/arm/deadline gate is valid, this module delegates to
``gemini_combo_mm_fok_live_v1`` after installing the single-call active-order audit.
All execution, freshness, >=3c floor, q=1 FOK, recovery, and reconciliation gates
remain in the live module.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping

import httpx

import src.gemini_combo_mm_fok_live_v1 as live
from src.gemini_prediction_auth import private_rest_request

ACTIVE_ORDER_LIMIT = 100


def _arm_state() -> dict[str, Any]:
    campaign = os.getenv("GEMINI_COMBO_FOK_CAMPAIGN", "").strip()
    arm = os.getenv("GEMINI_COMBO_FOK_ARM_CAMPAIGN", "").strip()
    enabled = os.getenv("ENABLE_GEMINI_COMBO_FOK_LIVE", "").strip().upper() == "YES"
    deadline = live._parse_deadline(os.getenv("GEMINI_COMBO_FOK_ARM_NOT_AFTER_UTC", ""))
    deadline_ok = bool(deadline and datetime.now(timezone.utc) <= deadline)
    return {
        "campaign": campaign,
        "enabled": enabled,
        "arm_matches": bool(campaign and arm == campaign),
        "deadline_ok": deadline_ok,
        "armed": bool(enabled and campaign and arm == campaign and deadline_ok),
    }


async def _active_orders_for_symbols_single_call(symbols: set[str]) -> tuple[bool, list[dict[str, Any]]]:
    if not symbols:
        return True, []
    try:
        r = await private_rest_request(
            "POST",
            "/v1/prediction-markets/orders/active",
            params={"limit": ACTIVE_ORDER_LIMIT, "offset": 0},
            timeout=5.0,
        )
    except Exception:
        return False, []
    if r.status_code != 200:
        return False, []
    try:
        body = r.json()
    except Exception:
        return False, []
    if not isinstance(body, Mapping):
        return False, []
    raw_orders = body.get("orders") or []
    if not isinstance(raw_orders, list):
        return False, []
    # The endpoint caps a page at 100.  A full page is ambiguous because there may
    # be more account orders beyond the first page; fail closed instead of burning
    # more time-based nonces or silently missing a relevant order.
    if len(raw_orders) >= ACTIVE_ORDER_LIMIT:
        return False, []
    relevant: list[dict[str, Any]] = []
    for row in raw_orders:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or row.get("instrumentSymbol") or "")
        if symbol in symbols:
            relevant.append(dict(row))
    return True, relevant


async def run() -> dict[str, Any]:
    state = _arm_state()
    # Install the nonce-safe audit for both boot preflight and all final/recovery
    # reconciliations before delegating to the live module.
    live._active_orders_for_symbols = _active_orders_for_symbols_single_call

    if not state["armed"]:
        try:
            async with httpx.AsyncClient(timeout=12.0) as public_http:
                catalog, candidates = await live._load_candidates(public_http)
            public_ok = int(catalog.get("http") or 0) == 200
        except Exception as exc:
            catalog = {"http": 0, "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
            candidates = []
            public_ok = False
        out = {
            "status": "GEMINI_COMBO_MM_FOK_ENTRY_V1",
            **state,
            "catalog": catalog,
            "candidate_count": len(candidates),
            "private_preflight": "SKIPPED_DISARMED",
            "classification": "DISARMED_PUBLIC_CATALOG_READY" if public_ok else "DISARMED_PUBLIC_CATALOG_BLOCKED",
            "order_entry": False,
            "realized": False,
            "resting_combo_orders": False,
        }
        print("GEMINI_COMBO_MM_FOK_ENTRY_V1 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    return await live.run()


if __name__ == "__main__":
    asyncio.run(run())
