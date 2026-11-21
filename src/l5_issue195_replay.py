"""Replay the 0300z and 0315z losing windows against their own recorded books.

THE QUESTION THIS ANSWERS, and the one issue #195 insists be answered from
actual books rather than assumed: were the two residuals that settled for
-$1.00 on 2026-11-06 PREVENTABLE from the liquidity that was really there?

WHAT IT REPLAYS. Two archived live streams, both read-only, both committed
beside this module:

  analysis/l5/issue195/residual_book_samples_20261106_0300z_0315z.json
      256 `L5_RESIDUAL_BOOK_SAMPLE` records -- the authenticated L2 book,
      sampled every 5 s while unmatched inventory was held, carrying the
      opposite side's rungs, the executable taker walk and the maker join.

  analysis/l5/issue195/ts_completion_decisions_20261106_0300z_0315z.json
      90 `L5_TS_COMPLETION` records -- what the engine decided, including the
      43 and 22 `ok: true` decisions that reached `WOULD_SUBMIT`.

  analysis/l5/issue195/oos_shadow_residual_samples_20261106_1130z_1145z.json
      501 records from two LATER windows, for out-of-sample validation. No
      threshold in this programme was chosen with reference to them.

WHAT IT IS NOT. It is not a fill simulator and it does not claim a fill. A
crossing order against displayed depth is the closest thing to certainty this
venue offers and it is still not certain, so every figure below is reported
against HAIRCUT depth -- a quarter of what was displayed -- and the
`depth_multiple` column says how many times over the haircut book covered the
need. It replays DECISIONS, not executions, and says so in every record.

WHAT IT FOUND, stated here so it can be checked against the code:

  1. Both residuals were preventable. 0300z offered 43 separate moments where
     a cross strictly improved the worse settlement outcome, by $0.44 to
     $0.79, against 1312.39 displayed contracts for a 1-contract need. 0315z
     offered 22, by $0.04 to $0.35.

  2. The stall cost real money. The first evaluation in each window landed at
     +98.5 s and +97.5 s -- immediately after the 93 s roll -- and in 0315z
     the improvement available decayed from $0.35 at that first evaluation to
     $0.04 by the end of the window.

  3. Wiring the route is NOT sufficient on its own. With the deployed
     `DEFAULT_MARGIN_FLOOR_USD` of +$0.25 the bounded policy refuses both
     rescues, because a rescue ends below zero by construction. That is what
     the separately-authorized, default-off loss-reduction mode exists for,
     and the report below prints both verdicts side by side rather than only
     the flattering one.
"""
from __future__ import annotations

import json
import os
import pathlib
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_inventory_decision as ID
from src import l5_taker_completion_policy as TCP
from src import l5_two_sided_probe as TSP

ARCHIVE = pathlib.Path("analysis/l5/issue195")

SAMPLES = "residual_book_samples_20261106_0300z_0315z.json"
DECISIONS = "ts_completion_decisions_20261106_0300z_0315z.json"
OOS = "oos_shadow_residual_samples_20261106_1130z_1145z.json"

#: what the venue actually settled, from the campaign's own
#: L5_LIVE_SETTLED records (resolution_source VENUE_SETTLEMENT_PX,
#: venue_state MARKET_STATE_EXPIRED). Both windows settled DOWN.
SETTLED_UP = {
    "cpc-btc-updown-15m-2026-11-06-0300z": False,
    "cpc-btc-updown-15m-2026-11-06-0315z": False,
}

#: the venue-confirmed campaign result these are replayed against
ACTUAL = {
    "cpc-btc-updown-15m-2026-11-06-0300z": {
        "up_qty": D("2"), "dn_qty": D("1"),
        "up_cost_usd": D("1.24"), "dn_cost_usd": D("0.35"),
        "settled_usd": D("-0.59")},
    "cpc-btc-updown-15m-2026-11-06-0315z": {
        "up_qty": D("1"), "dn_qty": D("0"),
        "up_cost_usd": D("0.38"), "dn_cost_usd": D("0"),
        "settled_usd": D("-0.38")},
}
ACTUAL_CAMPAIGN_USD = D("-0.97")


