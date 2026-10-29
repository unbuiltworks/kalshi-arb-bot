"""If COMPLETE could re-quote on a moved touch, what would it have earned?

OWNER: "Replay/backtest it against the existing tape and quantify: additional
attainable completions, COMPLETE fill rate, pairing rate, residual reduction,
pair cost, residual P&L, and counterfactual net economics."

THE MECHANISM BEING REPLAYED ALREADY EXISTS. The live session cancels and
re-quotes when L5's touch moves off a resting order, under

    if relation_ in ("open", "extend") and touch != px_:
        return f"touch_moved:{touch}"

COMPLETE is excluded, and the exclusion is visible in the tape: 36 OPEN and
35 EXTEND orders exited via repriced:touch_moved against 0 of 868 COMPLETEs.
So this is a fidelity correction -- following L5's own price when it moves --
and not a new strategy. Nothing here pays more than the price the touch is
at.

WHY THIS IS NOT A COUNT OF FILLS. Repricing a BUY upward buys fills at a
worse price. The live session already computes pair_at_touch on every
completion decision and it is frequently ABOVE PAR: in the first session
read while building this, opening at 0.4698 and completing at the 0.61 touch
would have cost $1.0798 for a $1.00 payout. An analysis that counted
"additional completions" would have booked that as a win. So every
counterfactual completion here is priced, and the comparison is

    complete at the re-quote price  ->  a KNOWN pair cost, win or lose
    hold to settlement              ->  measured at -$0.331449 per unit

An above-par completion at 1.08 locks -$0.08 and still beats a residual that
has averaged -$0.33. That is the arithmetic the net is built on, and the
cohort average for stranding is the weakest link in it -- it carries the
variance of a coin flip and the selection of which markets stranded.

HOW PAIR COST IS OBTAINED. Not from the session-end inventory, which
reflects fills that happened AFTER the order being replayed. Orders are
walked in sequence and the running average cost of each side is accumulated
from the fills, so the cost basis used against each unfilled COMPLETE is the
one that actually existed when it was resting.

ONE COUNTERFACTUAL PER MARKET, AT THE FIRST DIVERGENCE. The first version of
this replay counted every unfilled COMPLETE independently and produced 77.76
counterfactual units against a cohort that only ever stranded 29.80 -- one
market contributed 5.5 + 5.41 + 5.41 units against a residual that peaked at
10.91. If the first re-quote had filled, the later completes never happen:
the tape after the divergence point is a history that would not have
existed. So at most ONE counterfactual completion is taken per market, the
earliest triggered-and-reached one, which also absorbs the case of a restart
resuming the same held position. It understates a market that genuinely had
two separate residual episodes, and understating is the right direction.

THE TWO BOUNDS, WHICH DO NOT CANCEL:
  * max_touch only reveals the touch moving UP, so triggers are a LOWER
    bound -- a touch that moved down would also fire a re-quote and is
    invisible;
  * min_opp_ask and max_touch are extrema over the same window and cannot
    be sequenced, so conversion is an UPPER bound.
Every figure is reported against a conversion ladder for that reason, and
the middle rung is the one the frozen bar scores.

READ-ONLY.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src import l5_complete_requote_gate as gate
from src import l5_completion_economics as ce
from src import l5_completion_extension_eval as ev

ONE = D("1.00")
RESIDUAL_LOSS_PER_UNIT = D("-0.331449")     # l5_breakeven, 15 markets
COMPLETE_REJECT_RATE = D("0.1866")          # 162 / 868, observed
BREAKER_MAX = 3                             # COMPLETE_REJECT_STREAK_MAX
CONVERSION_LADDER = (D("1.00"), D("0.50"), D("0.25"))


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


def walk_session(session: Mapping[str, Any]) -> List[dict]:
    """Replay one session's orders in sequence, carrying running cost basis.

    Returns one row per accepted-but-unfilled COMPLETE that carries a rest
    book, with the inventory state that actually existed while it rested."""
    up_q = dn_q = up_c = dn_c = D("0")
    out: List[dict] = []
    for o in (session.get("orders") or []):
        px, fq = _d(o.get("px")), _d(o.get("filled_qty")) or D("0")
        side = str(o.get("side") or "").upper()
        rb = o.get("rest_book") or {}
        is_unfilled_complete = (o.get("relation") == "complete"
                                and fq == 0 and bool(rb)
                                and not str(o.get("end_reason") or ""
                                            ).startswith("rejected"))
        if is_unfilled_complete and px is not None:
            resid_up = up_q - dn_q
            # the completing side is the one we are short of
            comp_is_no = resid_up > 0
            basis_q = up_q if comp_is_no else dn_q
            basis_c = up_c if comp_is_no else dn_c
            avg = (basis_c / basis_q) if basis_q > 0 else None
            mo, mt = _d(rb.get("min_opp_ask")), _d(rb.get("max_touch"))
            resid = abs(resid_up)
            qty = _d(o.get("qty")) or D("0")
            fillable = min(qty, resid) if resid > 0 else D("0")
            triggered = bool(mt is not None and mt != px)
            reached = bool(triggered and mo is not None and mt is not None
                           and mo <= mt)
            pair_cost = (avg + mt) if (avg is not None and mt is not None) else None
            out.append({
                "slug": session.get("slug"),
                "stage": o.get("stage"),
                "our_px": str(px),
                "requote_px": str(mt) if mt is not None else None,
                "min_opp_ask": str(mo) if mo is not None else None,
                "side": side,
                "residual_units_at_rest": str(resid),
                "fillable_units": str(fillable),
                "cost_basis_per_unit": str(avg) if avg is not None else None,
                "pair_cost_at_requote": (str(pair_cost) if pair_cost is not None
                                         else None),
                "above_par": (bool(pair_cost > ONE) if pair_cost is not None
                              else None),
                "requote_triggered": triggered,
                "book_reached_requote_px": reached,
                "locked_usd_if_completed": (
                    str((ONE - pair_cost) * fillable)
                    if (pair_cost is not None and fillable > 0) else None),
                "stranded_usd_if_held": str(RESIDUAL_LOSS_PER_UNIT * fillable),
            })
        if fq > 0 and px is not None:
            if side == "YES":
                up_q += fq
                up_c += fq * px
            elif side == "NO":
                dn_q += fq
                dn_c += fq * px
    return out


def _budget_and_breaker(session: Mapping[str, Any], requotes: int) -> dict:
    """Would the extra orders exhaust the action budget or trip the breaker?

    Each re-quote consumes one action. The extension already grants
    max_actions + 12 while a residual is open, so the question is whether the
    re-quotes push a session past what it actually used."""
    used = len(session.get("orders") or [])
    return {"orders_used": used, "requotes_added": requotes,
            "would_exceed_extended_budget": bool(used + requotes > 6 + 12)}


def replay(paths: Iterable[Any]) -> dict:
    sha = gate.assert_unmodified()
    sessions = ev._sessions(paths)
    rows: List[dict] = []
    budget: List[dict] = []
    for s in sessions:
        r = walk_session(s)
        if r:
            rows.extend(r)
            budget.append(_budget_and_breaker(
                s, sum(1 for x in r if x["requote_triggered"])))
    triggered = [r for r in rows if r["requote_triggered"]]
    reached = [r for r in triggered if r["book_reached_requote_px"]]
    priced_all = [r for r in reached if r["pair_cost_at_requote"] is not None
                  and r["locked_usd_if_completed"] is not None]
    # FIRST DIVERGENCE ONLY. After the first re-quote the tape is a history
    # that would not have happened, so later completes on the same market are
    # not evidence -- they are the consequence of the leg we are replacing.
    priced, seen_market = [], set()
    for r in priced_all:
        if r["slug"] in seen_market:
            continue
        seen_market.add(r["slug"])
        priced.append(r)
    above = [r for r in priced if r["above_par"]]
    below = [r for r in priced if not r["above_par"]]

    def usd(rs, key):
        return sum((D(r[key]) for r in rs if r.get(key)), D("0"))

    locked = usd(priced, "locked_usd_if_completed")
    stranded_avoided = -usd(priced, "stranded_usd_if_held")
    units = sum((D(r["fillable_units"]) for r in priced), D("0"))

    # rejection cost: every re-quote is one more order on the #93 surface
    expected_rejections = D(len(triggered)) * COMPLETE_REJECT_RATE

    ladder = []
    for c in CONVERSION_LADDER:
        l, s_, u = locked * c, stranded_avoided * c, units * c
        ladder.append({
            "conversion": str(c),
            "completions": int(len(priced) * float(c)),
            "units_paired": str(u.quantize(D("0.01"))),
            "locked_pair_usd": str(l.quantize(D("0.0001"))),
            "residual_loss_avoided_usd": str(s_.quantize(D("0.0001"))),
            "net_usd": str((l + s_).quantize(D("0.0001"))),
            "net_positive": bool((l + s_) > 0),
        })

    # THE SENSITIVITY THAT DECIDES IT. The whole net rests on charging a
    # stranded unit -$0.331449. Solve instead for the stranding cost at which
    # the change breaks even -- that number does not depend on the estimate
    # and is what the decision should be read against.
    breakeven_strand = ((-locked / units) if units > 0 else None)

    per_market: Dict[str, D] = defaultdict(lambda: D("0"))
    for r in priced:
        per_market[r["slug"]] += (D(r["locked_usd_if_completed"])
                                  - D(r["stranded_usd_if_held"]))
    net_total = locked + stranded_avoided
    top = max(per_market.values(), default=D("0"))
    return {
        "gate": gate.GATE, "gate_sha16": sha,
        "unfilled_completes_replayed": len(rows),
        "requote_triggered": len(triggered),
        "book_reached_requote_px": len(reached),
        "priced_counterfactual_completions": len(priced),
        "counterfactuals_dropped_after_first_divergence":
            len(priced_all) - len(priced),
        "first_divergence_rule": (
            "at most ONE counterfactual completion per market, the earliest "
            "triggered-and-reached one. After it the tape is a history that "
            "would not have existed, so later completes on the same market "
            "are consequences of the leg being replaced, not evidence."),
        "distinct_markets": len({r["slug"] for r in priced}),
        "pair_cost": {
            "above_par": len(above),
            "at_or_below_par": len(below),
            "above_par_share": (round(len(above) / len(priced), 4)
                                if priced else None),
            "worst_pair_cost": (max((D(r["pair_cost_at_requote"])
                                     for r in priced), default=None)),
            "median_pair_cost": (sorted(D(r["pair_cost_at_requote"])
                                        for r in priced)[len(priced) // 2]
                                 if priced else None),
        },
        "economics": {
            "units_paired": str(units.quantize(D("0.01"))),
            "locked_pair_usd_is_the_only_certain_term": (
                "locked_pair_usd is what the counterfactual TRADE earns: a "
                "known cost against a $1.00 payout. residual_loss_avoided is "
                "what we assume the alternative would have cost, from a "
                "cohort mean over four markets. If locked_pair_usd is "
                "negative the change loses money on every pair it makes and "
                "is only defensible if you believe the stranding estimate."),
            "locked_pair_usd_at_full_conversion":
                str(locked.quantize(D("0.0001"))),
            "residual_loss_avoided_usd_at_full_conversion":
                str(stranded_avoided.quantize(D("0.0001"))),
            "net_usd_at_full_conversion":
                str(net_total.quantize(D("0.0001"))),
            "locked_pair_usd_per_unit": (
                str((locked / units).quantize(D("0.0001")))
                if units > 0 else None),
            "breakeven_stranding_cost_per_unit_usd": (
                str(breakeven_strand.quantize(D("0.0001")))
                if breakeven_strand is not None else None),
            "breakeven_reading": (
                "the change pays for itself if a stranded unit costs more "
                f"than ${breakeven_strand.quantize(D('0.0001'))} to hold. Our "
                f"measured cost is ${-RESIDUAL_LOSS_PER_UNIT}. The estimate "
                "would have to be wrong by roughly "
                f"{(-RESIDUAL_LOSS_PER_UNIT / breakeven_strand).quantize(D('0.1'))}x "
                "in our favour for this to lose money."
                if (breakeven_strand and breakeven_strand > 0) else
                "the counterfactual pairs are profitable on their own terms; "
                "the stranding estimate is not load-bearing"),
            "if_residuals_were_fairly_priced_net_usd": (
                str(locked.quantize(D("0.0001")))),
            "why_that_rung_exists": (
                "a residual unit bought at fair value has expected P&L near "
                "zero, not -$0.33. The measured -$0.33 is a sample of four "
                "markets with one binary outcome each -- effectively n=4, not "
                "n=29.8. If residuals are fairly priced this change simply "
                "loses the locked amount."),
            "stranding_counterfactual_is_a_cohort_average": (
                f"residual is charged at {RESIDUAL_LOSS_PER_UNIT} per unit, "
                "the cohort mean. It carries the variance of a coin flip and "
                "the selection of which markets stranded, and it is the "
                "weakest link in the net."),
        },
        "operational": {
            "complete_fill_rate_before": 0.1440,      # 125 / 868
            "complete_orders_before": 868,
            "complete_fills_before": 125,
            "extra_complete_orders": len(triggered),
            "extra_complete_fills": len(priced),
            "complete_fill_rate_after": (
                round((125 + len(priced)) / (868 + len(triggered)), 4)
                if triggered else None),
            "residual_units_removed": str(units.quantize(D("0.01"))),
            "note": (
                "fill RATE barely moves because every re-quote is also a new "
                "order in the denominator. The change is not about the rate; "
                "it is about the residual units removed and what they cost."),
        },
        "conversion_ladder": ladder,
        "rejection_exposure": {
            "extra_orders": len(triggered),
            "expected_additional_rejections":
                str(expected_rejections.quantize(D("0.1"))),
            "observed_complete_reject_rate": str(COMPLETE_REJECT_RATE),
            "why_it_matters": (
                "a rejected complete at a non-crossing price revokes the "
                "complement proof; three consecutive end the session"),
        },
        "budget": {
            "sessions_with_requotes": sum(1 for b in budget
                                          if b["requotes_added"]),
            "sessions_exceeding_extended_budget": sum(
                1 for b in budget if b["would_exceed_extended_budget"]),
        },
        "concentration": {
            "largest_single_market_share_of_net": (
                float(round(top / net_total, 4))
                if net_total > 0 else None),
        },
        "bounds": dict(gate.REFUSALS),
        "rows": priced,
    }


def score(rep: Mapping[str, Any]) -> dict:
    """Apply the frozen bar. The bar decides; this only reads it."""
    ms, mn = gate.MUST_SHOW, gate.MUST_NOT_WORSEN
    rung = next((r for r in rep["conversion_ladder"]
                 if D(r["conversion"]) == D(str(ms["net_positive_at_conversion_rung"]))),
                None)
    checks = {
        "net_positive_at_full_conversion":
            D(rep["economics"]["net_usd_at_full_conversion"]) > 0,
        f"net_positive_at_{ms['net_positive_at_conversion_rung']}_conversion":
            bool(rung and rung["net_positive"]),
        "additional_completions_min":
            rep["priced_counterfactual_completions"] >= ms[
                "additional_completions_min"],
        "distinct_markets_min":
            rep["distinct_markets"] >= ms["distinct_markets_min"],
        "not_concentrated_in_one_market": (
            (rep["concentration"]["largest_single_market_share_of_net"] or 0)
            <= ms["max_share_of_one_market"]
            if "max_share_of_one_market" in ms else
            (rep["concentration"]["largest_single_market_share_of_net"] or 0)
            <= ms["max_share_of_net_from_one_market"]),
        "no_session_loses_an_opening_to_budget":
            rep["budget"]["sessions_exceeding_extended_budget"] <= mn[
                "sessions_losing_an_opening_to_budget_exhaustion_max"],
    }
    failed = [k for k, v in checks.items() if not v]
    if D(rep["economics"]["net_usd_at_full_conversion"]) <= 0:
        verdict = "OFFLINE_EVIDENCE_NEGATIVE"
    elif failed:
        verdict = "OFFLINE_EVIDENCE_WEAK"
    else:
        verdict = "OFFLINE_EVIDENCE_STRONG"
    return {
        "gate": rep["gate"], "gate_sha16": rep["gate_sha16"],
        "verdict": verdict, "checks": checks, "failed": failed,
        "authorises_live_ab_design":
            verdict in gate.AUTHORISES_LIVE_AB_DESIGN,
        "authorises_shipping": False,
        "non_contamination": gate.NON_CONTAMINATION,
    }


def render_markdown(rep: Mapping[str, Any], sc: Mapping[str, Any]) -> str:
    e, p = rep["economics"], rep["pair_cost"]
    L = ["# COMPLETE re-quote on a moved touch — replay against the tape", "",
         f"Bar `{rep['gate']}` sha `{rep['gate_sha16']}` — "
         f"**{sc['verdict']}**", "",
         "| | |", "|---|---|",
         f"| unfilled COMPLETEs replayed | {rep['unfilled_completes_replayed']} |",
         f"| a re-quote would have fired | {rep['requote_triggered']} |",
         f"| ...and the book reached that price | "
         f"{rep['book_reached_requote_px']} |",
         f"| priced counterfactual completions | "
         f"{rep['priced_counterfactual_completions']} "
         f"across {rep['distinct_markets']} markets |", "",
         "## Pair cost — the part a fill count would have hidden", "",
         "| | |", "|---|---|",
         f"| above par (locks a loss) | {p['above_par']} "
         f"({p['above_par_share']:.1%}) |" if p["above_par_share"] is not None
         else f"| above par | {p['above_par']} |",
         f"| at or below par | {p['at_or_below_par']} |",
         f"| median pair cost | {p['median_pair_cost']} |",
         f"| worst pair cost | {p['worst_pair_cost']} |", "",
         "## Counterfactual economics", "",
         "| conversion | completions | units | locked pair $ | residual loss "
         "avoided $ | net $ |", "|---|---|---|---|---|---|"]
    for r in rep["conversion_ladder"]:
        L.append(f"| {float(r['conversion']):.0%} | {r['completions']} | "
                 f"{r['units_paired']} | {r['locked_pair_usd']} | "
                 f"{r['residual_loss_avoided_usd']} | **{r['net_usd']}** |")
    L += ["", "> " + e["stranding_counterfactual_is_a_cohort_average"], "",
          "## Costs charged", "",
          f"- extra orders on the #93 surface: "
          f"**{rep['rejection_exposure']['extra_orders']}**, expected "
          f"additional rejections "
          f"**{rep['rejection_exposure']['expected_additional_rejections']}** "
          f"at the observed {rep['rejection_exposure']['observed_complete_reject_rate']} rate",
          f"- {rep['rejection_exposure']['why_it_matters']}",
          f"- sessions exceeding the extended action budget: "
          f"**{rep['budget']['sessions_exceeding_extended_budget']}** of "
          f"{rep['budget']['sessions_with_requotes']} with re-quotes", "",
          "## Frozen bar", "", "| check | result |", "|---|---|"]
    for k, v in sc["checks"].items():
        L.append(f"| {k} | {'PASS' if v else 'FAIL'} |")
    L += ["", f"Authorises designing a live A/B: "
              f"**{sc['authorises_live_ab_design']}**. "
              f"Authorises shipping: **{sc['authorises_shipping']}**.", "",
          "## Bounds", ""]
    for k, v in rep["bounds"].items():
        L.append(f"- **{k}** — {v}")
    L += ["", "## Non-contamination", "", sc["non_contamination"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    paths = ([os.path.join(a.logs, f) for f in sorted(os.listdir(a.logs))]
             if os.path.isdir(a.logs) else [a.logs])
    rep = replay(paths)
    sc = score(rep)
    md = render_markdown(rep, sc)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "COMPLETE_REQUOTE_REPLAY.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "complete_requote_replay.json"),
                  "w") as f:
            json.dump({"replay": rep, "score": sc}, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
