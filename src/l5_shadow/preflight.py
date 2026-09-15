"""G-P8: prove the money cannot vanish when the runtime changes operating state.

WHY THIS EXISTS, AND IT IS NOT HYPOTHETICAL. On 2026-09-14 the legacy PMUS lane
settled a real market for +$5.1425 and the accounting path did not record it
for THIRTY-ONE HOURS. Nothing failed loudly. The probe had simply moved into a
different operating state, and in that state it reported settlements nested
inside a `shutdown_idle` envelope instead of emitting them at the top level.
The reader selected records by the envelope's own `kind`, matched nothing, and
skipped them -- with its unreadable-record tally still reading ZERO, so the
omission looked exactly like an absence of settlements.

THE GENERAL DEFECT, which is what this asserts against:

    a terminal accounting record must be recoverable by WHAT IT IS,
    never by the runtime-state label of the envelope that happens to carry it.

THREE ASSERTIONS, all of which must hold before a live probe runs:

  A1  ENVELOPE INDEPENDENCE  the same settlement, wrapped in any operating
      state's envelope, yields the same economics.
  A2  SILENCE IS IMPOSSIBLE  a record the reader cannot understand increments
      a visible tally. This is the one that was violated: `unparsed` stayed 0
      while eight settlements disappeared.
  A3  ROUND TRIP IS LOSSLESS  filled -> settled -> realized preserves quantity
      and P&L exactly, with no float coercion.

AND A4, WHICH IS THE POINT OF PREREGISTERING IT. Every record kind the engine
can emit must be classified, in source, as TERMINAL or NON_TERMINAL. A kind
that is neither FAILS the preflight. A future operating state therefore cannot
slip through the same gap by being unknown to this file -- the preflight breaks
the moment someone adds one without saying which it is.

FAIL CLOSED. `verdict()` returns PASS only when every assertion holds. Anything
else -- including an error while checking -- is a refusal to launch.
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.l5_shadow import ledger as L

GATE = "G-P8/accounting_survives_operating_state"

# Records that carry money or its terminal status. Losing one is losing a fact
# about cash, so each must be recoverable from every envelope.
TERMINAL_KINDS = ("fill", "settlement", "settled", "realized")
# Records that carry no terminal accounting fact. Listed EXPLICITLY, because
# "not in the terminal list" must never be the same as "safe to drop".
NON_TERMINAL_KINDS = ("digest", "boot", "intent", "cycle_error")

# Envelope shapes an operating state may wrap a record in. The first is the
# ordinary one; the rest are the shapes that actually caused the loss.
ENVELOPES = (
    "top_level",            # {"kind": "settlement", ...}
    "nested_under_state",   # {"kind": "shutdown_idle", "settlement": {...}}
    "batched_under_state",  # {"kind": "...", "settlement": {"settled_now":[…]}}
)


def emitted_kinds(src_dir: str) -> List[str]:
    """Every `"kind": "..."` literal the engine can emit, read from SOURCE.

    Read from source rather than from a list kept by hand, so that adding a new
    record kind is visible to A4 whether or not anyone remembered this file.
    """
    found = set()
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            if not fn.endswith(".py") or fn == os.path.basename(__file__):
                continue
            try:
                txt = open(os.path.join(root, fn)).read()
            except OSError:
                continue
            found.update(re.findall(r'"kind":\s*"([a-z_]+)"', txt))
    return sorted(found)


def unwrap(rec: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(terminal records found, reasons a record could not be read).

    THE RULE THE LEGACY READER BROKE: selection is by what the record IS, not
    by the envelope's own kind. A settlement nested under any state key is
    still a settlement.
    """
    out: List[Dict[str, Any]] = []
    unreadable: List[str] = []
    if not isinstance(rec, dict):
        unreadable.append(f"not a record: {type(rec).__name__}")
        return out, unreadable

    def looks_terminal(d: Any) -> bool:
        return isinstance(d, dict) and str(d.get("kind") or "") in TERMINAL_KINDS

    if looks_terminal(rec):
        out.append(rec)
    for key, val in rec.items():
        if key == "kind":
            continue
        if looks_terminal(val):
            out.append(val)
        elif isinstance(val, dict):
            for sub in val.values():
                if looks_terminal(sub):
                    out.append(sub)
                elif isinstance(sub, list):
                    out.extend(x for x in sub if looks_terminal(x))
        elif isinstance(val, list):
            out.extend(x for x in val if looks_terminal(x))
    if not out:
        k = str(rec.get("kind") or "")
        if k not in NON_TERMINAL_KINDS:
            # THE WHOLE POINT. An envelope we do not recognise is reported, so
            # it can never read as "there were no settlements".
            unreadable.append(f"unrecognised envelope kind={k!r}")
    return out, unreadable


def wrap(rec: Dict[str, Any], envelope: str, *, state: str) -> Dict[str, Any]:
    """Put a terminal record inside one operating state's envelope."""
    if envelope == "top_level":
        return dict(rec)
    if envelope == "nested_under_state":
        return {"kind": state, "settlement": dict(rec)}
    if envelope == "batched_under_state":
        return {"kind": state, "settlement": {"settled_now": [dict(rec)]}}
    raise ValueError(f"unknown envelope {envelope!r}")


# --------------------------------------------------------------- assertions