def _load(name: str, root: Optional[str] = None) -> Dict[str, Any]:
    base = pathlib.Path(root) if root else ARCHIVE
    return json.loads((base / name).read_text())


def _d(v: Any) -> Optional[D]:
    return TSP._d(v)


def _rungs_from_sample(rec: Mapping[str, Any]) -> Optional[List[List[str]]]:
    """The OWN-PRICE rungs of the side a completion would consume.

    THE CONVERSION IS THE WHOLE POINT AND IT IS WHERE SIGNS GO WRONG. The
    wire is quoted in YES terms. To buy DOWN we consume the YES BIDS, and our
    own DOWN price at a wire bid `b` is `1 - b`; to buy UP we lift the YES
    ASKS and our own UP price is the ask itself. Cheapest own price first in
    both cases, which for DOWN means the HIGHEST wire bid first.
    """
    row = ((rec.get("book") or {}).get("row") or {})
    side = str((rec.get("reachability") or {}).get("complement_side")
               or "").upper()
    if side == "DOWN":
        raw = row.get("bids")
        if not raw:
            return None
        out = [[str(D(1) - D(str(px))), str(sz)] for px, sz in raw]
    elif side == "UP":
        raw = row.get("asks")
        if not raw:
            return None
        out = [[str(px), str(sz)] for px, sz in raw]
    else:
        return None
    return sorted(out, key=lambda r: D(r[0]))


def _mid_wire(rec: Mapping[str, Any]) -> Optional[D]:
    row = ((rec.get("book") or {}).get("row") or {})
    bids, asks = row.get("bids") or [], row.get("asks") or []
    if not bids or not asks:
        return None
    return (D(str(bids[0][0])) + D(str(asks[0][0]))) / D(2)


def replay_sample(rec: Mapping[str, Any], *, up_qty: D, dn_qty: D,
                  up_cost_px: D, dn_cost_px: D, window_start: float,
                  haircut: D = TCP.DEFAULT_DEPTH_HAIRCUT,
                  max_ev_giveup_usd: Any = None) -> Dict[str, Any]:
    """One recorded book, through the real ranking. No simulation of a fill."""
    side = str((rec.get("reachability") or {}).get("complement_side")
               or "").upper()
    deficit = abs(up_qty - dn_qty)
    rungs = _rungs_from_sample(rec)
    t = float(rec.get("t") or 0.0)
    age = ((rec.get("book") or {}).get("sync") or {}).get("row_age_s")
    out = ID.rank(
        up_qty=up_qty, dn_qty=dn_qty,
        up_cost_px=up_cost_px, dn_cost_px=dn_cost_px,
        deficient_side=side, deficit=deficit, min_qty="0.01",
        taker_rungs=rungs, depth_haircut=haircut,
        # THE MAKER SIDE IS LEFT OUT DELIBERATELY. The archived samples carry
        # a maker join price but no MEASURED maker fill rate, and
        # `l5_inventory_decision` refuses to price a maker alternative
        # without one. Inventing a rate here would be inventing the one
        # number that decides whether resting beats crossing.
        maker_own_px=None,
        seconds_to_settlement=max(0.0, window_start + TSP.WINDOW_S - t),
        up_mid_wire=_mid_wire(rec),
        max_ev_giveup_usd=max_ev_giveup_usd,
        book_age_s=age if age is not None else "0",
        gap_suspected=False)
    chosen = None
    for alt in out.get("alternatives") or ():
        if alt.get("alternative") == out.get("chosen"):
            chosen = alt
            break
    # HOW DEEP THE HAIRCUT BOOK WAS, not how much of it we used.
    # `walk_own_side` stops at the size wanted, so its quantity is the need
    # and a multiple computed from it is always 1 -- which says nothing. This
    # is the whole side, discounted, so `depth_multiple` answers the question
    # that matters: how many times over could this have been filled.
    need = deficit
    hair = None
    if rungs:
        hair = sum((D(sz) * haircut for _px, sz in rungs), D(0))
    return {
        "t": t, "t_in_window_s": round(t - window_start, 1),
        "chosen": out.get("chosen"),
        "worst_before": out.get("worst_before"),
        "worst_after": (chosen or {}).get("worst_after"),
        "worst_improvement_usd": (chosen or {}).get("worst_improvement_usd"),
        "own_px": (chosen or {}).get("own_px"),
        "qty": (chosen or {}).get("qty"),
        "fee_usd": (chosen or {}).get("fee_usd"),
        "cash_outlay_usd": (chosen or {}).get("cash_outlay_usd"),
        "ev_giveup_usd": (chosen or {}).get("ev_giveup_usd"),
        "haircut_depth": None if hair is None else str(hair),
        "depth_multiple": (None if (hair is None or need <= 0)
                           else str(hair / need)),
        "why_hold": out.get("why_hold"),
    }


