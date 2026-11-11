"""P23 -- the spec-to-wire chain, proved end to end and proved to fail closed.

Probe B declared `placement="maker_improve"` and ran the driver's DEFAULT
placement, because neither `WC.Driver(...)` construction site passed the field.
Four live windows and one authorization were spent on an experiment that never
ran. Nothing in the tree noticed, because every individual piece was correct:
the spec declared it, the hash covered it, the driver had the parameter. The
DELIVERY between them was missing and unwitnessed.

This module is the witness. It walks the whole chain in one pass and asserts at
each seam, so the claim "B2 will actually test maker_improve" is checkable
rather than asserted:

    DECLARED -> HASHED -> INVOCATION -> DRIVER -> TRANSFORM -> SUBMITTED

and then proves the three refusals that matter: an undeclared placement, an
action_size that disagrees with its envelope, and a price that would cross.

Read-only. Constructs no transport, holds no credentials, sends nothing.
Exit status is non-zero if any seam fails, so it is usable as a gate.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.gemini_execution import ab_arms as AB            # noqa: E402
from src.gemini_execution import live_probe_spec as LPS   # noqa: E402

SPEC_ID = "btc5m_q1_improve"
FROZEN_SPEC_SHA = ("bfe7ba23199c544d30f25cd1c730d92e62d0"
                   "bea534274b1c00feff961242a650")

fails: List[str] = []


def check(label: str, ok: bool, detail: Any = "") -> None:
    print("  %-46s %s  %s" % (label, "OK  " if ok else "FAIL", detail))
    if not ok:
        fails.append(label)


def driver_call_sites() -> List[Dict[str, Any]]:
    """Every `WC.Driver(...)` in run_continuous.py, and whether it passes
    `placement`. Parsed from the AST, not grepped: a comment mentioning the
    keyword must not be able to satisfy this."""
    src = (ROOT / "src/gemini_execution/run_continuous.py").read_text()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = None
        if isinstance(f, ast.Attribute):
            name = f.attr
        elif isinstance(f, ast.Name):
            name = f.id
        if name != "Driver":
            continue
        out.append({
            "line": node.lineno,
            "passes_placement": any(k.arg == "placement" for k in node.keywords),
        })
    return out


def main() -> int:
    spec = LPS.get(SPEC_ID)

    print("\n1. DECLARED")
    check("spec_id", spec.spec_id == SPEC_ID, spec.spec_id)
    check("placement", spec.placement == "maker_improve", spec.placement)
    check("action_size agrees with envelope",
          str(spec.effective_action_size) == str(spec.envelope_obj.action_size),
          "%s == %s" % (spec.effective_action_size,
                        spec.envelope_obj.action_size))

    print("\n2. HASHED")
    check("spec_sha is the frozen one", spec.spec_sha() == FROZEN_SPEC_SHA,
          spec.spec_sha()[:16] + "...")
    fp = spec.invocation_fingerprint()
    check("placement is inside the fingerprint",
          any("placement" in str(k) for k in fp), sorted(fp)[:0] or "")
    import dataclasses
    swapped = dataclasses.replace(spec, placement=None)
    check("swapping placement MOVES spec_sha",
          swapped.spec_sha() != spec.spec_sha(),
          "%s -> %s" % (spec.spec_sha()[:8], swapped.spec_sha()[:8]))
    regrant = dataclasses.replace(spec, live_cohort="COHORT-SOMETHING-ELSE")
    check("repointing live_cohort does NOT move spec_sha",
          regrant.spec_sha() == spec.spec_sha(), regrant.spec_sha()[:8])

    print("\n3. INVOCATION")
    argv = spec.canonical_argv(out="o.json", wire="w.json")
    flags = [a for a in argv if a.startswith("--")]
    check("argv carries the executable fields",
          {"--family", "--envelope", "--entry-tte-band", "--cohort"} <= set(flags),
          " ".join(flags))
    for f in spec.ARGV_CANNOT_CARRY:
        check("argv CANNOT carry %r" % f,
              not any(f.replace("_", "-") in a for a in flags), "")

    print("\n4. DRIVER")
    sites = driver_call_sites()
    check("at least two Driver construction sites", len(sites) >= 2,
          [s["line"] for s in sites])
    check("EVERY Driver site passes placement",
          all(s["passes_placement"] for s in sites),
          [(s["line"], s["passes_placement"]) for s in sites])
    rc = (ROOT / "src/gemini_execution/run_continuous.py").read_text()
    check("delivery is verified OFF the constructed driver",
          rc.count('getattr(probe, "_placement"'
                   , ) + rc.count('getattr(driver, "_placement"') >= 2, "")
    check("a non-delivery has a refusal state",
          "REFUSED_PLACEMENT_NOT_DELIVERED" in rc, "")

    print("\n5. FAIL-CLOSED")
    try:
        dataclasses.replace(spec, action_size="7")
        check("action_size split brain is refused", False, "it was accepted")
    except Exception as e:                                     # noqa: BLE001
        check("action_size split brain is refused", True,
              type(e).__name__)
    try:
        dataclasses.replace(spec, placement="not_a_mode")
        check("an undeclared placement mode is refused", False,
              "it was accepted")
    except Exception as e:                                     # noqa: BLE001
        check("an undeclared placement mode is refused", True,
              type(e).__name__)

    print("\n6. TRANSFORM  (maker_only_improve)")
    book = {"yes": {"bid": "0.39", "ask": "0.45"}}
    got = AB.maker_only_improve(book, "YES", "0.39")
    check("one tick better when that stays maker", got == D("0.40"), got)
    acts = AB.apply_A([{"leg": "YES", "price": "0.39"}], book)
    check("apply_A stamps native_price and improved",
          acts[0].get("native_price") == "0.39"
          and acts[0].get("price") == "0.40"
          and acts[0].get("improved") is True,
          json.dumps(acts[0]))

    print("\n7. SUBMITTED  (what must never reach the wire)")
    tight = {"yes": {"bid": "0.39", "ask": "0.40"}}
    check("improving INTO the ask is refused (returns None)",
          AB.maker_only_improve(tight, "YES", "0.39") is None, "")
    at_ask = {"yes": {"bid": "0.44", "ask": "0.45"}}
    check("improving THROUGH the ask is refused",
          AB.maker_only_improve(at_ask, "YES", "0.44") is None, "")
    blind = {"yes": {"bid": "0.39", "ask": None}}
    check("an unreadable ask is NOT waved through",
          AB.maker_only_improve(blind, "YES", "0.39") is None, "")
    unimproved = AB.apply_A([{"leg": "YES", "price": "0.39"}], tight)
    check("a refused improvement leaves the native price untouched",
          unimproved[0]["price"] == "0.39"
          and unimproved[0]["improved"] is False,
          json.dumps(unimproved[0]))

    print("\n8. CLASSIFIED")
    m = spec.executable_field_map()
    hashed = set(fp)
    unclassified = sorted(
        k for k in (f.name for f in dataclasses.fields(spec))
        if k not in m)
    check("every declared field is classified", not unclassified, unclassified)
    check("hashed field set is non-empty", bool(hashed), len(hashed))

    print("\n%s  (%d seam(s) failed)"
          % ("CHAIN PROVEN" if not fails else "CHAIN BROKEN", len(fails)))
    for f in fails:
        print("   FAILED:", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
