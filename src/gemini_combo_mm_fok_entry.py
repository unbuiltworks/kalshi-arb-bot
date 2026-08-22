"""Fail-closed entrypoint for the bounded Gemini combo q=1 FOK executor.

A DISARMED deployment never spends private Gemini nonces merely to prove it is
disarmed: it loads only the public active-combo catalog and exits. When genuinely
armed, the entrypoint installs one unfiltered active-order account audit (filtered
locally) to avoid one time-based nonce per candidate/component. A full 100-order page
fails closed as ambiguous.

Armed execution delegates to ``gemini_combo_mm_fok_live_v2``. That executor uses the
minimal exact conjunction covers C(YES)+all complements and C(NO)+one canonical
required leg while preserving q1 FOK, >=3c after-fee, <=50ms final skew, exact depth,
capital/account, recovery, reconciliation, fresh campaign/token/deadline, and
FILLED_NOT_SETTLED classification gates. Resting combo orders remain forbidden.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping

import httpx

import src.gemini_combo_mm_fok_live_v2 as live
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
            "status": "GEMINI_COMBO_MM_FOK_ENTRY_V2_MINIMAL_COVER",
            **state,
            "catalog": catalog,
            "candidate_count": len(candidates),
            "private_preflight": "SKIPPED_DISARMED",
            "classification": "DISARMED_PUBLIC_CATALOG_READY" if public_ok else "DISARMED_PUBLIC_CATALOG_BLOCKED",
            "settlement_identities": ["C_YES+SUM(ALL_COMPLEMENTS)>=1", "C_NO+ANY_SINGLE_REQUIRED_LEG>=1"],
            "order_entry": False,
            "realized": False,
            "resting_combo_orders": False,
        }
        print("GEMINI_COMBO_MM_FOK_ENTRY_V2_MINIMAL_COVER " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    return await live.run()


if __name__ == "__main__":
    asyncio.run(run())
