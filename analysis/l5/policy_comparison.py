"""Compare decision policies for the BTC15m shadow controller, on L5's own
frozen history. READ-ONLY. REPLAY, never realized P&L.

WHY THIS EXISTS. The deployed controller consults the frozen v2 model by
ARGMAX. The model's mean probabilities across its on-distribution states
are extend 0.541 / complete 0.459, so the argmax turns a near-coin-flip
into a monotone extend run: each extend sets last_rel=extend, which keeps
extend as the argmax until the tte bucket itself changes. L5's own
consecutive-extend runs have median 2 and p99 18; ours is bounded only by
the bucket boundary. That is the defect under test.

WHAT IS NOT DONE HERE. No policy targets L5's 52/48 action ratio. Matching
a ratio is not a profit mechanism and forcing one would be fitting a
summary statistic. The ratio is used ONLY as a behavioural diagnostic --
reported beside the economics, never optimised toward.

NO FREE PARAMETERS. Every policy below is deterministic and uses only
constants already in the codebase (the 0.96 spring pair cap,
MAX_RESIDUAL_UNITS = 3). There is nothing here to tune on the evaluation
sample, which is the failure mode the COMPLETE re-quote campaign was
NO-GO'd for.

THE BOOK IS A PROXY AND IS LABELLED ONE. L5's history records its executed
prints, not the BBO. At each decision instant the reference price is L5's
own print price -- a real executed price at a real instant -- with the
maker join taken one tick inside it. This is a PROXY FOR THE TOUCH, not a
measured book, and it is the same proxy for every policy, so the
comparison between policies is fair even where the absolute level is not.

TWO FILL COLUMNS, NEVER SUMMED.
  CONSERVATIVE: our resting quote fills only when a LATER L5 print would
    have taken it out -- a print on the hitting side at or through our
    price, strictly after we quoted, inside the same window. L5's prints
    are a subset of all market prints, so this UNDERSTATES fills.
  OPTIMISTIC: the controller's own TOUCH_THROUGH_OPTIMISTIC rule, an
    acknowledged UPPER BOUND.
Neither is a measured fill rate. A touch is not a fill.

HOLDOUT. Windows are split by time at the median. Policies are defined and
frozen in this file before any number is read, and both halves are
reported, so a policy that only works on one is visible as such.
"""
from __future__ import annotations

import json
import statistics as stat
import sys
from collections import Counter
from decimal import Decimal as D

sys.path.insert(0, ".")

from src import pmus_l5_persistence_v2 as pv2                   # noqa: E402
from src import pmus_sports_l5_decision as l5d                  # noqa: E402
from src.polymarket_l5.action_model import _bucket, TTE_BUCKETS  # noqa: E402

WINDOW_S = 900.0
SEQ = "data/l5_master/btc15m_sequences.jsonl"
TICK = D("0.01")


def floor_tick(v: D) -> D:
    return (v / TICK).to_integral_value(rounding="ROUND_DOWN") * TICK


# ===================================================================
# THE POLICIES. Frozen before any result is read.
# ===================================================================

def pol_argmax(prop, inv, book, tte):
    """BASELINE -- exactly what is deployed. The model's argmax relation."""
    return prop.get("recommended_relation")


def pol_pair_available(prop, inv, book, tte):
    """COMPLETE WHENEVER THE PAIR CAN BE LOCKED, else extend.

    The only action that locks economics is a completion whose combined
    pair cost clears the cap; nothing else converts inventory into a
    determinate $1 redemption. So this asks the economic question directly
    -- is a fee-safe completion priceable from OUR average cost right now?
    -- and takes it when the answer is yes, whatever the model's argmax
    says. It never completes on a clock and never extends because a bucket
    boundary moved.
    """
    if inv.residual == 0:
        return "open"
    side_yes = inv.residual < 0                 # the DEFICIT side
    surplus_avg = inv.avg(not side_yes)
    if surplus_avg is None:
        return prop.get("recommended_relation")
    px, _ = l5d.complement_target(surplus_avg, _touch(side_yes, book),
                                  pair_cap=l5d.BTC15M_PAIR_CAP)
    return "complete" if px is not None else "extend"


def pol_residual_urgency(prop, inv, book, tte):
    """RISK-DRIVEN: never let inventory reach the cap, else follow the model.

    At |residual| == MAX_RESIDUAL_UNITS the controller stops acting
    entirely -- it can neither extend (capped) nor usefully open -- so the
    window ends with the largest possible one-sided position and the whole
    settlement is a coin flip. This completes one step before that, and
    otherwise defers to the model.
    """
    if inv.residual == 0:
        return "open"
    if abs(inv.residual) >= l5d.MAX_RESIDUAL_UNITS - 1:
        return "complete"
    return prop.get("recommended_relation")


