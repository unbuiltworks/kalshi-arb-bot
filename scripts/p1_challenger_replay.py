"""P1 challenger replay -- the frozen n=25 arm is NOT touched.

THE CHALLENGER IS NOT A NEW RULE. Every difference it expresses already exists in
the frozen scorer as a parameter, and each is chosen because the L5 corpus
documents it, not because it improves the number:

  * VARIANT A instead of B. Variant B blocks same-side rebuild for `hold_s` after
    a completion; variant A re-engages immediately. In all five losing-state
    neighbourhoods retrieved from L5's 66,949 BTC15m states, his next action
    arrives in a MEDIAN 1-3 SECONDS and is a near coin-flip between EXTEND
    (48-55%) and COMPLETE (40-49%). He does not cool off. Our frozen arm waits.
  * SMALLER CLIP. L5's median fill is 10.5 contracts at a median notional of
    $3.99; his p90 notional is $28. Our q=50 clip is ~$25 -- 6x his median. His
    next fill in those neighbourhoods is a median 1.00 of his OWN typical size,
    so the comparable translation is a small clip repeated, not a large one.
    q=5 also sits INSIDE demonstrated live capacity (5.31 contracts filled), so
    it is the only rung here that is not a modeled extrapolation.

NOT DONE, deliberately: no theta change, no q increase, no directional predictor,
no invented stop, no settlement information in any decision. Same tape, same
windows, same queue/fill model, same fee schedule.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_completion_shadow as S                       # noqa: E402
from src import pmus_fees as F                                    # noqa: E402
from scripts.audit_economic_complete import load, USABLE, audit   # noqa: E402
from scripts.audit_completion_orderstate import walk              # noqa: E402

THETA, FILL = D("0.96"), "queue_aware"
OUT = "analysis/l5/p1/p1_challenger_replay.json"


def arm(by, clip, variant):
    """One arm over every frozen window. Returns the reported waterfall."""
    rows = []
    for slug, winner in USABLE.items():
        r = S.run_window(by[slug], clip=clip, theta=THETA, winner=winner,
                         variant=variant, fill_model=FILL)
        a = audit(by[slug], clip, THETA, variant, winner, emit=True,
                  fill_model=FILL)
        reb = D(0)
        for e in a["ledger"]:
            if e["action"].startswith("ACQ"):
                bp = e["acq_basis"]
                px = bp if e["side"] == "up" else (D(1) - bp)
                reb += F.maker_rebate(px, e["qty"], cents=True)
        ec = r["gross_margin"]
        rows.append({
            "window": slug[-5:],
            "ec_matched_gross": ec,
            "passive_matched_gross": r["matched_pnl"] - ec,
            "matched_gross": r["matched_pnl"],
            "fees": r["fees"],
            "residual_pnl": r["resid_pnl"],
            "shadow_trading_net": r["matched_pnl"] - r["fees"] + r["resid_pnl"],
            "expected_maker_rebate": reb,
            "peak_directional_exposure_contracts": r["peak_resid"],
            "terminal_residual": r["resid_qty"],
            "completions": r["completions"],
            "maker_qty": r["maker_qty"], "taker_qty": r["taker_qty"],
        })
    n = D(len(rows))
    g = lambda f: sum(x[f] for x in rows) / n
    net = g("shadow_trading_net")
    return {
        "clip": str(clip), "variant": variant, "theta": str(THETA),
        "fill_model": FILL, "n_windows": int(n),
        "per_window": {
            "ec_matched_gross": str(g("ec_matched_gross").quantize(D("0.0001"))),
            "passive_matched_gross": str(
                g("passive_matched_gross").quantize(D("0.0001"))),
            "matched_gross": str(g("matched_gross").quantize(D("0.0001"))),
            "fees": str(g("fees").quantize(D("0.0001"))),
            "residual_pnl": str(g("residual_pnl").quantize(D("0.0001"))),
            "shadow_trading_net": str(net.quantize(D("0.0001"))),
            "expected_maker_rebate": str(
                g("expected_maker_rebate").quantize(D("0.0001"))),
            "shadow_modeled_net_including_expected_rebate": str(
                (net + g("expected_maker_rebate")).quantize(D("0.0001"))),
        },
        "exposure": {
            "mean_peak_directional_contracts": str(
                g("peak_directional_exposure_contracts").quantize(D("0.01"))),
            "max_peak_directional_contracts": str(
                max(x["peak_directional_exposure_contracts"] for x in rows)),
            "mean_peak_in_clips": str(
                (g("peak_directional_exposure_contracts") / clip)
                .quantize(D("0.01"))),
            "windows_with_terminal_residual": sum(
                1 for x in rows if x["terminal_residual"] > 0),
        },
        "activity": {
            "mean_completions_per_window": str(
                g("completions").quantize(D("0.01"))),
            "mean_maker_contracts": str(g("maker_qty").quantize(D("0.01"))),
            "mean_taker_contracts": str(g("taker_qty").quantize(D("0.01"))),
        },
        "windows": [{k: str(v) for k, v in x.items()} for x in rows],
    }


def main(argv):
    by = load()
    arms = []
    for clip in (D(5), D(10), D(25), D(50)):
        for variant in ("B", "A"):
            arms.append(arm(by, clip, variant))
    doc = {
        "role": "P1 CHALLENGER -- diagnostic. The frozen n=25 arm is q in {25,50}"
                " variant B and is unchanged.",
        "frozen_arm": {"clip": "25 and 50", "variant": "B"},
        "no_settlement_in_decisions": True,
        "arms": arms,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    hdr = ("clip var |    EC  passive  matched    fees  resid    NET   reb"
           "   MODELED | peakC  clips  termR  compl")
    print(hdr)
    for a in arms:
        p, e, c = a["per_window"], a["exposure"], a["activity"]
        print("%4s %3s | %6s %8s %8s %7s %6s %6s %5s %9s | %5s %6s %6d %6s" % (
            a["clip"], a["variant"], p["ec_matched_gross"],
            p["passive_matched_gross"], p["matched_gross"], p["fees"],
            p["residual_pnl"], p["shadow_trading_net"],
            p["expected_maker_rebate"],
            p["shadow_modeled_net_including_expected_rebate"],
            e["mean_peak_directional_contracts"], e["mean_peak_in_clips"],
            e["windows_with_terminal_residual"],
            c["mean_completions_per_window"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
