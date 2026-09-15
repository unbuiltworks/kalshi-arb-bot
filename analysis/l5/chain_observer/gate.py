"""Report the PREREGISTERED chain-observer promotion gate. No moving goalposts.

Every threshold here is copied from PROMOTION_GATE_PREREGISTERED.md, which was
written before the observations existed. This module only MEASURES; it never
decides a threshold, and a change to one of these constants must be a change to
that document first.

Promotion requires G-C1 … G-C8 to pass SIMULTANEOUSLY in one report over one
continuous qualifying sample. No partial credit.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from src.l5_chain_observer import classify as CL
from src.l5_chain_observer import finality as FN

# --- preregistered thresholds. Do not tune. See the .md.
MIN_JOINED = 500                 # G-C1
MIN_PRICE_AGREE = 0.995          # G-C4
MIN_SIZE_AGREE = 0.995           # G-C4
CONFIRMATIONS = 64               # G-C5
QUALIFYING_FROM_COMMIT = "47cb38e0"   # the block-boundary fix
# G-C1 requires the sample to be measured by code at or after that fix. A
# commit hash cannot be compared without git, which the deployed image may not
# carry, so the enforceable form is the validator's EPOCH -- stamped on every
# row it writes, and bumped whenever a change makes older rows non-comparable.
# ROWS BELOW THIS EPOCH ARE DISCARDED, not down-weighted: rows from before legs
# were grouped by role carried "240 @ 0.35" for a fill the venue reported as
# "80 @ 0.05", and counting them would have failed G-C4 on superseded code.
QUALIFYING_EPOCH = 3


def _load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def _qualifying(rows: List[dict]) -> List[dict]:
    """Only rows measured at or after the qualifying epoch. An unstamped row
    predates the stamp, so it is excluded rather than assumed current."""
    return [r for r in rows if int(r.get("epoch") or 0) >= QUALIFYING_EPOCH]


def report(state_root: str) -> Dict[str, Any]:
    vdir = os.path.join(state_root, "chainval")
    all_runs = _load(os.path.join(vdir, "validations.jsonl"))
    all_attr = _load(os.path.join(vdir, "attribution.jsonl"))
    runs = [r for r in _qualifying(all_runs) if r.get("ok")]
    attr = _qualifying(all_attr)
    cov = _coverage(state_root)
    excluded = {"runs": len(all_runs) - len(_qualifying(all_runs)),
                "attribution_rows": len(all_attr) - len(attr),
                "qualifying_epoch": QUALIFYING_EPOCH,
                "why": ("measured by superseded code; see EPOCH in "
                        "validate.py")}

    # G-C7 is not a report, it is a FILTER. A run whose compared window
    # overlaps any supervisor downtime is dropped before anything is counted:
    # evidence gathered while we were not watching is not evidence.
    cov_rows = _coverage_rows(state_root)
    observed, unobserved = _split_observed(runs, cov_rows)
    runs = observed
    joined = sum(int(r.get("agree") or 0) for r in runs)
    feed_only = [t for r in runs for t in (r.get("feed_only") or [])]
    chain_only = [t for r in runs for t in (r.get("chain_only") or [])]

    px = [a for a in attr if a.get("price_agrees") is not None]
    px_ok = sum(1 for a in px if a["price_agrees"])
    sz_ok = sum(1 for a in px if a["size_agrees"])
    unconfirmed = [a for a in attr
                   if (a.get("confirmations") or 0) < CONFIRMATIONS]
    no_prov = [a for a in attr if not (a.get("tx") and a.get("blocks")
                                       and a.get("log_indexes") is not None
                                       and a.get("contract"))]
    failed_runs = [r for r in _qualifying(all_runs) if not r.get("ok")]
    leaked = [r for r in failed_runs if "agreement_rate" in r]

    g: Dict[str, Any] = {}
    g["G-C1_sample_size"] = _g(
        joined >= MIN_JOINED, f"joined transactions >= {MIN_JOINED}",
        {"joined": joined, "threshold": MIN_JOINED, "runs": len(runs),
         "qualifying_from_commit": QUALIFYING_FROM_COMMIT,
         "qualifying_epoch": QUALIFYING_EPOCH,
         "code_versions": sorted({r.get("code") for r in runs
                                  if r.get("code")})})
    g["G-C2_no_unexplained_misses"] = _g(
        not feed_only, "feed_only == 0 unexplained",
        {"feed_only": len(feed_only), "examples": feed_only[:5]})
    cls = _qualifying(_load(
        os.path.join(vdir, "chain_only_classified.jsonl")))
    cls_sum = CL.summarise(cls)
    # every chain_only transaction the runs reported must have a row, not just
    # the ones that happened to be classified
    unaccounted = sorted(set(chain_only) - {r.get("tx") for r in cls})
    # Three ways this fails, and each is a real failure rather than a tally:
    # a chain_only transaction with NO row (the classifier did not run on it),
    # a row that came back `unresolved` (the chain reports a fill the venue
    # does not, which is the dangerous case), and a category outside the
    # preregistered list (a category invented after the fact).
    g["G-C3_chain_only_classified"] = _g(
        (not unaccounted and cls_sum["unresolved"] == 0
         and cls_sum["outside_permitted_categories"] == 0),
        "every chain_only case classified, unresolved == 0",
        {"chain_only": len(chain_only),
         "classified": cls_sum["classified"],
         "unaccounted": len(unaccounted),
         "unaccounted_examples": unaccounted[:5],
         "unresolved": cls_sum["unresolved"],
         "outside_permitted_categories":
             cls_sum["outside_permitted_categories"],
         "by_category": cls_sum["by_category"],
         "exercised": bool(cls),
         "permitted_categories": list(CL.CATEGORIES),
         "note": ("a category is not an explanation unless it is evidenced: "
                  "publication_lag and window_edge both require the "
                  "transaction to be FOUND in the feed. `exercised: false` "
                  "means no chain_only case arose, so this gate is satisfied "
                  "vacuously and the classifier is untested on live data")})
    g["G-C4_attribution"] = _g(
        bool(px) and (px_ok / len(px)) >= MIN_PRICE_AGREE
        and (sz_ok / len(px)) >= MIN_SIZE_AGREE
        and px_ok == len(px) and sz_ok == len(px),
        f"price and size agreement >= {MIN_PRICE_AGREE}, 0 unexplained",
        {"attributed": len(px),
         "price_agree": px_ok, "size_agree": sz_ok,
         "price_rate": (round(px_ok / len(px), 4) if px else None),
         "size_rate": (round(sz_ok / len(px), 4) if px else None),
         "unexplained_mismatches": len(px) - min(px_ok, sz_ok),
         "matched_group": dict(Counter(
             json.dumps(a.get("matched_group"), sort_keys=True)
             for a in px if a.get("matched_group")))})
    reorg = _reorg_tested()
    fin = _qualifying(_load(os.path.join(vdir, "finality.jsonl")))
    retracted = {e["tx"] for e in fin if e.get("kind") == "chain_retraction"}
    counted_but_retracted = sorted(retracted & {a.get("tx") for a in attr})
    g["G-C5_finality"] = _g(
        (not unconfirmed and reorg["passed"]
         and not counted_but_retracted),
        f"0 counted transactions below {CONFIRMATIONS} confirmations, "
        f"reorg handling tested",
        {"below_confirmations": len(unconfirmed),
         "confirmations_required": CONFIRMATIONS,
         "reorg_simulation": reorg,
         "retraction_events": len(retracted),
         "reblock_events": sum(1 for e in fin
                               if e.get("kind") == "chain_reblocked"),
         "counted_but_retracted": counted_but_retracted[:5],
         "note": ("the simulation is RUN when this report is produced, not "
                  "trusted: a block range re-scanned to different content "
                  "must produce a chain_retraction and remove the fill from "
                  "countable evidence")})
    g["G-C6_rpc_failure_is_unknown"] = _g(
        not leaked,
        "0 failed passes carrying agreement figures",
        {"failed_passes": len(failed_runs), "leaked_figures": len(leaked)})
    g["G-C7_coverage"] = _g(
        cov.get("available") is True and not cov.get("still_down"),
        "counted evidence excludes unobserved intervals",
        {**cov,
         "runs_counted": len(runs),
         "runs_dropped_as_unobserved": len(unobserved),
         "dropped_windows": [r.get("window_utc") for r in unobserved[:5]],
         "joined_excluded": sum(int(r.get("agree") or 0)
                                for r in unobserved)})
    g["G-C8_provenance"] = _g(
        bool(attr) and not no_prov,
        "100% of attributed transactions retain tx/block/logIndex/contract",
        {"attributed": len(attr), "missing_provenance": len(no_prov)})

    passed = [k for k, v in g.items() if v["pass"] is True]
    return {
        "preregistered": "analysis/l5/chain_observer/PROMOTION_GATE_PREREGISTERED.md",
        "gates_passing": len(passed), "gates_total": len(g),
        "PROMOTE": len(passed) == len(g),
        "blocking": [k for k, v in g.items() if v["pass"] is not True],
        "gates": g,
        "excluded_as_pre_qualifying": excluded,
        "labels": ("promotion requires ALL gates in ONE report over ONE "
                   "continuous qualifying sample. Passing some is not partial "
                   "progress toward promotion, it is not promotion."),
    }


def _reorg_tested() -> Dict[str, Any]:
    """Run the reorg simulation now. A gate that trusts a boolean about code
    it never executed is not measuring anything."""
    try:
        return FN.self_test()
    except Exception as exc:                                  # noqa: BLE001
        return {"passed": False, "error": f"{type(exc).__name__}: {exc}"[:160]}


def _coverage_rows(state_root: str) -> List[dict]:
    try:
        from analysis.l5 import coverage as C
        return C.load(state_root)
    except Exception:                                         # noqa: BLE001
        return []


def _split_observed(runs: List[dict], cov_rows: List[dict]):
    """(counted, dropped). With no markers at all nothing is counted: unknown
    coverage is not good coverage, and G-C7 fails on its own besides."""
    if not cov_rows:
        return [], list(runs)
    try:
        from analysis.l5 import coverage as C
    except Exception:                                         # noqa: BLE001
        return [], list(runs)
    keep, drop = [], []
    for r in runs:
        w = r.get("window_utc") or []
        if len(w) != 2:
            drop.append(r)
            continue
        (keep if C.observed(cov_rows, "chain_validate",
                            float(w[0]), float(w[1])) else drop).append(r)
    return keep, drop


def _coverage(state_root: str) -> Dict[str, Any]:
    try:
        from analysis.l5 import coverage as C
        rep = C.report(state_root)
    except Exception as exc:                                  # noqa: BLE001
        return {"available": False, "reason": str(exc)[:120]}
    if not rep.get("markers"):
        return {"available": False, "reason": rep.get("verdict")}
    cv = (rep.get("per_stream") or {}).get("chain_validate") or {}
    return {"available": True, "coverage_fraction": cv.get("coverage_fraction"),
            "downtime_s": cv.get("downtime_s"), "gaps": cv.get("gaps"),
            "still_down": cv.get("still_down")}


def _g(ok: Optional[bool], rule: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {"pass": ok, "rule": rule, "evidence": evidence}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", default="/state/l5-research")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.state_root), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