def settled_counterfactual(market: str, *, complete_px: D, complete_qty: D
                           ) -> Dict[str, Any]:
    """What the window would have SETTLED at, had the completion filled.

    THE VENUE'S OWN OUTCOME, not a modelled one: both windows settled DOWN at
    yes_value 0.0000. A matched contract redeems $1.00 whichever way the
    market settles; an unmatched one redeems $1.00 only on its winning side.

    THE FEE IS CHARGED ONCE, ON THE ONE NEW ORDER. The ranking's arithmetic
    charges a notional taker fee on BOTH legs, which is the conservative
    direction for a DECISION; for a realized settlement the first leg's fee
    is already in the recorded cost basis, so charging it again would
    overstate the loss.
    """
    a = ACTUAL[market]
    up_won = SETTLED_UP[market]
    up_q, dn_q = a["up_qty"], a["dn_qty"]
    cost = a["up_cost_usd"] + a["dn_cost_usd"]
    deficient_up = dn_q > up_q
    if deficient_up:
        up_q = up_q + complete_qty
    else:
        dn_q = dn_q + complete_qty
    fee = TSP.worst_fee(complete_qty, complete_px)
    cost = cost + complete_qty * complete_px + fee
    payout = (up_q if up_won else dn_q)
    net = payout - cost
    return {"market": market, "settled_up": up_won,
            "completion_px": str(complete_px),
            "completion_qty": str(complete_qty),
            "completion_fee_usd": str(fee),
            "cost_usd": str(cost), "payout_usd": str(payout),
            "counterfactual_settled_usd": str(net),
            "actual_settled_usd": str(a["settled_usd"]),
            "delta_usd": str(net - a["settled_usd"]),
            "basis": ("the VENUE's own settlement outcome, with the "
                      "completion filled at the recorded executable price. "
                      "It is a counterfactual on a real book, not a claimed "
                      "fill.")}


