"""The BTC15m evaluation path, with settlement bridged and failing CLOSED.

READ-ONLY SHADOW. No transport, no signer, no order path.

NO SECOND ENGINE, AND THE FROZEN POLICY IS NOT TOUCHED.
-------------------------------------------------------
Every decision, threshold, gate and settlement decomposition below is the
frozen policy's own: this module imports `windows_from_tape` and
`shadow_window` from `btc15m_shadow_policy` and reimplements nothing. That
file's exact bytes are what `src/gemini_execution/ci15m_policy.fingerprint()`
hashes and what the owner's CI15M live-probe authorization is granted
against, so editing it would silently invalidate that authorization. It is
left byte-identical on purpose.

WHAT THIS FIXES
---------------
Two BTC15m tape producers write incompatible settlement rows:

    ie_collector                 {"resolution_side": "yes"|"no",
                                  "settlement_value": ..., "strike": ...}
    btc15m two-sided recorder    {"settlement": {"value": ...},
                                  "raw_event": {...}}        <- no side

`shadow_window` reads `settled["resolution_side"]` and, when it is absent,
emits no settlement block at all. The economic columns then print "-" and the
cohort totals 0 -- indistinguishable from "the policy made no money". On the
2026-10-06 recorder tape that silently suppressed 24 traded windows; bridged,
the same unchanged policy reports -6.41 (EXTEND) / -4.29 (NO_EXTEND).

The defect was never in the decomposition -- given a side, the frozen policy
computes it correctly. It was that nobody supplied one. So the bridge belongs
UPSTREAM, in window construction, and the policy needs no edit:

  * `windows_from_tape()` is called unchanged, then each window's `settled`
    is filled in from `settlement_bridge` (strike vs the venue's own
    settlement value; UP wins on `value >= strike`, ties to UP; derivation
    validated 58/58 against the venue's own `resolution_side` with zero
    disagreements), carrying its provenance;
  * a window the bridge cannot resolve is marked `INCOMPLETE:<reason>` and
    reported with EXPLICIT nulls -- never 0, never blank;
  * a cohort containing an unresolved window that TOOK A POSITION is declared
    INVALID and this module exits non-zero. An unresolved traded window has
    unknown economics, and a total that silently omits it is not a result.

Audited 2026-10-11; see `analysis/l5/btc15m_execution_audit/REPORT.md`.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_BRIDGE = _ROOT / "analysis" / "l5" / "btc15m_execution_audit"
if str(_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_BRIDGE))

import settlement_bridge as sb                                    # noqa: E402
from analysis.gemini.info_edge.btc15m_shadow_policy import (      # noqa: E402
    shadow_window, windows_from_tape)

INCOMPLETE_LABEL = (
    "INVALID FOR ECONOMICS. Settlement evidence is missing for this window, "
    "so no P&L is reported for it -- not 0, not blank. Fees are shown because "
    "they are known; nothing else is. This window must be EXCLUDED from every "
    "total, and a total that silently omits it is not a cohort result.")


def bridged_windows(tape_dir: str) -> Dict[str, Dict[str, Any]]:
    """The frozen replay's own windows, with settlement supplied explicitly."""
    ws = windows_from_tape(tape_dir)
    try:
        table = sb.table(tape_dir)
    except Exception as exc:                                     # noqa: BLE001
        raise SystemExit("settlement bridge failed on %s (%s). Refusing to "
                         "evaluate: an unbridged run reports 0 for every "
                         "unresolved window." % (tape_dir, exc))
    for tk, w in ws.items():
        raw = w.get("settled")
        venue_side = (raw or {}).get("resolution_side")
        b = table.get(tk) or {}
        if venue_side in ("yes", "no"):
            side, prov = venue_side, "VENUE_FIELD"
        else:
            side, prov = b.get("resolution_side"), (
                b.get("provenance") or "UNRESOLVED_NO_TAPE_EVIDENCE")
        if side in ("yes", "no"):
            w["settled"] = {"resolution_side": side, "provenance": prov,
                            "strike": b.get("strike"),
                            "settlement_value": b.get("settlement_value"),
                            "raw": raw}
            w["settlement_status"] = "RESOLVED:" + prov
        else:
            w["settled"] = None
            w["settlement_status"] = "INCOMPLETE:" + prov
    return ws


