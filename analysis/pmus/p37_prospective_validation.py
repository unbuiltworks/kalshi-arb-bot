"""P37 -- prospective out-of-sample validation of the FROZEN faithful rule.

The five development windows are CLOSED and are refused by name here, so they
cannot leak back in and inflate the prospective sample. Everything else is
scored by the unchanged eleven-point report at commit 030d8bf.

THREE THINGS ARE REPORTED SEPARATELY AND NEVER SELECTED BETWEEN:

    L5_NATIVE                what L5 actually did
    PMUS_TRANSLATION         venue-only: what PMUS could execute
    SAFETY_OVERLAY           what our hard controls would additionally refuse

Nothing here picks a configuration by P&L. Per-window results are retained in
an append-only ledger alongside the aggregate, because an average is exactly
where a bad window hides.

A window is scored only when it is COMPLETE: the market has expired with a
settlement value, and both the observer rows and the authenticated trade tape
for it are present. Anything else is reported as PENDING with the reason,
never scored partially.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from analysis.pmus import p34_amplitude_report as P34        # noqa: E402
from analysis.pmus import p35_translation_tracks as P35      # noqa: E402
from src import l5_execution_translations as X               # noqa: E402
from src import l5_faithful_translation as F                 # noqa: E402
from src import l5_fidelity_report as R                      # noqa: E402

OBS = "data/l5_pmus_realtime/l5_pmus_realtime.jsonl"
LEDGER = "analysis/pmus/evidence/prospective_ledger.jsonl"
FREEZE_COMMIT = "030d8bf"

#: CLOSED development sample -- produced the rule, so never out-of-sample
DEV_SAMPLE = {"2026-09-23T16:15", "2026-09-23T16:30", "2026-09-23T16:45",
              "2026-09-23T17:30", "2026-09-23T19:30"}

#: The first window that opened AFTER the rule was frozen at 030d8bf
#: (2026-09-23T20:16Z). A window that closed BEFORE the freeze is a holdout,
#: not prospective evidence: its data existed while the rule was being
#: specified, even if it was not used. Holdouts are scored and reported, and
#: never pooled into the prospective aggregate, because calling a
#: backward-looking window "prospective" is exactly the inflation this
#: separation exists to prevent.
PROSPECTIVE_FROM = "2026-09-23T20:30"


def window_id(row: Dict[str, Any]) -> str:
    return str(row.get("window_start_utc") or "")[:16]


def load_ledger() -> List[dict]:
    p = pathlib.Path(LEDGER)
    if not p.exists():
        return []
    out = []
    for line in p.open():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--append", action="store_true",
                    help="append newly scored windows to the ledger")
    a = ap.parse_args(argv)

    rows = []
    for line in open(a.obs):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "l5_action" and not r.get("backlog"):
            rows.append(r)

    auth = P34.authenticated_map(rows)
    lam = X.lambda_ref(rows)
    sett = P35.settlements()
    already = {e["window"] for e in load_ledger()}

    by_w = collections.defaultdict(list)
    for r in rows:
        by_w[window_id(r)].append(r)

    VENUE = F.SafetyLimits(enabled=False)
    OVERLAY = F.SafetyLimits()
    scored, holdout, pending, new_entries = [], [], [], []

    for w, rs in sorted(by_w.items()):
        if w in DEV_SAMPLE:
            continue                       # closed: never out-of-sample
        kind = ("PROSPECTIVE_OUT_OF_SAMPLE" if w >= PROSPECTIVE_FROM
                else "PRE_FREEZE_HOLDOUT")
        hhmm = w[11:16]
        yv = sett.get(hhmm)
        if yv is None:
            pending.append((w, len(rs), "NOT_SETTLED_OR_NO_TAPE"))
            continue
        if not any(r.get("action_key") in auth for r in rs):
            pending.append((w, len(rs), "NO_AUTHENTICATED_TAPE"))
            continue

        venue = F.run_window(rs, auth, lam=lam, limits=VENUE)
        full = F.run_window(rs, auth, lam=lam, limits=OVERLAY)
        rv = R.report(venue, yes_value=yv, lam=lam)
        rf = R.report(full, yes_value=yv, lam=lam)
        entry = {
            "window": w, "freeze_commit": FREEZE_COMMIT,
            "sample": kind,
            "settlement_yes_value": str(yv),
            "L5_NATIVE": {
                "actions": rv["action_reproduction"]["l5_actions"],
                "first_side": rv["sequence_agreement"]["native_first_side"],
                "longest_run": rv["sequence_agreement"]["native_longest_run"],
                "crossed_zero": rv["zero_cross_overshoot"]["native_crossed"],
                "overshoot": rv["zero_cross_overshoot"]["native_overshoot"],
                "final_imbalance":
                    rv["inventory_path_agreement"]["native_final_imbalance"],
            },
            "PMUS_TRANSLATION_venue_only": rv,
            "SAFETY_OVERLAY_applied": rf,
            "labels": ("SHADOW. No order exists. Nothing is submitted, "
                       "filled, settled or realized."),
        }
        (scored if kind == "PROSPECTIVE_OUT_OF_SAMPLE"
         else holdout).append(entry)
        if w not in already:
            new_entries.append(entry)

    print("FROZEN RULE %s -- prospective out-of-sample validation" % FREEZE_COMMIT)
    print("development sample CLOSED and excluded: %s\n"
          % ", ".join(sorted(DEV_SAMPLE)))

    if not scored:
        print("no PROSPECTIVE window is complete yet (freeze at %s, first "
              "prospective window %s)." % (FREEZE_COMMIT, PROSPECTIVE_FROM))
    if holdout:
        print("%d PRE-FREEZE HOLDOUT window(s) scored below and reported "
              "SEPARATELY -- never pooled into the prospective aggregate.\n"
              % len(holdout))
    for e in scored + holdout:
        v = e["PMUS_TRANSLATION_venue_only"]
        z = v["zero_cross_overshoot"]
        print("== %s  [%s]  settle %s"
              % (e["window"], e["sample"], e["settlement_yes_value"]))
        print("   L5_NATIVE   actions %3d  run %2d  xzero %-5s  overshoot %7.2f"
              % (e["L5_NATIVE"]["actions"], e["L5_NATIVE"]["longest_run"],
                 e["L5_NATIVE"]["crossed_zero"], e["L5_NATIVE"]["overshoot"]))
        print("   VENUE-ONLY  repro %3d/%3d (%5s%%)  mix %s  xzero %-5s %s  "
              "corr %s  slip %+.4f  fees %.4f  reb %.4f  TOTAL %+.4f"
              % (v["action_reproduction"]["reproduced"],
                 v["action_reproduction"]["l5_actions"],
                 v["action_reproduction"]["rate_pct"], v["maker_taker_mix"],
                 z["track_crossed"], z["direction"],
                 v["inventory_path_agreement"]["path_correlation"],
                 v["incremental_translation_cost"]["slippage_usd_vs_l5"],
                 v["fees_and_rebates"]["taker_fees_usd"],
                 v["fees_and_rebates"]["maker_rebate_accrual_usd"],
                 v["complete_window_economics"][
                     "total_settlement_implied_shadow_usd"]))
        print("   matched %8.2f  residual %9.2f (%s)  opposite %s"
              % (v["matched_vs_residual"]["track_matched"],
                 v["matched_vs_residual"]["track_residual"],
                 v["matched_vs_residual"]["track_residual_direction"],
                 v["opposite_acquisition"]["agrees"]))
        print("   irreducible %s" % v["irreducible_failures"]["detail"])
        o = e["SAFETY_OVERLAY_applied"]
        print("   OVERLAY     repro %3d/%3d  refused %d  TOTAL %+.4f"
              % (o["action_reproduction"]["reproduced"],
                 o["action_reproduction"]["l5_actions"],
                 o["irreducible_failures"]["by_layer"].get("SAFETY_OVERLAY", 0),
                 o["complete_window_economics"][
                     "total_settlement_implied_shadow_usd"]))

    if scored:
        n = len(scored)
        ra = sum(e["PMUS_TRANSLATION_venue_only"]["action_reproduction"]["reproduced"]
                 for e in scored)
        na = sum(e["PMUS_TRANSLATION_venue_only"]["action_reproduction"]["l5_actions"]
                 for e in scored)
        mk = sum(e["PMUS_TRANSLATION_venue_only"]["maker_taker_mix"].get("MAKER", 0)
                 for e in scored)
        tk = sum(e["PMUS_TRANSLATION_venue_only"]["maker_taker_mix"].get("TAKER", 0)
                 for e in scored)
        agree = sum(1 for e in scored
                    if e["PMUS_TRANSLATION_venue_only"][
                        "zero_cross_overshoot"]["crossing_agrees"])
        opp = sum(1 for e in scored
                  if e["PMUS_TRANSLATION_venue_only"][
                      "opposite_acquisition"]["agrees"])
        print("\n== PROSPECTIVE AGGREGATE over %d window(s)" % n)
        print("   reproduction %d/%d (%.1f%%)   maker/taker %d/%d (taker %.1f%%)"
              % (ra, na, 100.0 * ra / max(na, 1), mk, tk,
                 100.0 * tk / max(mk + tk, 1)))
        print("   zero-cross agreement %d/%d   opposite-acquisition agreement %d/%d"
              % (agree, n, opp, n))
        print("   DEVELOPMENT SAMPLE, for contrast only (not pooled): "
              "46.1%% reproduction, taker 91.6%%, xzero 3/5, opposite 5/5")

    for w, k, why in pending:
        print("   PENDING %s  (%d actions)  %s" % (w, k, why))

    if a.append and new_entries:
        p = pathlib.Path(LEDGER)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            for e in new_entries:
                fh.write(json.dumps(e, default=str) + "\n")
        print("\nappended %d new window(s) to %s" % (len(new_entries), LEDGER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