def policy_verdicts(market: str, *, decision: Mapping[str, Any],
                    rungs: Any) -> Dict[str, Any]:
    """Would the bounded policy have authorized it? Both modes, side by side.

    This is the finding that matters most and the one it would be easiest to
    leave out: wiring the submission route is necessary and NOT sufficient.
    """
    common = dict(
        decision=dict(decision), enabled=True, owner_authorized=True,
        held_up_qty=ACTUAL[market]["up_qty"],
        held_dn_qty=ACTUAL[market]["dn_qty"],
        held_up_px=(ACTUAL[market]["up_cost_usd"] / ACTUAL[market]["up_qty"]
                    if ACTUAL[market]["up_qty"] else D(0)),
        held_dn_px=(ACTUAL[market]["dn_cost_usd"] / ACTUAL[market]["dn_qty"]
                    if ACTUAL[market]["dn_qty"] else D(0)),
        maker_attempted=True, maker_rested_s=D("120"),
        window_outlay_spent_usd=D(0), book_rungs=rungs, halted=False)
    profit = TCP.authorize(**common)
    rescue = TCP.authorize(**dict(common, loss_reduction=True,
                                  min_worst_improvement_usd=D("0.25")))
    return {"profit_mode": {"authorized": bool(profit.get("authorized")),
                            "reason": profit.get("reason")},
            "loss_reduction_mode": {
                "authorized": bool(rescue.get("authorized")),
                "reason": rescue.get("reason"),
                "qty": rescue.get("qty"), "price": rescue.get("price"),
                "min_worst_improvement_usd": D("0.25")
                and str(D("0.25"))},
            "labels": ("profit_mode is the DEPLOYED configuration. A rescue "
                       "ends below zero by construction, so the +$0.25 "
                       "margin floor refuses it however good the "
                       "improvement -- which is why the loss-reducing case "
                       "needs its own default-off authorization.")}


def replay_window(market: str, *, samples: List[Mapping[str, Any]],
                  decisions: List[Mapping[str, Any]],
                  max_ev_giveup_usd: Any = None) -> Dict[str, Any]:
    a = ACTUAL[market]
    up_px = (a["up_cost_usd"] / a["up_qty"]) if a["up_qty"] else D(0)
    dn_px = (a["dn_cost_usd"] / a["dn_qty"]) if a["dn_qty"] else D(0)
    mine = [s for s in samples if str(s.get("slug")) == market]
    if not mine:
        return {"market": market, "ok": False, "reason": "NO_SAMPLES"}
    ws = min(float(s["t"]) for s in mine)
    ws = ws - (ws % TSP.WINDOW_S)
    rows = [replay_sample(s, up_qty=a["up_qty"], dn_qty=a["dn_qty"],
                          up_cost_px=up_px, dn_cost_px=dn_px,
                          window_start=ws,
                          max_ev_giveup_usd=max_ev_giveup_usd)
            for s in mine]
    acted = [r for r in rows if r["chosen"] in (ID.TAKER_COMPLETE,
                                                ID.TAKER_PARTIAL)]
    best = (max(acted, key=lambda r: D(r["worst_improvement_usd"]))
            if acted else None)
    first = acted[0] if acted else None

    # THE ENGINE'S OWN DECISIONS, for the same window, as it recorded them.
    live = [d["rec"] if isinstance(d, Mapping) and "rec" in d else d
            for d in decisions]
    live = [d for d in live if str(d.get("market")) == market]
    live_ok = [d for d in live if d.get("ok")]
    crossed = [d for d in live_ok if d.get("crossed") not in (False, None)]

    out: Dict[str, Any] = {
        "market": market, "ok": True,
        "settled_up": SETTLED_UP[market],
        "held": {"up_qty": str(a["up_qty"]), "dn_qty": str(a["dn_qty"]),
                 "up_cost_px": str(up_px), "dn_cost_px": str(dn_px)},
        "samples": len(mine),
        "executable_and_economic_samples": len(acted),
        "first_actionable": first, "best_actionable": best,
        "live_engine": {
            "decisions": len(live),
            "decisions_ok": len(live_ok),
            "ever_submitted": len(crossed),
            "crossed_values": sorted({str(d.get("crossed"))
                                      for d in live_ok}),
            "withheld_by": sorted({str((d.get("taker_policy") or {})
                                       .get("reason")) for d in live_ok}),
        },
    }
    if first is not None:
        out["counterfactual_at_first_actionable"] = settled_counterfactual(
            market, complete_px=D(first["own_px"]),
            complete_qty=D(first["qty"]))
    if best is not None:
        out["counterfactual_at_best_actionable"] = settled_counterfactual(
            market, complete_px=D(best["own_px"]),
            complete_qty=D(best["qty"]))
    if live_ok:
        out["policy_verdicts_on_the_live_decision"] = policy_verdicts(
            market, decision=live_ok[0],
            rungs=_rungs_from_sample(mine[0]))
        out["decay"] = {
            "first_improvement_usd": str(live_ok[0].get(
                "worst_improvement_usd")),
            "last_improvement_usd": str(live_ok[-1].get(
                "worst_improvement_usd")),
            "labels": ("how much the opportunity was worth at the FIRST "
                       "evaluation and at the last. The first evaluation "
                       "could not happen until the 93-second roll "
                       "returned."),
        }
    return out