def a1_envelope_independence(fills: Sequence[dict],
                             resolution: Dict[str, Any]) -> Dict[str, Any]:
    """The same fills, wrapped in every envelope, settle to the same number."""
    baseline = None
    per: Dict[str, Any] = {}
    for env in ENVELOPES:
        recovered: List[dict] = []
        for f in fills:
            got, _ = unwrap(wrap({**f, "kind": "fill"}, env,
                                 state="shutdown_idle"))
            recovered.extend(got)
        if len(recovered) != len(fills):
            per[env] = {"ok": False,
                        "why": f"recovered {len(recovered)} of {len(fills)} fills"}
            continue
        rows = [L.settle(p, resolution)
                for p in L.positions(recovered).values()]
        got = [r.get("realized_shadow") for r in rows]
        if baseline is None:
            baseline = got
        per[env] = {"ok": got == baseline, "realized_shadow": got}
    return {"assertion": "A1", "ok": all(v.get("ok") for v in per.values()),
            "per_envelope": per,
            "why": ("a settlement nested under an operating-state key is still "
                    "a settlement and must produce identical economics")}


def a2_silence_is_impossible() -> Dict[str, Any]:
    """An unreadable record must be COUNTED. Zero is the failure."""
    cases = [
        ({"kind": "some_future_state", "payload": {"x": 1}},
         "an operating state this file has never heard of"),
        ({"kind": "shutdown_idle", "settlement": {"settled_now": []}},
         "a state envelope carrying nothing"),
        ("not even a dict", "a line that is not a record at all"),
    ]
    rows = []
    for rec, why in cases:
        found, unreadable = unwrap(rec)  # type: ignore[arg-type]
        rows.append({"case": why, "terminal_found": len(found),
                     "tallied_unreadable": len(unreadable),
                     "ok": (len(found) > 0) or (len(unreadable) > 0)})
    return {"assertion": "A2", "ok": all(r["ok"] for r in rows), "cases": rows,
            "why": ("the legacy failure was an unreadable-record tally of 0 "
                    "while eight settlements were dropped; a drop must be "
                    "loud or it is indistinguishable from an absence")}


def a3_round_trip_is_lossless(fills: Sequence[dict],
                              resolution: Dict[str, Any]) -> Dict[str, Any]:
    """Quantity and P&L survive filled -> settled -> realized exactly."""
    rows = [L.settle(p, resolution) for p in L.positions(fills).values()]
    in_qty = sum(D(str(f["qty"])) for f in fills)
    out_qty = sum(D(r["up_qty"]) + D(r["dn_qty"]) for r in rows)
    recomputed = []
    for r in rows:
        if r.get("realized_shadow") is None:
            continue
        parts = (D(r["matched_pair_pnl"]) + D(r["residual_pnl"])
                 - D(r["fees_charged"]))
        recomputed.append(parts == D(r["realized_shadow"]))
    return {"assertion": "A3",
            "ok": in_qty == out_qty and all(recomputed) and bool(recomputed),
            "qty_in": str(in_qty), "qty_out": str(out_qty),
            "components_sum_to_realized": all(recomputed),
            "why": ("quantity is conserved and realized is exactly its own "
                    "three components; a float would not close this")}


def a4_every_kind_is_classified(src_dir: str) -> Dict[str, Any]:
    """A record kind that is neither TERMINAL nor NON_TERMINAL fails the gate.

    This is what stops the next operating state from repeating the last one.
    """
    known = set(TERMINAL_KINDS) | set(NON_TERMINAL_KINDS)
    kinds = emitted_kinds(src_dir)
    unclassified = [k for k in kinds if k not in known]
    return {"assertion": "A4", "ok": not unclassified,
            "kinds_found": kinds, "unclassified": unclassified,
            "why": ("a kind nobody classified is a kind the reader may drop; "
                    "adding one must break this gate, not pass silently")}


# ------------------------------------------------------------------ verdict

def _sample() -> Tuple[List[dict], Dict[str, Any]]:
    """One matched pair plus a residual, so all three P&L legs are exercised."""
    f = {"venue": "polymarket", "cohort": "BTC15m", "market_key": "m1",
         "clock": "venue", "fee": "0"}
    fills = [
        {**f, "outcome": "Up", "qty": "10", "price": "0.47", "t": 1.0,
         "intent_id": "i1"},
        {**f, "outcome": "Down", "qty": "10", "price": "0.49", "t": 2.0,
         "intent_id": "i2"},
        {**f, "outcome": "Up", "qty": "4", "price": "0.45", "t": 3.0,
         "intent_id": "i3"},
    ]
    return fills, {"resolved": True, "winning_outcome": "Up",
                   "resolution_source": "venue"}


def verdict(*, src_dir: str = "src/l5_shadow") -> Dict[str, Any]:
    """PASS or a refusal to launch. Anything that is not PASS blocks the probe."""
    fills, res = _sample()
    try:
        checks = [a1_envelope_independence(fills, res),
                  a2_silence_is_impossible(),
                  a3_round_trip_is_lossless(fills, res),
                  a4_every_kind_is_classified(src_dir)]
    except Exception as exc:                                  # noqa: BLE001
        # An error while CHECKING is not a pass. Fail closed.
        return {"gate": GATE, "verdict": "REFUSED",
                "error": f"{type(exc).__name__}: {exc}",
                "labels": "the preflight could not complete; no probe launches"}
    failed = [c["assertion"] for c in checks if not c["ok"]]
    return {
        "gate": GATE,
        "verdict": "PASS" if not failed else "REFUSED",
        "failed": failed,
        "checks": checks,
        "authorizes": ("nothing on its own. This is a PRECONDITION on the "
                       "accounting path, checked before a bounded BTC15m live "
                       "probe. G-P1..G-P7 still decide whether a probe is "
                       "proposed at all, and live authorization remains a "
                       "separate human decision."),
        "labels": ("asserts only that a terminal record survives a change of "
                   "operating state. It does not assert the numbers are "
                   "right, and it never converts shadow economics into "
                   "realized profit."),
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", default="src/l5_shadow")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    v = verdict(src_dir=a.src_dir)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as fh:
            json.dump(v, fh, indent=1, default=str)
    print(json.dumps(v, indent=1, default=str))
    return 0 if v["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
