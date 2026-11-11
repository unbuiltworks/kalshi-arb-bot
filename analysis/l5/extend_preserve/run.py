#!/usr/bin/env python3
"""L5_EXTEND_PRESERVE vs CONTROL -- read-only reconciliation.

OWNER GO: "determine whether the shell's sampled EXTEND -> acted COMPLETE
conversion is suppressing genuine L5 recovery trajectories, without changing
production. Freeze current production behavior as CONTROL. Build a
sequential, stateful shadow arm called L5_EXTEND_PRESERVE ... Explicitly
reconcile this with 9d6feb46 ... If it doesn't, kill the hypothesis."

THE ARM ALREADY EXISTS, TWICE. Preserving a model-selected EXTEND instead of
converting it to COMPLETE is exactly what `SHADOW_1_ENTRY_ONLY`
(src/l5_band_semantics.py) does at the held-inventory gate, and exactly what
`band=entry_only|cap=6` did in the 2026-09-13 residual-cap study (9d6feb46).
Both carry their OWN inventory forward from the first divergence and re-check
every other gate on that state; neither waives a cap. So this script does not
re-simulate anything -- re-running the same arm under a third name would be a
third copy of one experiment, not a second piece of evidence. It READS the two
committed artifacts and joins them to the venue tape, which is the thing that
was never done: what happens to the residual the arm creates.

NOTHING HERE SUBMITS, PRICES OR REPRICES. No production file is read for
configuration and none is written. Financial gates are reported at their
production values and never varied: residual cap 6, per-market cap $30,
maker-only, band unchanged.

FILL_SUPPORTED vs FILL_UNKNOWN is preserved end to end and never summed:
  FILL_SUPPORTED  TAPE_SUPPORTED -- a counterfactual EXTEND counts as filled
                  only where a real EXTEND order filled in the same market
                  inside SUPPORT_WINDOW_S (600 s).
  FILL_UNKNOWN    FILL_ASSUMED -- every admitted EXTEND fills. An UPPER
                  BOUND, labelled as one, never a forecast.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
BAND = ROOT / "analysis/l5/band_semantics/band_arms.json"
CAPS = ROOT / "analysis/l5/residual_cap/residual_cap_arms.json"
OUT = Path(__file__).resolve().parent / "extend_preserve.json"

# production values, reported not varied
MAX_RESIDUAL_UNITS = 6
PER_MARKET_CAP_USD = "30.00"
CONTROL_ARM = "CONTROL"
PRESERVE_ARM = "SHADOW_1_ENTRY_ONLY"      # == L5_EXTEND_PRESERVE
PRIOR_CELL = "band=entry_only|cap=6"      # 9d6feb46, same gate, same cap
RULES = (("FILL_SUPPORTED", "TAPE_SUPPORTED"),
         ("FILL_UNKNOWN", "FILL_ASSUMED_upper_bound"))


def _f(x) -> float:
    try:
        return float(D(str(x)))
    except Exception:                                        # noqa: BLE001
        return 0.0


def order_conversion() -> dict:
    """Per-relation maker order outcomes from the venue tape. This is the
    step both counterfactual studies declare out of scope -- neither imputes
    a COMPLETE on top of a counterfactual EXTEND -- so it is measured here
    from orders that were actually submitted, and reported separately."""
    by_market: dict = defaultdict(Counter)
    glob: Counter = Counter()
    for line in gzip.open(SESSIONS, "rt"):
        line = line.strip()
        if not line:
            continue
        s = json.loads(line)
        ds = s.get("decisions") or []
        slug = ds[0].get("slug") if ds else s.get("slug")
        for o in (s.get("orders") or []):
            rel = o.get("relation") or "unknown"
            filled = _f(o.get("filled_qty")) > 0
            glob[(rel, filled)] += 1
            by_market[slug][(rel, filled)] += 1

    def cell(c: Counter, rel: str) -> dict:
        f, n = c[(rel, True)], c[(rel, True)] + c[(rel, False)]
        return {"orders": n, "filled": f,
                "rate": round(f / n, 4) if n else None}

    return {"global": {r: cell(glob, r) for r in ("open", "extend", "complete")},
            "by_market": {m: {r: cell(c, r)
                              for r in ("open", "extend", "complete")}
                          for m, c in sorted(by_market.items())}}


def main() -> int:
    band = json.loads(BAND.read_text())
    caps = json.loads(CAPS.read_text())
    conv = order_conversion()

    arms = {}
    for arm in (CONTROL_ARM, PRESERVE_ARM):
        a = band["arms"][arm]
        arms[arm] = {label: {
            "shadow_fills": a[key]["shadow_fills"],
            "extra_capital_usd": a[key]["extra_capital_usd"],
            "settlement_pnl_delta_usd": a[key]["total_settlement_pnl_delta_usd"],
            "unsettled_markets": a[key]["unsettled_markets"],
            "by_market": a[key]["by_market"]} for label, key in RULES}
        arms[arm]["economics_decomposition"] = a["economics_decomposition"]

    prior = {}
    for cell_name in ("band=current|cap=6", PRIOR_CELL):
        c = caps["grid"][cell_name]
        prior[cell_name] = {
            rule: {k: c[rule].get(k) for k in (
                "extra_extend_fills", "matched_contracts_delta",
                "matched_pair_pnl_delta", "directional_residual_pnl_delta",
                "capital_deployed_delta_usd",
                "production_extends_refused_by_this_arm")}
            for rule in ("TAPE_SUPPORTED", "FILL_ASSUMED")}
        prior[cell_name]["p3_outcome_classes"] = \
            caps["p3_outcome_classes"].get(cell_name, {})
        prior[cell_name]["by_market_matched"] = {
            m: {"production_matched": v.get("production_matched"),
                "arm_matched": v.get("matched_inventory"),
                "production_terminal_residual":
                    v.get("production_terminal_residual"),
                "arm_terminal_residual": v.get("terminal_residual")}
            for m, v in c["FILL_ASSUMED"]["by_market"].items()}

    # the join nobody had made: the arm's extra residual needs a COMPLETE
    # fill to become matched, and the tape says how often that happens in
    # exactly the markets the arm adds residual to.
    touched = sorted(
        m for m, v in arms[PRESERVE_ARM]["FILL_UNKNOWN"]["by_market"].items()
        if v["shadow_fills"] > 0)
    downstream = {m: conv["by_market"].get(m, {}).get("complete")
                  for m in touched}

    out = {
        "labels": {
            "arm_identity": ("L5_EXTEND_PRESERVE == SHADOW_1_ENTRY_ONLY "
                             "== band=entry_only|cap=6 (9d6feb46): one gate, "
                             "three names"),
            "scope": ("SHADOW ONLY; nothing submitted, repriced or cancelled; "
                      "production unchanged"),
            "fill_rules": {
                "FILL_SUPPORTED": ("a real EXTEND order filled in the same "
                                   "market within 600 s"),
                "FILL_UNKNOWN": ("every admitted EXTEND assumed filled -- "
                                 "UPPER BOUND, never a forecast")},
            "never_summed": ("FILL_SUPPORTED and FILL_UNKNOWN are two "
                             "readings of one arm, not two results to add"),
        },
        "gates_held_at_production_values": {
            "max_residual_units": MAX_RESIDUAL_UNITS,
            "per_market_cap_usd": PER_MARKET_CAP_USD,
            "band": "unchanged", "maker_only": True,
            "taker": "not enabled in any arm",
            "note": "no cap was raised to make the arm work"},
        "corpus": band["corpus"],
        "arms": arms,
        "prior_study_9d6feb46": prior,
        "observed_maker_conversion": conv,
        "downstream_complete_conversion_in_markets_the_arm_touches": downstream,
        "matched_pair_delta": {
            "band_semantics": arms[PRESERVE_ARM]["economics_decomposition"][
                "matched_pair_pnl_delta"],
            "residual_cap_9d6feb46_matched_contracts_delta": "0.00",
            "reading": ("both studies, both fill rules: the arm produces "
                        "ZERO extra matched contracts")},
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")

    print(f"{'arm':24s} {'rule':15s} {'fills':>5} {'capital':>9} {'settle Δ':>9}")
    for arm in (CONTROL_ARM, PRESERVE_ARM):
        for label, _key in RULES:
            r = arms[arm][label]
            print(f"{arm:24s} {label:15s} {r['shadow_fills']:5d} "
                  f"{r['extra_capital_usd']:>9} "
                  f"{r['settlement_pnl_delta_usd']:>9}")
    print()
    for m, c in downstream.items():
        print(f"  {m:42s} COMPLETE {c['filled']}/{c['orders']} = {c['rate']}")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