def replay(root: Optional[str] = None, *, max_ev_giveup_usd: Any = None
           ) -> Dict[str, Any]:
    """The whole report. READ-ONLY: it opens two archived files and nothing
    else, holds no transport and no store, and writes nothing."""
    s = _load(SAMPLES, root)
    dec = _load(DECISIONS, root)
    samples = [x["rec"] for x in s["samples"]]
    decisions = dec["decisions"]
    windows = [replay_window(m, samples=samples, decisions=decisions,
                             max_ev_giveup_usd=max_ev_giveup_usd)
               for m in sorted(ACTUAL)]
    cf_first = D(0)
    cf_best = D(0)
    for w in windows:
        if not w.get("ok"):
            continue
        for key, acc in (("counterfactual_at_first_actionable", "first"),
                         ("counterfactual_at_best_actionable", "best")):
            c = w.get(key)
            if not c:
                continue
            if acc == "first":
                cf_first += D(c["counterfactual_settled_usd"])
            else:
                cf_best += D(c["counterfactual_settled_usd"])
    return {
        "report": "ISSUE_195_CAUSAL_REPLAY",
        "source": {"samples": s.get("source"), "decisions": dec.get("source")},
        "windows": windows,
        "campaign": {
            "actual_settled_usd": str(ACTUAL_CAMPAIGN_USD),
            "counterfactual_at_first_actionable_usd": str(cf_first),
            "counterfactual_at_best_actionable_usd": str(cf_best),
            "delta_at_first_usd": str(cf_first - ACTUAL_CAMPAIGN_USD),
            "note": ("the 0245z window traded nothing and settled 0.00, so "
                     "it contributes to neither total."),
        },
        "preventable": all(
            (w.get("executable_and_economic_samples") or 0) > 0
            for w in windows if w.get("ok")),
        "limitation_the_first_98_seconds_are_unobservable": (
            "THE STALL ALSO BLOCKED THE SAMPLER. `sample_residual_book` runs "
            "in the same tick, after `roll`, so the earliest recorded book "
            "in either window is about +115 s. Nothing here can say what the "
            "book looked like during the 93 s the roll held, and nothing "
            "here claims to. What IS measured is that the opportunity "
            "decayed monotonically across the observable part of 0315z, from "
            "$0.35 at the first sample to $0.04 at the last -- so the time "
            "the diagnostic spent was not free time, and the unobserved "
            "first 98 s is a gap in the evidence rather than a point in the "
            "fix's favour."),
        "sufficient": False,
        "why_not_sufficient": (
            "wiring the submission route is necessary and NOT sufficient. A "
            "rescue's best achievable worse outcome is still negative, so "
            "the deployed +$0.25 margin floor refuses both windows. The "
            "loss-reducing case needs its own default-off authorization and "
            "an owner-named improvement floor; see "
            "l5_taker_completion_policy.LOSS_REDUCTION_ENV."),
        "labels": ("REPLAY OF DECISIONS, NOT OF EXECUTIONS. Every figure is "
                   "priced against HAIRCUT displayed depth and no fill is "
                   "claimed anywhere. `depth_multiple` says how many times "
                   "over the haircut book covered the need."),
    }


