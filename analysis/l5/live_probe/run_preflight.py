"""Run the COMPLETE preflight against the exact proposed V0 configuration.

Read-only. Places nothing, deploys nothing, arms nothing. Reports
READY / NOT_READY with every remaining blocker and the exact maximum loss.
"""
from __future__ import annotations

import json
import os
import subprocess
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src.l5_live_probe import envelope as ENV
from src.l5_live_probe import preflight as P
from src.l5_live_probe import probe_v0 as V


class _Intent:
    """The single largest action V0 can take: one minimum-size order at the
    dearest tradeable price."""
    incremental_qty = V.ORDER_QTY
    max_acceptable_price = str(V.VENUE_MAX_PRICE)


def live_market() -> Optional[Dict[str, Any]]:
    """A real BTC15m window in the shape MARKET_ELIGIBILITY consumes."""
    from src.l5_shadow import venues as VEN
    wins = [w for w in VEN.live_windows() if w.get("cohort") == "BTC15m"]
    if not wins:
        return None
    mk = wins[0]["market_key"]
    w = VEN.pm_window(mk)
    if not w:
        return None
    book = VEN.pm_book(w.get("token_up")) or {}
    bids, asks = book.get("bids") or [], book.get("asks") or []
    settle = None
    try:
        settle = VEN.pm_settlement(mk)
    except Exception:                                         # noqa: BLE001
        settle = None
    return {
        "market_key": mk,
        "open": bool(w.get("accepting_orders")),
        "best_bid": (bids[0].get("price") if bids else None),
        "best_ask": (asks[0].get("price") if asks else None),
        # resolvable = the settlement path answered at all, even "not yet"
        "resolvable": settle is not None,
        "order_min_size": w.get("order_min_size"),
        "tick": w.get("tick"),
        # carried so VENUE_ACCESS can attribute the row to a real venue rather
        # than trusting the `venue: "PMUS"` label, which says PMUS for
        # international markets too
        "terms_source": w.get("terms_source"),
    }


def connectivity():
    from src.l5_shadow import venues as VEN
    return bool(VEN.live_windows())


def run(*, set_envelope: bool = False) -> Dict[str, Any]:
    """set_envelope=True previews the verdict AS IF the owner had chosen the
    bounds. It is a preview only: it does not persist anything and arming is
    still an explicit act outside this process."""
    if set_envelope:
        for k, v in V.envelope_env().items():
            os.environ[k] = v
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    mkt = live_market()
    mins = {"qty": V.VENUE_ORDER_MIN}
    size = {"qty": V.ORDER_QTY}
    from src.l5_live_probe import venue_access as VA
    access = VA.tradeable_on_credentialed_venue("updown")
    ver = P.verdict(
        expected_sha=sha, connectivity=connectivity, market=mkt,
        size=size, minimums=mins, access=access,
        risk={"realized": "0", "floors": [],
              "current_inventory": "0", "market_spent": "0",
              "open_markets": 0, "last_heartbeat_age_s": 1.0,
              "intent": _Intent()},
    )
    return {
        "probe": V.LABEL, "status": V.STATUS,
        "envelope_previewed": set_envelope,
        "artifact_sha": sha,
        "market": mkt,
        "venue_access": access,
        "READY": "READY" if ver["ok"] else "NOT_READY",
        "max_loss": V.max_total_loss(),
        "blockers": ver["blockers"],
        "checks": [{"check": c["check"], "state": c["state"],
                    "blocker": c["blocker"]} for c in ver["checks"]],
    }


if __name__ == "__main__":
    import sys
    r = run(set_envelope="--preview-envelope" in sys.argv)
    print(json.dumps(r, indent=1, default=str))
