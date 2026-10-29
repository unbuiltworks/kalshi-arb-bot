"""Evaluate ADVERSE_EXTEND_REFUSAL against the three deployed policies.

PRE-REGISTERED in analysis/l5/ADVERSE_EXTEND_PREREGISTRATION.md, committed
before this file produced a number. The rule, its lack of a magnitude
threshold, the 60/40 chronological split, the four metrics and the decision
criterion all come from that file and are not re-chosen here.

READ-ONLY. Imports the existing replay rather than reimplementing it, so
the candidate and the incumbents are measured by the same code on the same
observations.

WHAT THIS REPLAY'S MONEY NUMBERS ARE WORTH: not much, and the
pre-registration says so. It reconstructs a synthetic one-tick spread,
which has produced nets of 24-33% against L5's own realized 0.30%. So the
BEHAVIOURAL columns -- how often the rule fires, completion rates, residual
units -- are the output here, and expectancy is quoted only from the
production shadow records.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from decimal import Decimal as D

sys.path.insert(0, ".")
from analysis.l5 import policy_comparison as PC          # noqa: E402
from src import pmus_sports_l5_decision as l5d           # noqa: E402
from src import pmus_l5_persistence_v2 as pv2            # noqa: E402

#: fixed by the pre-registration, section 5
DEV_FRACTION = 0.60


# ---------------------------------------------------------------- the rule
def make_veto(diag: Counter, refusals: list):
    """The pre-registered rule, as a VETO on ARGMAX's extend.

    A veto, not a policy that returns None: `plan_action` treats an
    unrecognised relation as a QUOTE, so a declining policy would have
    quoted rather than refused.

    Tests the SIGN of wire - avg. No magnitude threshold, deliberately.
    """
    def veto(rel, prop, inv, book, tte):
        if rel != "extend" or inv.residual == 0:
            return False
        surplus_side_yes = inv.residual > 0
        avg = inv.avg(surplus_side_yes)
        if avg is None:
            return False
        wire = PC._touch(surplus_side_yes, book)
        if wire < avg:
            diag["refused"] += 1
            refusals.append({"side_yes": surplus_side_yes, "avg": avg,
                             "wire": wire, "tte": float(tte)})
            return True
        diag["allowed"] += 1
        return False
    return veto


def run_one(name, pol, windows, model, fill_mode, veto=None):
    r = PC.Run(name)
    PC.replay(windows, pol, r, model, fill_mode, veto=veto)
    return r


def completion_stats(windows, pol, model, fill_mode, veto=None):
    """Per-window completed pairs and the zero-completion share, which the
    Run aggregate cannot give because it sums across windows."""
    zero, pairs = 0, []
    for w in windows:
        r = PC.Run("one")
        PC.replay([w], pol, r, model, fill_mode, veto=veto)
        m = getattr(r, fill_mode)["matched"]
        pairs.append(m)
        zero += 1 if m == 0 else 0
    n = len(windows) or 1
    return {"windows": len(windows), "zero_completion_windows": zero,
            "zero_completion_share": round(zero / n, 3),
            "mean_pairs_per_window": round(sum(pairs) / n, 3)}


def recovery_audit(windows, model, fill_mode):
    """For every extend the rule refuses: did that side RECOVER before
    expiry? The cost side of the rule, reported as prominently as the
    benefit, as the pre-registration requires.

    Well-defined per refusal and needing no trajectory: at the refusal we
    know our average on that side, and the window's REMAINING prints say
    whether that side's own-outcome price ever came back above it. The
    refusal's absolute time is reconstructed from tte, which is what the
    policy layer is given.
    """
    prevented = recovered = 0
    prevented_cost = D(0)
    for w in windows:
        diag, refs = Counter(), []
        r = PC.Run("audit")
        PC.replay([w], PC.pol_argmax, r, model, fill_mode,
                  veto=make_veto(diag, refs))
        for ref in refs:
            t_ref = w["open"] + PC.WINDOW_S - ref["tte"]
            best = None
            for pr in w["prints"]:
                if pr["t"] <= t_ref:
                    continue                 # only what came AFTER
                px = (PC.floor_tick(pr["px"]) if ref["side_yes"]
                      else PC.floor_tick(D(1) - pr["px"]))
                best = px if best is None else max(best, px)
            if best is not None and best > ref["avg"]:
                recovered += 1
            else:
                prevented += 1
                prevented_cost += ref["wire"]
    tot = prevented + recovered
    return {"refusals": tot,
            "losses_prevented": prevented,
            "prevented_cost_usd": str(prevented_cost),
            "profitable_recoveries_missed": recovered,
            "recovery_share": (None if not tot
                               else round(recovered / tot, 3)),
            "missed_exceeds_prevented": recovered > prevented}


def main():
    model = pv2.load_v2()
    wins = PC.load()
    cut = int(len(wins) * DEV_FRACTION)
    splits = (("DEV (older %d%%)" % int(DEV_FRACTION * 100), wins[:cut]),
              ("HOLDOUT (newer %d%%)" % int(100 - DEV_FRACTION * 100),
               wins[cut:]))
    print(json.dumps({"windows": len(wins), "dev": cut,
                      "holdout": len(wins) - cut,
                      "model": model.get("_sha256_16"),
                      "pair_cap": str(l5d.BTC15M_PAIR_CAP),
                      "residual_cap": l5d.MAX_RESIDUAL_UNITS,
                      "split": "chronological, pre-registered 60/40",
                      "money_caveat": ("replay P&L is NOT expectancy; "
                                       "synthetic 1-tick spread")},
                     indent=1))
    for label, ws in splits:
        print("\n" + "=" * 70 + "\n" + label + "  n=%d" % len(ws))
        diag, refs = Counter(), []
        # a FACTORY, not one shared veto: `completion_stats` replays each
        # window separately, and a shared counter would tally the rule's
        # firings once per headline run and again once per window.
        rows = [("ADVERSE_EXTEND_REFUSAL", PC.pol_argmax,
                 lambda: make_veto(diag, refs))]
        rows += [(n, f, None) for n, f in PC.POLICIES]
        for name, pol, vf in rows:
            r = run_one(name, pol, ws, model, "cons",
                        veto=(vf() if vf else None))
            rep = r.report()
            c = rep["CONSERVATIVE"]
            print("  %-24s quotes=%-5d mix=%s" % (name, rep["quotes"],
                                                  rep["mix"]))
            print("     matched=%-4d resid_abs=%-4d windows_w_resid=%-4d "
                  "fills=%d"
                  % (c["matched_units"], c["residual_units_abs"],
                     c["windows_left_with_residual"], c["modelled_fills"]))
            # throwaway counters here; the headline run above owns `diag`
            print("     completion:", json.dumps(completion_stats(
                ws, pol, model, "cons",
                veto=(make_veto(Counter(), []) if vf else None))))
        print("  rule fired: refused=%d allowed=%d"
              % (diag["refused"], diag["allowed"]))
        print("  recovery audit:", json.dumps(
            recovery_audit(ws, model, "cons")))


if __name__ == "__main__":
    main()
