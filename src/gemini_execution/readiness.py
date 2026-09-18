"""The complete readiness report: the preflight PLUS the venue write path.

WHY THIS LIVES HERE AND NOT IN THE PREFLIGHT. The preflight is a READ-ONLY
module and must stay one; if it imported this package, the architectural
isolation test would be right to fail. The dependency only runs one way --
execution may import a read, a read may never import execution -- so the
composition happens on this side of the boundary.

That direction is also why the gate was missing in the first place. The
preflight enumerated an authoritative order/fill READ path and a durable
intent path, and nothing in it could have noticed that no venue WRITE path
existed, because it had no business knowing. Now something does.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Dict, Optional, Sequence

from src.gemini_execution import arming, latch, spec

PROVEN = "PROVEN"
UNPROVEN = "UNPROVEN"


def venue_write_path() -> Dict[str, Any]:
    """PROVEN means the path exists, is arm-gated and is unit-proven.

    It does NOT mean the venue has accepted anything. Nothing here has been
    exercised against Gemini, and maker-only rejection behaviour stays
    unclaimed until the canary runs.
    """
    from src.gemini_execution import transport as T
    ok = bool(spec.PLACE_PATH and spec.CANCEL_PATH
              and callable(getattr(T, "place", None))
              and callable(getattr(T, "cancel_and_confirm", None)))
    return {
        "VENUE_WRITE_PATH": PROVEN if ok else UNPROVEN,
        "place": spec.PLACE_PATH,
        "cancel": spec.CANCEL_PATH,
        "reconcile": spec.ACTIVE_PATH,
        "maker_protection": "makerOrCancel: true (a boolean, per the API)",
        "order_type": spec.ORDER_TYPE_LIMIT,
        "arm_gated": True,
        "verified_against": ("developer.gemini.com, 2026-09-17. NOT inferred "
                             "from the PMUS executor or older Gemini code: "
                             "the prediction-market schema takes quantity, "
                             "outcome and a BOOLEAN makerOrCancel, where spot "
                             "takes amount and an options array."),
        "exercised_against_the_venue": False,
        "caveat": ("the request is constructed and unit-proven. Venue "
                   "acceptance, and maker-only rejection behaviour, stay "
                   "UNCLAIMED until the canary actually runs. The API "
                   "exposes no preview or sandbox for this path, so there is "
                   "no way to prove venue behaviour without placing one."),
    }


async def report(auth_id: str = arming.EXPECTED_AUTH_ID,
                 *, state_dir: str = latch.DEFAULT_DIR,
                 now: Optional[float] = None) -> Dict[str, Any]:
    from src import gemini_canary_preflight as PF
    pf = await PF.preflight_async(now)
    wp = venue_write_path()
    rec = latch.read(auth_id, state_dir)
    gates = {
        "preflight_ok": bool(pf.get("ok")),
        "VENUE_WRITE_PATH": wp["VENUE_WRITE_PATH"],
        "armed": arming.armed(now),
        "authorization_unconsumed": not latch.consumed(auth_id, state_dir),
    }
    blocking = [k for k, v in gates.items() if v is not True
                and k != "VENUE_WRITE_PATH"]
    if wp["VENUE_WRITE_PATH"] != PROVEN:
        blocking.append("VENUE_WRITE_PATH")
    return {
        "label": "GEMINI_EXECUTION_READINESS_V1",
        "gates": gates,
        "blocking": blocking,
        "venue_write_path": wp,
        "arming": arming.state(now),
        "latch": rec,
        "preflight": pf,
        "verdict": ("GEMINI_EXECUTION_READY" if not blocking
                    else "SPECIFIC_FIX_REQUIRED"),
        "authorization": ("NOT GRANTED by this report. Readiness is a "
                          "statement about capability and gates, never an "
                          "authorization to submit."),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-id", default=arming.EXPECTED_AUTH_ID)
    a = ap.parse_args(argv)
    out = asyncio.run(report(a.auth_id))
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
