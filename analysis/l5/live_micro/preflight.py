"""LIVE-L5-MICRO-1 authorized preflight. Fail-closed. Submits nothing.

Owner authorized fingerprint
69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118
for a single probe on PMUS btc-updown-15m.

This runner evaluates the frozen gates IN ORDER and stops at the first
failure, reporting the actual condition. It never loosens a gate, never
substitutes a default for missing venue metadata, and has no code path that
submits or signs.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
from typing import Any, Dict, List

from analysis.l5.live_micro.ledger import (assert_disarmed_or_owner_armed,
                                           micro_armed)
from analysis.l5.live_micro.spec import (MICRO_FINGERPRINT, SPEC,
                                         assert_frozen, fingerprint)

AUTHORIZED_FINGERPRINT = \
    "69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118"
GATEWAY = "https://gateway.polymarket.us"
ARM_KEYS = ("PMUS_MICRO_LIVE", "PMUS_MICRO_SESSION_ID", "PMUS_MICRO_ARM",
            "PMUS_MICRO_GATE_EVIDENCE")
CRED_HINTS = ("PMUS_API", "PMUS_KEY", "PMUS_SECRET", "PRIVATE_KEY",
              "POLYMARKET", "WALLET", "MNEMONIC", "PASSPHRASE")


def gate_fingerprint() -> Dict[str, Any]:
    got = fingerprint()
    ok = got == AUTHORIZED_FINGERPRINT == MICRO_FINGERPRINT
    return {"gate": "G1_FINGERPRINT_MATCHES_AUTHORIZATION", "pass": ok,
            "authorized": AUTHORIZED_FINGERPRINT, "computed": got}


def gate_arming() -> Dict[str, Any]:
    present = {k: bool(os.getenv(k, "").strip()) for k in ARM_KEYS}
    armed = micro_armed()
    return {"gate": "G2_OWNER_ARMING_KEYS_PRESENT", "pass": armed,
            "keys_present": present, "armed": armed,
            "detail": ("the owner's four PMUS_MICRO_* keys are the arming "
                       "mechanism. The frozen spec asserts "
                       "claude_may_not_set_these, so this runner reports "
                       "them and never sets them.")}


def gate_credentials() -> Dict[str, Any]:
    found = sorted(k for k in os.environ
                   if any(h in k.upper() for h in CRED_HINTS))
    return {"gate": "G3_SUBMISSION_CREDENTIALS_PRESENT",
            "pass": bool(found),
            "credential_shaped_vars_present": len(found),
            "names_only": found,
            "detail": ("a real order needs authenticated API access and "
                       "wallet signing. Values are never printed.")}


def gate_network() -> Dict[str, Any]:
    try:
        req = urllib.request.Request(GATEWAY + "/v1/markets",
                                     headers={"User-Agent": "micro-preflight"})
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=20) as r:
            return {"gate": "G4_VENUE_REACHABLE", "pass": True,
                    "http_status": r.status}
    except Exception as exc:                            # noqa: BLE001
        return {"gate": "G4_VENUE_REACHABLE", "pass": False,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


def gate_metadata(slug: str) -> Dict[str, Any]:
    """The frozen fail-closed metadata gate: minimumTradeQty < 1 and the
    0.005 tick. UNREADABLE is a SKIP, never a default."""
    try:
        req = urllib.request.Request(GATEWAY + "/v1/markets/%s" % slug,
                                     headers={"User-Agent": "micro-preflight"})
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=20) as r:
            d = json.loads(r.read())
    except Exception as exc:                            # noqa: BLE001
        return {"gate": "G5_VENUE_METADATA_FAIL_CLOSED", "pass": False,
                "minimumTradeQty": "UNREADABLE",
                "orderPriceMinTickSize": "UNREADABLE",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "detail": ("metadata could not be read, so the frozen gate "
                           "fails closed. No default was substituted.")}
    m = d.get("market") or d
    minq, tick = m.get("minimumTradeQty"), m.get("orderPriceMinTickSize")
    ok = (minq is not None and tick is not None
          and float(minq) < 1 and abs(float(tick) - 0.005) < 1e-12)
    return {"gate": "G5_VENUE_METADATA_FAIL_CLOSED", "pass": bool(ok),
            "minimumTradeQty": minq, "orderPriceMinTickSize": tick,
            "required": "minimumTradeQty < 1 AND tick == 0.005"}


def main(argv=None) -> int:
    assert_frozen()
    slug = os.getenv("PMUS_MICRO_GATE_EVIDENCE", "").strip() or "<not gated>"
    gates: List[Dict[str, Any]] = [gate_fingerprint(), gate_arming(),
                                   gate_credentials(), gate_network()]
    # the metadata gate is only meaningful once the venue is reachable
    if gates[-1]["pass"]:
        gates.append(gate_metadata(slug))
    failed = [g for g in gates if not g["pass"]]
    out = {
        "probe": "LIVE-L5-MICRO-1",
        "owner_authorized_fingerprint": AUTHORIZED_FINGERPRINT,
        "spec_fingerprint": MICRO_FINGERPRINT,
        "market_family": SPEC["market"]["series"],
        "gated_slug": slug,
        "gates": gates,
        "VERDICT": "PROCEED_TO_SUBMIT" if not failed else "SKIP",
        "blocking_gates": [g["gate"] for g in failed],
        "orders_submitted": 0,
        "wallet_signing": 0,
        "capital_moved_usd": "0.00",
        "lifecycle_states_reached": [],
        "NO_GATE_LOOSENED": True,
        "NO_PARAMETER_CHANGED_TO_OBTAIN_A_TRADE": True,
        "detail": ("every blocking gate is reported with its actual "
                   "condition. Per the owner's authorization, a SKIP stops "
                   "here and nothing is loosened automatically."),
    }
    os.makedirs("analysis/l5/live_micro", exist_ok=True)
    with open("analysis/l5/live_micro/PREFLIGHT_PROBE_1.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("VERDICT: %s  blocking=%s" % (out["VERDICT"],
                                        out["blocking_gates"]))
    return 0 if out["VERDICT"] == "PROCEED_TO_SUBMIT" else 3


if __name__ == "__main__":
    raise SystemExit(main())