def evaluate(tape_dir: str) -> Dict[str, Any]:
    ws = bridged_windows(tape_dir)
    arms: Dict[str, List[Dict[str, Any]]] = {"EXTEND": [], "NO_EXTEND": []}
    incomplete: List[Dict[str, Any]] = []
    for tk, w in sorted(ws.items()):
        for allow, name in ((True, "EXTEND"), (False, "NO_EXTEND")):
            r = shadow_window(tk, w, allow_extend=allow)
            r["settlement_status"] = w["settlement_status"]
            if not r.get("settlement"):
                #: EXPLICIT, in place of the silence that was the defect.
                r["settlement"] = {
                    "status": w["settlement_status"], "outcome": None,
                    "matched_pair_pnl": None,
                    "directional_residual_pnl": None,
                    "net_settlement_pnl": None,
                    "fees": r["final"]["fees"],
                    "actions_taken": r["n_actions"],
                    "n_fills": r["final"]["n_fills"],
                    "labels": INCOMPLETE_LABEL}
                if name == "EXTEND":
                    incomplete.append({"ticker": tk,
                                       "status": w["settlement_status"],
                                       "actions": r["n_actions"],
                                       "n_fills": r["final"]["n_fills"]})
            arms[name].append(r)

    def total(name: str) -> Tuple[D, int]:
        t, n = D("0"), 0
        for r in arms[name]:
            v = (r.get("settlement") or {}).get("net_settlement_pnl")
            if v is not None:
                t += D(str(v))
                n += 1
        return t, n

    traded_unresolved = [i for i in incomplete if i["actions"] > 0]
    out: Dict[str, Any] = {
        "label": "BTC15M_SHADOW_EVAL_BRIDGED_V1",
        "accounting": ("SHADOW. No order was sent. Nothing is submitted, "
                       "filled, settled or realized."),
        "policy_file_unmodified": True,
        "tape_dir": tape_dir,
        "windows_with_book": len(ws),
        "windows_resolved": sum(1 for w in ws.values() if w.get("settled")),
        "windows_incomplete": len(incomplete),
        "incomplete": incomplete,
        "traded_but_unresolved": traded_unresolved,
        "cohort_valid": not traded_unresolved,
        "arms": {}, "per_window": arms,
    }
    for name in arms:
        t, n = total(name)
        out["arms"][name] = {
            "settled_windows": n,
            "net_settlement_pnl": str(t),
            "net_per_window": str((t / n).quantize(D("0.0001"))) if n else None,
            "fills": sum(r["final"]["n_fills"] for r in arms[name]),
            "matched_contracts": str(sum(D(r["final"]["matched_qty"])
                                         for r in arms[name])),
            "fees": str(sum(D(r["final"]["fees"]) for r in arms[name])),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape-dir", default="data/gemini_infoedge_15m")
    ap.add_argument("--out", default=None)
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="exit 0 even when a TRADED window has no settlement "
                         "evidence. Off by default, and turning it on does "
                         "not make the total sound -- it only stops this "
                         "process from saying so.")
    a = ap.parse_args()
    r = evaluate(a.tape_dir)
    print("=" * 96)
    print("BTC15m SHADOW EVALUATION -- frozen policy, settlement BRIDGED. "
          "NO ORDER IS SENT.")
    print("=" * 96)
    print("windows with a reconstructed book: %d | resolved: %d | "
          "INCOMPLETE: %d" % (r["windows_with_book"], r["windows_resolved"],
                              r["windows_incomplete"]))
    for i in r["incomplete"]:
        print("    INCOMPLETE %-20s actions=%d fills=%d  %s"
              % (i["ticker"], i["actions"], i["n_fills"], i["status"]))
    print()
    print("%-12s %8s %7s %9s %8s %10s %12s"
          % ("arm", "windows", "fills", "matched", "fees", "NET$", "per window"))
    for name, s in r["arms"].items():
        print("%-12s %8s %7s %9s %8s %10s %12s"
              % (name, s["settled_windows"], s["fills"],
                 s["matched_contracts"], s["fees"],
                 s["net_settlement_pnl"], s["net_per_window"]))
    print()
    if r["traded_but_unresolved"]:
        print("INVALID: %d window(s) took positions with NO settlement "
              "evidence. The totals above cover resolved windows only and "
              "are NOT a cohort result."
              % len(r["traded_but_unresolved"]))
        for i in r["traded_but_unresolved"]:
            print("    %-20s actions=%d  %s"
                  % (i["ticker"], i["actions"], i["status"]))
    else:
        print("SETTLEMENT COMPLETE: every window that took a position has "
              "settlement evidence. The totals above are the whole cohort.")
    if a.out:
        Path(a.out).write_text(json.dumps(r, indent=2, default=str))
        print("\nwrote %s" % a.out)
    return 1 if (r["traded_but_unresolved"] and not a.allow_incomplete) else 0


if __name__ == "__main__":
    sys.exit(main())