def replay_oos(root: Optional[str] = None) -> Dict[str, Any]:
    """OUT OF SAMPLE: two later windows, modelled inventory, live books.

    It answers a narrower question than the causal replay, and only the
    narrow one: on books nobody tuned anything against, does the ranking
    produce decisions at all, and are they the shape the in-sample windows
    produced -- or does it fire everywhere, which would mean it is measuring
    nothing.

    THERE IS NO SETTLED P&L HERE and none is computed. The inventory is
    modelled, so there is no venue position and no realized result to
    compare against; claiming one would be the exact error this programme has
    already paid for once.
    """
    d = _load(OOS, root)
    rows: List[Dict[str, Any]] = []
    for item in d["samples"]:
        rec = item["rec"]
        r = (rec.get("reachability") or {})
        first = _d((r.get("pair_if_maker") or {}).get("first_leg_own_px"))
        resid = _d(rec.get("residual"))
        held = str(rec.get("held_side") or "").upper()
        if first is None or resid is None or resid == 0 or held not in (
                "UP", "DOWN"):
            continue
        q = abs(resid)
        up_q, dn_q = (q, D(0)) if held == "UP" else (D(0), q)
        up_px, dn_px = (first, D(0)) if held == "UP" else (D(0), first)
        t = float(rec["t"])
        ws = t - (t % TSP.WINDOW_S)
        rows.append(dict(replay_sample(
            rec, up_qty=up_q, dn_qty=dn_q, up_cost_px=up_px,
            dn_cost_px=dn_px, window_start=ws),
            variant=rec.get("shadow_variant"),
            slug=str(rec.get("slug"))))
    acted = [r for r in rows if r["chosen"] in (ID.TAKER_COMPLETE,
                                                ID.TAKER_PARTIAL)]
    held = [r for r in rows if r["chosen"] == ID.HOLD]
    improvements = sorted(D(r["worst_improvement_usd"]) for r in acted)
    return {
        "report": "ISSUE_195_OUT_OF_SAMPLE",
        "source": d.get("source"), "purpose": d.get("purpose"),
        "samples": len(rows),
        "chose_completion": len(acted),
        "chose_hold": len(held),
        "completion_rate": (None if not rows
                            else str(D(len(acted)) / D(len(rows)))),
        "improvement_usd": {
            "min": str(improvements[0]) if improvements else None,
            "median": (str(improvements[len(improvements) // 2])
                       if improvements else None),
            "max": str(improvements[-1]) if improvements else None},
        # HOW MANY SURVIVE AN IMPROVEMENT FLOOR, which is the number the
        # owner actually has to choose. A ranking that surfaces a one-cent
        # rescue is working correctly; acting on one is not, and the floor is
        # what separates the two. Reported across a range rather than at one
        # value so the choice is visible instead of implied.
        "survivors_by_improvement_floor_usd": {
            str(f): len([r for r in acted
                         if D(r["worst_improvement_usd"]) >= D(f)])
            for f in ("0.05", "0.10", "0.25", "0.50", "1.00")},
        "hold_reasons": sorted({str(r.get("why_hold")) for r in held
                                if r.get("why_hold")}),
        "discriminates": bool(acted) and bool(held),
        "labels": ("MODELLED inventory over LIVE books. There is no venue "
                   "position here and NO realized P&L is computed. "
                   "`discriminates` false would mean the ranking fires "
                   "everywhere or nowhere, which would make it evidence of "
                   "nothing."),
    }


def main(argv: Optional[List[str]] = None) -> int:
    which = (argv or [None])[0] if argv else os.getenv("L5_REPLAY_WHICH")
    if str(which) == "oos":
        print(json.dumps(replay_oos(), indent=1, default=str))
    else:
        print(json.dumps(replay(), indent=1, default=str))
    return 0


if __name__ == "__main__":       # pragma: no cover
    import sys
    raise SystemExit(main(sys.argv[1:]))