def pol_pair_or_urgency(prop, inv, book, tte):
    """Both of the above: take a lockable pair, and never reach the cap."""
    if inv.residual and abs(inv.residual) >= l5d.MAX_RESIDUAL_UNITS - 1:
        return "complete"
    return pol_pair_available(prop, inv, book, tte)


POLICIES = (("ARGMAX_BASELINE", pol_argmax),
            ("PAIR_AVAILABLE", pol_pair_available),
            ("RESIDUAL_URGENCY", pol_residual_urgency),
            ("PAIR_OR_URGENCY", pol_pair_or_urgency))


def _touch(side_yes, book):
    return (floor_tick(book["bid"]) if side_yes
            else floor_tick(D(1) - book["ask"]))


# ===================================================================

class Run:
    """One policy's replay over one set of windows."""

    def __init__(self, name):
        self.name = name
        self.rel = Counter()
        self.refused = Counter()
        self.quotes = 0
        self.runs = []                 # consecutive-extend run lengths
        self.tte_at_action = []
        self.pair_costs = []
        for tag in ("cons", "opt"):
            setattr(self, tag, {"cost": D(0), "redeem": D(0), "matched": 0,
                                "resid_abs": 0, "fills": 0,
                                "windows_with_residual": 0})

    def record_settle(self, tag, inv, yes_value):
        b = getattr(self, tag)
        b["cost"] += inv.yes_cost + inv.no_cost
        b["matched"] += inv.matched
        b["resid_abs"] += abs(inv.residual)
        b["redeem"] += (D(inv.matched)
                        + D(abs(inv.residual))
                        * (yes_value if inv.residual > 0 else D(1) - yes_value))
        if inv.residual:
            b["windows_with_residual"] += 1

    def report(self):
        tot = sum(self.rel.values()) or 1
        e, k = self.rel["extend"], self.rel["complete"]
        out = {"policy": self.name, "decisions": tot,
               "quotes": self.quotes,
               "mix": {r: round(self.rel[r] / tot, 3)
                       for r in ("open", "extend", "complete", "none",
                                 "refused")},
               "top_refusals": dict(self.refused.most_common(3)),
               "extend_share_of_ext_cpl": (round(e / (e + k), 3)
                                           if (e + k) else None),
               "extend_run_median": (stat.median(self.runs) if self.runs
                                     else None),
               "extend_run_p90": (sorted(self.runs)[9 * len(self.runs) // 10]
                                  if self.runs else None),
               "extend_run_max": max(self.runs) if self.runs else None,
               "median_tte_at_action": (round(stat.median(self.tte_at_action))
                                        if self.tte_at_action else None),
               "median_pair_cost": (str(stat.median(self.pair_costs))
                                    if self.pair_costs else None)}
        for tag, label in (("cons", "CONSERVATIVE"), ("opt", "OPTIMISTIC")):
            b = getattr(self, tag)
            out[label] = {
                "modelled_fills": b["fills"],
                "matched_units": b["matched"],
                "residual_units_abs": b["resid_abs"],
                "windows_left_with_residual": b["windows_with_residual"],
                "cost_total": str(b["cost"]),
                "redemption_total": str(b["redeem"]),
                "modelled_net": str(b["redeem"] - b["cost"]),
            }
        return out


def replay(windows, policy_fn, run, model, fill_mode):
    """Walk each window's prints, decide, and settle. fill_mode is
    'cons' (a later print at/through us) or 'opt' (strictly through)."""
    for w in windows:
        prints = w["prints"]
        inv = l5d.Inventory()
        last_rel = ""
        resting = None
        extend_run = 0
        for i, p in enumerate(prints):
            t, px = p["t"], p["px"]
            book = {"bid": floor_tick(px), "ask": floor_tick(px) + TICK}
            # ---- does a resting quote fill, from a LATER print only?
            if resting is not None:
                hit = False
                for q in prints[i:]:
                    if q["t"] <= resting["t"]:
                        continue
                    if resting["side_yes"]:
                        # our YES bid is taken out by a print at/below it
                        hit = (q["px"] <= resting["px"] if fill_mode == "cons"
                               else q["px"] < resting["px"])
                    else:
                        thr = D(1) - resting["px"]
                        hit = (q["px"] >= thr if fill_mode == "cons"
                               else q["px"] > thr)
                    if hit:
                        break
                if hit:
                    inv.record(resting["side_yes"], resting["px"])
                    getattr(run, fill_mode)["fills"] += 1
                    last_rel = resting["rel"]
                    resting = None
            if resting is not None:
                continue               # one outstanding order at a time
            tte = max(0.0, (w["open"] + WINDOW_S) - t)
            prop = l5d._proposal(model, float(book["bid"]), inv,
                                 last_rel, tte_s=tte)
            if prop.get("off_distribution"):
                run.rel["none"] += 1
                continue
            rel = policy_fn(prop, inv, book, tte)
            plan = l5d.plan_action(rel, inv, book,
                                   pair_cap=l5d.BTC15M_PAIR_CAP)
            # THE CORRECTION. An earlier version counted plan["relation"]
            # whether or not the plan was QUOTED. At the residual cap
            # `plan_action` returns relation=extend with decision=no_action
            # and reason=residual_cap, which is an INTENT and not an action
            # -- production shows exactly that, parked at 3>=3. Counting it
            # as an extend produced run lengths up to 154 for a policy whose
            # real action run cannot exceed MAX_RESIDUAL_UNITS. Only QUOTED
            # actions count here; refusals are tallied separately as the
            # dead time they are.
            quoted = plan.get("decision") == "quote"
            rel_q = str(plan.get("relation") or "none")
            if not quoted:
                run.refused[str(plan.get("reason") or rel_q)] += 1
                run.rel["refused"] += 1
                continue
            run.rel[rel_q] += 1
            if rel_q == "extend":
                extend_run += 1
            else:
                if extend_run:
                    run.runs.append(extend_run)
                extend_run = 0
            run.quotes += 1
            run.tte_at_action.append(tte)
            if plan.get("relation") == "complete":
                surplus = inv.avg(inv.residual > 0)
                if surplus is not None:
                    run.pair_costs.append(surplus + plan["_px"])
            resting = {"side_yes": plan["_side_yes"], "px": plan["_px"],
                       "t": t, "rel": plan["relation"]}
            last_rel = plan["relation"]
        if extend_run:
            run.runs.append(extend_run)
        run.record_settle(fill_mode, inv,
                          D(1) if w["winner"] == "Up" else D(0))


def load():
    out = []
    for line in open(SEQ, encoding="utf-8"):
        r = json.loads(line)
        tr = sorted(r.get("trades") or [], key=lambda x: float(x["t"]))
        if len(tr) < 2 or not r.get("winner"):
            continue
        out.append({"open": float(r["open"]), "winner": r["winner"],
                    "prints": [{"t": float(x["t"]),
                                "px": D(str(round(float(x["price"]), 4)))}
                               for x in tr]})
    out.sort(key=lambda w: w["open"])
    return out


def main():
    model = pv2.load_v2()
    wins = load()
    cut = len(wins) // 2
    halves = (("DEV  (older half)", wins[:cut]),
              ("HOLDOUT (newer half)", wins[cut:]))
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print("windows=%d  model=%s  pair_cap=%s  residual_cap=%s"
          % (len(wins), model.get("_sha256_16"), l5d.BTC15M_PAIR_CAP,
             l5d.MAX_RESIDUAL_UNITS))
    for label, ws in halves:
        if cap:
            ws = ws[:cap]
        print("\n" + "=" * 70 + "\n%s  n=%d windows" % (label, len(ws)))
        for name, fn in POLICIES:
            run = Run(name)
            for mode in ("cons", "opt"):
                replay(ws, fn, run, model, mode)
            r = run.report()
            print("\n  %-18s decisions=%-7d quotes=%-6d mix=%s"
                  % (r["policy"], r["decisions"], r["quotes"], r["mix"]))
            print("    refusals: %s" % r["top_refusals"])
            print("    extend share %s | run med %s p90 %s max %s | "
                  "median tte at action %ss | median pair cost %s"
                  % (r["extend_share_of_ext_cpl"], r["extend_run_median"],
                     r["extend_run_p90"], r["extend_run_max"],
                     r["median_tte_at_action"], r["median_pair_cost"]))
            for lab in ("CONSERVATIVE", "OPTIMISTIC"):
                b = r[lab]
                print("    %-12s fills=%-6d matched=%-6d resid=%-6d "
                      "cost=%-10s redeem=%-10s net=%s"
                      % (lab, b["modelled_fills"], b["matched_units"],
                         b["residual_units_abs"], b["cost_total"],
                         b["redemption_total"], b["modelled_net"]))


if __name__ == "__main__":
    main()
