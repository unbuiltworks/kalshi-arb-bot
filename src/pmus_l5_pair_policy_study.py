"""EXECUTION-POLICY SENSITIVITY: is $1.08-1.12 the venue, or our rules?

THE QUESTION. The live shadow measured that once a first leg is held, the
complement's own-side maker price plus the residual's cost basis comes to
1.06-1.21, with 0% at or below the 0.96 ceiling. That number was produced
under ONE entry rule and ONE completion rule. Before calling the venue
structurally unsuitable, the same collected tape is replayed under six
execution policies to separate the market from our choices.

WHAT THIS IS NOT. No new strategy, no new executor, no new service. It is a
pure function over the observation tape this service already writes, run
inside the same process, and it places nothing.

THE RULES OF THE REPLAY, which are what make the answer worth anything:

  * CHRONOLOGICAL. Each market's observations are replayed in time order and
    every decision uses only rows at or before the decision instant. A later
    book is never information at entry.
  * NO ASSUMED FILLS. A resting quote fills only when volume printed THROUGH
    its threshold exceeds the queue that was resting at or better than its
    price when it was placed -- the same pessimistic test the live shadow
    uses, applied here to both legs.
  * NO RETROACTIVE PRICING. A policy's quote price is whatever its rule says
    at placement time. Nothing is re-priced after the fact, and no policy is
    allowed to pick the best price in hindsight.
  * FEES ARE CHARGED WHERE THE POLICY CROSSES. A maker leg pays nothing and
    accrues no rebate here; a taker leg pays the published schedule.
  * UNFILLED MEANS UNFILLED. A policy that fails to complete carries a
    directional residual to the end of the window and is scored with it.

THE SIX POLICIES (one knob each, so the comparison attributes cause):

  P1 IMMEDIATE_TAKER     cross the complement the moment the first leg fills
  P2 DELAYED_MAKER       rest the complement at its own-side bid, bounded wait
  P3 BOTH_SIDES_RESTING  quote both sides from the start, independently
  P4 ACCUMULATE          up to K same-side fills, then complete the lot
  P5 SELECTIVE_ENTRY     enter only when pre-entry drift is not against us
  P6 INVENTORY_AWARE     complete whatever fits the budget, continuously

THE FOUR PLACEMENT POLICIES (P7-P10). P1-P6 attempt entry ONCE per market, so
they cannot answer how many fills PMUS flow can supply -- which is the gap
between L5's ~80 fills per market and our 1-3. P7-P10 re-quote continuously
across the whole window and differ only in WHERE the resting quote sits:

  P7 BEST_BID_RESTING    rest at the prevailing own-side best bid
  P8 DEEPER_RESTING      rest one tick behind the bid (cheaper, longer queue)
  P9 ONE_TICK_IMPROVE    rest one tick in front of the bid (dearer, no queue)
  P10 CONTINUOUS_TWO_SIDED  both sides quoted at the bid, every requote

All four complete inventory-aware: whenever a residual exists, the complement
is also rested, at the best price the pair ceiling still allows. All legs are
maker, so none pays a taker fee and none is credited a rebate. The queue
discipline is `resting_fill`'s and is unchanged: the queue standing at
placement must be cleared by printed volume, only the excess is ours, and a
missing ladder is never an empty queue.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_execution_shadow as EX
from src import pmus_fees as FEES
from src import pmus_status_report as ST

ONE = D("1")
TICK = D("0.01")
CEILING = D(os.getenv("L5_CLONE_PAIR_CEILING", "0.96"))
UNIT = D("1")
MAX_WAIT_S = float(os.getenv("L5_STUDY_MAX_WAIT_S", "120"))
MAX_ACCUM = int(os.getenv("L5_STUDY_MAX_ACCUM", "3"))
DRIFT_WINDOW_S = float(os.getenv("L5_STUDY_DRIFT_S", "60"))
MARKOUT_S = 30.0

POLICIES = ("P1_IMMEDIATE_TAKER", "P2_DELAYED_MAKER", "P3_BOTH_SIDES_RESTING",
            "P4_ACCUMULATE_THEN_COMPLETE", "P5_SELECTIVE_ENTRY",
            "P6_INVENTORY_AWARE")

# placement policies: {name: (tick offset from own-side bid, two-sided?)}
PLACEMENT = {"P7_BEST_BID_RESTING": (0, False),
             "P8_DEEPER_RESTING": (-1, False),
             "P9_ONE_TICK_IMPROVE": (1, False),
             "P10_CONTINUOUS_TWO_SIDED": (0, True)}
ALL_POLICIES = POLICIES + tuple(PLACEMENT)
REQUOTE_S = float(os.getenv("L5_STUDY_REQUOTE_S", "60"))
MAX_EXTEND = D(os.getenv("L5_STUDY_MAX_EXTEND", "3"))


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def own_side(row: Mapping[str, Any], side_yes: bool) -> Optional[dict]:
    """Own-side maker and taker price in the side's own frame, or None."""
    b, a = _f(row.get("bid")), _f(row.get("ask"))
    if b is None or a is None or a <= b:
        return None
    B, A = D(str(b)), D(str(a))
    return {"maker": B if side_yes else ONE - A,
            "taker": A if side_yes else ONE - B,
            "mid": (A + B) / 2, "bid": B, "ask": A}


def queue_ahead(row: Mapping[str, Any], side_yes: bool, px: D
                ) -> Optional[float]:
    """Shares at or better than our price on our own side, or None if the
    ladder is missing -- in which case the quote is never deemed filled."""
    lv = row.get("book_levels") or {}
    side = lv.get("bids") if side_yes else lv.get("offers")
    if not side:
        return None
    yes_px = px if side_yes else (ONE - px)
    tot = 0.0
    for l in side:
        p, q = _f(l.get("px")), _f(l.get("qty"))
        if p is None or q is None:
            continue
        pp = D(str(p))
        if (pp > yes_px if side_yes else pp < yes_px) or pp == yes_px:
            tot += q
    return tot


def resting_fill(obs: Sequence[Mapping[str, Any]], i0: int, side_yes: bool,
                 px: D, qty: D, deadline: Optional[float]) -> dict:
    """When, if ever, a quote resting from obs[i0] would defensibly fill.

    Conservative on three axes at once: the queue standing at placement must
    be cleared by printed volume, only the EXCESS counts as ours, and a
    missing ladder is never treated as an empty queue.
    """
    t0 = _f(obs[i0].get("_poll_t"))
    ahead = queue_ahead(obs[i0], side_yes, px)
    if t0 is None or ahead is None:
        return {"filled": False, "why": "QUEUE_UNKNOWN_AT_PLACEMENT"}
    trades = ST.infer_trades(list(obs))
    for j in range(i0 + 1, len(obs)):
        t = _f(obs[j].get("_poll_t"))
        if t is None:
            continue
        if deadline is not None and t > t0 + deadline:
            return {"filled": False, "why": "DEADLINE_PASSED",
                    "waited_s": round(t - t0, 1)}
        through = EX._volume_through(trades, t0, t, side_yes, px)
        if through > ahead:
            got = min(float(qty), through - ahead)
            if got > 0:
                return {"filled": True, "t": t, "i": j,
                        "qty": round(got, 4), "latency_s": round(t - t0, 1),
                        "queue_ahead": round(ahead, 2),
                        "traded_through": round(through, 2)}
    return {"filled": False, "why": "NEVER_CLEARED_QUEUE",
            "queue_ahead": round(ahead, 2)}


def markout(obs: Sequence[Mapping[str, Any]], t_fill: float, side_yes: bool,
            mid_at_fill: D) -> Optional[float]:
    """Signed mid move 30 s after a fill: negative is adverse to the buyer."""
    for r in obs:
        t = _f(r.get("_poll_t"))
        if t is None or t < t_fill + MARKOUT_S:
            continue
        q = own_side(r, side_yes)
        if q is None:
            continue
        move = float(q["mid"] - mid_at_fill)
        return round(move if side_yes else -move, 5)
    return None


def pre_entry_drift(obs: Sequence[Mapping[str, Any]], i: int,
                    side_yes: bool) -> Optional[float]:
    """Mid drift over the PRECEDING window, signed against the side we would
    buy. Uses only rows at or before i, which is the whole point."""
    t = _f(obs[i].get("_poll_t"))
    cur = own_side(obs[i], side_yes)
    if t is None or cur is None:
        return None
    base = None
    for j in range(i, -1, -1):
        tj = _f(obs[j].get("_poll_t"))
        if tj is None:
            continue
        if t - tj >= DRIFT_WINDOW_S:
            base = own_side(obs[j], side_yes)
            break
    if base is None:
        return None
    move = float(cur["mid"] - base["mid"])
    return round(move if side_yes else -move, 5)


def _entry_side(row: Mapping[str, Any]) -> Optional[bool]:
    """The cheaper side, by own-side maker price. No hindsight."""
    y, n = own_side(row, True), own_side(row, False)
    if y is None or n is None:
        return None
    return bool(y["maker"] <= n["maker"])


# --------------------------------------------------------- the policies ---
def _pair_row(*, policy: str, slug: str, legs: List[dict],
              obs: Sequence[Mapping[str, Any]], fees: D,
              complete: bool, why: str) -> dict:
    """One replayed sequence, scored. Components are never pre-summed."""
    yes_q = sum(float(l["qty"]) for l in legs if l["side_yes"])
    no_q = sum(float(l["qty"]) for l in legs if not l["side_yes"])
    yes_c = sum(float(l["qty"]) * float(l["px"]) for l in legs
                if l["side_yes"])
    no_c = sum(float(l["qty"]) * float(l["px"]) for l in legs
               if not l["side_yes"])
    matched = min(yes_q, no_q)
    pair_cost = None
    if matched > 0 and yes_q > 0 and no_q > 0:
        pair_cost = round(yes_c / yes_q + no_c / no_q, 5)
    resid = round(yes_q - no_q, 4)
    t_first = min((l["t"] for l in legs), default=None)
    t_last = max((l["t"] for l in legs), default=None)
    t_end = _f(obs[-1].get("_poll_t")) if len(obs) else None
    # TIME AT RISK is from the first fill until the residual is closed, or
    # until the tape ends if it never is -- not until the last fill, which
    # would flatter every policy that fails to complete.
    if t_first is None:
        at_risk = None
    elif complete and t_last is not None:
        at_risk = round(t_last - t_first, 1)
    elif t_end is not None:
        at_risk = round(t_end - t_first, 1)
    else:
        at_risk = None
    advs = [l["markout"] for l in legs if l.get("markout") is not None]
    return {
        "policy": policy, "slug": slug,
        "legs": len(legs), "complete": bool(complete), "why": why,
        "matched_shares": round(matched, 4),
        "matched_pair_cost": pair_cost,
        "pair_at_or_below_ceiling": (None if pair_cost is None
                                     else bool(pair_cost <= float(CEILING))),
        "pair_under_one": (None if pair_cost is None else bool(pair_cost < 1)),
        "residual_shares_signed": resid,
        "fees_usd": str(fees),
        "time_at_risk_s": at_risk,
        "adverse_selection_t30": (round(sum(advs) / len(advs), 5)
                                  if advs else None),
        "fill_evidence": [{"side": "YES" if l["side_yes"] else "NO",
                           "px": str(l["px"]), "qty": l["qty"],
                           "latency_s": l.get("latency_s"),
                           "queue_ahead": l.get("queue_ahead"),
                           "traded_through": l.get("traded_through"),
                           "maker": l.get("maker", True)}
                          for l in legs],
        "LABEL": "REPLAY_NOT_REALIZED",
    }


def replay(slug: str, obs: Sequence[Mapping[str, Any]], policy: str) -> dict:
    """Replay one market under one policy. Entry is attempted ONCE, at the
    first observation where the rule admits it, so policies are compared on
    the same opportunity rather than on how many shots they take."""
    if len(obs) < 3:
        return {"policy": policy, "slug": slug, "skipped": "TAPE_TOO_SHORT"}
    fees = D("0")
    for i in range(len(obs) - 2):
        row = obs[i]
        side = _entry_side(row)
        if side is None:
            continue
        q = own_side(row, side)
        if q is None or q["maker"] <= 0 or q["maker"] > D("0.90"):
            continue

        if policy == "P5_SELECTIVE_ENTRY":
            d = pre_entry_drift(obs, i, side)
            if d is None or d < 0:
                # the side we would buy has been drifting against us over
                # the preceding window: skip, using only prior rows
                continue

        if policy == "P3_BOTH_SIDES_RESTING":
            qy, qn = own_side(row, True), own_side(row, False)
            fy = resting_fill(obs, i, True, qy["maker"], UNIT, None)
            fn = resting_fill(obs, i, False, qn["maker"], UNIT, None)
            legs = []
            for sy, f, qq in ((True, fy, qy), (False, fn, qn)):
                if f.get("filled"):
                    legs.append({"side_yes": sy, "px": qq["maker"],
                                 "qty": f["qty"], "t": f["t"],
                                 "latency_s": f["latency_s"],
                                 "queue_ahead": f["queue_ahead"],
                                 "traded_through": f["traded_through"],
                                 "maker": True,
                                 "markout": markout(obs, f["t"], sy,
                                                    qq["mid"])})
            if not legs:
                return {"policy": policy, "slug": slug,
                        "skipped": "NEITHER_SIDE_FILLED"}
            both = len(legs) == 2
            return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                             fees=fees, complete=both,
                             why="BOTH_LEGS_FILLED" if both
                             else "ONE_SIDED_FILL")

        first = resting_fill(obs, i, side, q["maker"], UNIT, None)
        if not first.get("filled"):
            continue
        legs = [{"side_yes": side, "px": q["maker"], "qty": first["qty"],
                 "t": first["t"], "latency_s": first["latency_s"],
                 "queue_ahead": first["queue_ahead"],
                 "traded_through": first["traded_through"], "maker": True,
                 "markout": markout(obs, first["t"], side, q["mid"])}]
        i_fill = first["i"]

        if policy == "P1_IMMEDIATE_TAKER":
            c = own_side(obs[i_fill], not side)
            if c is None:
                return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                                 fees=fees, complete=False,
                                 why="NO_COMPLEMENT_QUOTE_AT_FILL")
            qty = D(str(legs[0]["qty"]))
            fees = FEES.taker_fee(c["taker"], qty)
            legs.append({"side_yes": not side, "px": c["taker"],
                         "qty": float(qty), "t": _f(obs[i_fill]["_poll_t"]),
                         "latency_s": 0.0, "maker": False,
                         "markout": markout(obs, _f(obs[i_fill]["_poll_t"]),
                                            not side, c["mid"])})
            return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                             fees=fees, complete=True, why="TAKER_CROSSED")

        if policy in ("P2_DELAYED_MAKER", "P5_SELECTIVE_ENTRY"):
            c = own_side(obs[i_fill], not side)
            if c is None:
                return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                                 fees=fees, complete=False,
                                 why="NO_COMPLEMENT_QUOTE_AT_FILL")
            f2 = resting_fill(obs, i_fill, not side, c["maker"], UNIT,
                              MAX_WAIT_S)
            if f2.get("filled"):
                legs.append({"side_yes": not side, "px": c["maker"],
                             "qty": f2["qty"], "t": f2["t"],
                             "latency_s": f2["latency_s"],
                             "queue_ahead": f2["queue_ahead"],
                             "traded_through": f2["traded_through"],
                             "maker": True,
                             "markout": markout(obs, f2["t"], not side,
                                                c["mid"])})
                return _pair_row(policy=policy, slug=slug, legs=legs,
                                 obs=obs, fees=fees, complete=True,
                                 why="MAKER_COMPLETED")
            return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                             fees=fees, complete=False,
                             why="COMPLEMENT_%s" % f2.get("why"))

        if policy == "P4_ACCUMULATE_THEN_COMPLETE":
            j = i_fill
            while len([l for l in legs if l["side_yes"] == side]) < MAX_ACCUM:
                qj = own_side(obs[j], side)
                if qj is None or qj["maker"] > D("0.90"):
                    break
                fx = resting_fill(obs, j, side, qj["maker"], UNIT, MAX_WAIT_S)
                if not fx.get("filled"):
                    break
                legs.append({"side_yes": side, "px": qj["maker"],
                             "qty": fx["qty"], "t": fx["t"],
                             "latency_s": fx["latency_s"],
                             "queue_ahead": fx["queue_ahead"],
                             "traded_through": fx["traded_through"],
                             "maker": True,
                             "markout": markout(obs, fx["t"], side,
                                                qj["mid"])})
                j = fx["i"]
            held = sum(l["qty"] for l in legs if l["side_yes"] == side)
            c = own_side(obs[j], not side)
            if c is None:
                return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                                 fees=fees, complete=False,
                                 why="NO_COMPLEMENT_QUOTE")
            f2 = resting_fill(obs, j, not side, c["maker"], D(str(held)),
                              MAX_WAIT_S)
            if f2.get("filled"):
                legs.append({"side_yes": not side, "px": c["maker"],
                             "qty": f2["qty"], "t": f2["t"],
                             "latency_s": f2["latency_s"],
                             "queue_ahead": f2["queue_ahead"],
                             "traded_through": f2["traded_through"],
                             "maker": True,
                             "markout": markout(obs, f2["t"], not side,
                                                c["mid"])})
                return _pair_row(policy=policy, slug=slug, legs=legs,
                                 obs=obs, fees=fees,
                                 complete=f2["qty"] >= held,
                                 why="LOT_COMPLETED")
            return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                             fees=fees, complete=False,
                             why="LOT_%s" % f2.get("why"))

        if policy == "P6_INVENTORY_AWARE":
            # Complete whatever fits the budget for the CURRENT weighted
            # average, continuously, rather than once: the budget is
            # recomputed from inventory at every step and no step may use a
            # price from a later row.
            j = i_fill
            steps = 0
            while steps < 2 * MAX_ACCUM and j < len(obs) - 1:
                steps += 1
                yq = sum(l["qty"] for l in legs if l["side_yes"])
                nq = sum(l["qty"] for l in legs if not l["side_yes"])
                long_yes = yq > nq
                resid = abs(yq - nq)
                if resid <= 0:
                    break
                own = [l for l in legs if l["side_yes"] == long_yes]
                avg = D(str(sum(l["qty"] * float(l["px"]) for l in own)
                            / max(1e-9, sum(l["qty"] for l in own))))
                c = own_side(obs[j], not long_yes)
                if c is None:
                    j += 1
                    continue
                if c["maker"] <= CEILING - avg:
                    f2 = resting_fill(obs, j, not long_yes, c["maker"],
                                      D(str(resid)), MAX_WAIT_S)
                    if f2.get("filled"):
                        legs.append({"side_yes": not long_yes,
                                     "px": c["maker"], "qty": f2["qty"],
                                     "t": f2["t"],
                                     "latency_s": f2["latency_s"],
                                     "queue_ahead": f2["queue_ahead"],
                                     "traded_through": f2["traded_through"],
                                     "maker": True,
                                     "markout": markout(obs, f2["t"],
                                                        not long_yes,
                                                        c["mid"])})
                        j = f2["i"]
                        continue
                j += 1
            yq = sum(l["qty"] for l in legs if l["side_yes"])
            nq = sum(l["qty"] for l in legs if not l["side_yes"])
            return _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                             fees=fees, complete=abs(yq - nq) < 1e-9,
                             why="BUDGETED_COMPLETION")

        return {"policy": policy, "slug": slug, "skipped": "UNKNOWN_POLICY"}
    return {"policy": policy, "slug": slug, "skipped": "NO_ENTRY_TAKEN"}


# ------------------------------------------------------- the aggregation ---
def _pct(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return round(s[k], 5)


def summarize(rows: Sequence[Mapping[str, Any]], policy: str) -> dict:
    """One policy's executable economics over the replayed markets.

    NET EXPECTANCY per entry is reported, built from the pieces rather than
    asserted: a matched pair pays 1.00, so its contribution is
    (1 - pair_cost) * matched shares; an unclosed residual is scored at the
    only honest value available without a settlement feed -- UNKNOWN -- and
    counted separately, because a policy that leaves residuals is not
    cheaper, it is unmeasured.
    """
    mine = [r for r in rows if r.get("policy") == policy
            and "skipped" not in r]
    skipped = [r for r in rows if r.get("policy") == policy
               and "skipped" in r]
    if not mine:
        return {"policy": policy, "entries": 0,
                "skipped": len(skipped),
                "skip_reasons": _mix([r.get("skipped") for r in skipped])}
    costs = [r["matched_pair_cost"] for r in mine
             if r.get("matched_pair_cost") is not None]
    complete = [r for r in mine if r.get("complete")]
    resid = [abs(float(r.get("residual_shares_signed") or 0)) for r in mine]
    risk = [r["time_at_risk_s"] for r in mine
            if r.get("time_at_risk_s") is not None]
    adv = [r["adverse_selection_t30"] for r in mine
           if r.get("adverse_selection_t30") is not None]
    fees = sum(float(r.get("fees_usd") or 0) for r in mine)
    matched_pnl = sum((1.0 - r["matched_pair_cost"]) * r["matched_shares"]
                      for r in mine
                      if r.get("matched_pair_cost") is not None)
    n = len(mine)
    return {
        "policy": policy,
        "entries": n,
        "skipped": len(skipped),
        "skip_reasons": _mix([r.get("skipped") for r in skipped]),
        "completed_pairs": len(complete),
        "completion_rate": round(len(complete) / n, 4),
        "matched_pair_cost": {
            "n": len(costs), "min": _pct(costs, 0.0),
            "p25": _pct(costs, 0.25), "median": _pct(costs, 0.5),
            "p75": _pct(costs, 0.75), "max": _pct(costs, 1.0),
            "frac_at_or_below_ceiling": (
                round(sum(1 for c in costs if c <= float(CEILING))
                      / len(costs), 4) if costs else None),
            "frac_under_one": (round(sum(1 for c in costs if c < 1.0)
                                     / len(costs), 4) if costs else None)},
        "residual_shares": {"mean_abs": (round(sum(resid) / n, 4)),
                            "max_abs": (round(max(resid), 4) if resid
                                        else None),
                            "frac_left_open": round(
                                sum(1 for r in mine
                                    if not r.get("complete")) / n, 4)},
        "time_at_risk_s": {"median": _pct(risk, 0.5),
                           "p90": _pct(risk, 0.9)},
        "adverse_selection_t30": {"n": len(adv), "median": _pct(adv, 0.5),
                                  "mean": (round(sum(adv) / len(adv), 5)
                                           if adv else None)},
        "fees_usd_total": round(fees, 5),
        "matched_pair_pnl_usd": round(matched_pnl, 5),
        "net_per_entry_usd_matched_only": round((matched_pnl - fees) / n, 5),
        "UNRESOLVED_RESIDUAL": ("residual P&L requires a settlement feed the "
                                "public surface does not provide, so it is "
                                "NOT included in net_per_entry; a policy "
                                "that leaves residuals is unmeasured, not "
                                "cheap"),
        "LABEL": "REPLAY_NOT_REALIZED",
    }


def _mix(vals: Sequence[Any]) -> dict:
    out: Dict[str, int] = {}
    for v in vals:
        if v is None:
            continue
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def load_tape(state_dir: str, *, prefix: str = "cpc-",
              max_files: int = 4) -> Dict[str, List[dict]]:
    """Rebuild per-market observation sequences from the service's own tape.

    The observation files hold one row per watched market per cycle, so the
    sequences are reassembled by slug and sorted by poll time; duplicates
    from overlapping cycles are dropped on (slug, poll time).
    """
    out: Dict[str, Dict[float, dict]] = {}
    try:
        files = sorted(f for f in os.listdir(state_dir)
                       if f.startswith("observations-"))[-max_files:]
    except Exception:                                          # noqa: BLE001
        return {}
    for f in files:
        try:
            with open(os.path.join(state_dir, f)) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    for row in (rec.get("rows") or []):
                        slug = str(row.get("slug") or "")
                        if prefix and not slug.startswith(prefix):
                            continue
                        t = _f(row.get("_poll_t"))
                        if t is None:
                            continue
                        out.setdefault(slug, {})[t] = row
        except Exception:                                      # noqa: BLE001
            continue
    return {s: [v for _k, v in sorted(d.items())] for s, d in out.items()}


def settled_summary(rows: Sequence[Mapping[str, Any]],
                    settled: Mapping[str, Mapping[str, Any]],
                    policy: str) -> dict:
    """FULL-INVENTORY expectancy per entry, every residual included.

    Reported over the settlement-DECIDABLE subset, with the undecidable
    count beside it: dropping undecidable windows silently would bias the
    result toward whichever outcomes happen to converge inside our tape.
    """
    mine = [r for r in rows if r.get("policy") == policy
            and "skipped" not in r]
    decided, undec = [], 0
    for r in mine:
        o = settled.get(str(r.get("slug"))) or {}
        sr = settle_row(r, o)
        if sr.get("settled"):
            decided.append(sr)
        else:
            undec += 1
    nets = [d["net_usd"] for d in decided]
    pair = [d["matched_pair_pnl_usd"] for d in decided]
    res = [d["residual_pnl_usd"] for d in decided]
    losers = [x for x in res if x < 0]
    return {
        "policy": policy,
        "entries": len(mine),
        "settlement_decidable": len(decided),
        "settlement_undecidable": undec,
        "net_per_entry_usd_FULL_INVENTORY": _boot_ci(nets),
        "matched_pair_pnl_per_entry": _boot_ci(pair),
        "residual_pnl_per_entry": _boot_ci(res),
        "losing_residuals": {"n": len(losers),
                             "frac_of_decided": (round(len(losers)
                                                       / len(decided), 4)
                                                 if decided else None),
                             "total_usd": round(sum(losers), 5)},
        "fees_usd_total": round(sum(d["fees_usd"] for d in decided), 5),
        "total_net_usd": round(sum(nets), 5) if nets else None,
        "SETTLEMENT": "INFERRED_FROM_TERMINAL_BOOK, never venue-confirmed",
        "LABEL": "REPLAY_SETTLED_INFERRED_NOT_REALIZED",
    }


# ------------------------------------------- continuous placement (P7-P10) ---
def _at_or_after(obs: Sequence[Mapping[str, Any]], t: float,
                 frm: int) -> Optional[int]:
    for j in range(frm, len(obs)):
        tj = _f(obs[j].get("_poll_t"))
        if tj is not None and tj >= t:
            return j
    return None


def _place_px(row: Mapping[str, Any], side_yes: bool, off: int) -> Optional[D]:
    """Own-side resting price at `off` ticks from the own-side bid. A quote
    that would cross its own ask is not a resting quote and is refused."""
    q = own_side(row, side_yes)
    if q is None:
        return None
    px = q["maker"] + D(off) * TICK
    if px <= 0 or px >= (ONE - q["bid"] if not side_yes else q["ask"]):
        return None
    return px


def _budget(legs: List[dict], side_yes: bool) -> Optional[D]:
    """Highest price the complement may pay and still keep the PAIR at or
    below the ceiling, given what the long side has actually cost so far."""
    long_q = sum(D(str(l["qty"])) for l in legs if l["side_yes"] != side_yes)
    if long_q <= 0:
        return None
    long_c = sum(D(str(l["qty"])) * D(str(l["px"])) for l in legs
                 if l["side_yes"] != side_yes)
    return CEILING - (long_c / long_q)


def replay_placement(slug: str, obs: Sequence[Mapping[str, Any]],
                     policy: str) -> dict:
    """Re-quote across the whole window; one knob is WHERE the quote rests.

    Entry is not one-shot. After each fill or each unfilled requote interval
    the quote is replaced from the book as it then stands, so the number of
    fills is an OUTPUT of PMUS flow rather than a cap imposed by the harness.
    """
    off, two_sided = PLACEMENT[policy]
    if len(obs) < 3:
        return {"policy": policy, "slug": slug, "skipped": "TAPE_TOO_SHORT"}
    legs: List[dict] = []
    requotes = 0
    unfilled = 0
    i = 0
    while i < len(obs) - 2:
        t0 = _f(obs[i].get("_poll_t"))
        if t0 is None:
            i += 1
            continue
        yes_q = sum(D(str(l["qty"])) for l in legs if l["side_yes"])
        no_q = sum(D(str(l["qty"])) for l in legs if not l["side_yes"])
        resid = yes_q - no_q

        # which sides do we rest this round?
        sides: List[bool] = []
        if two_sided:
            sides = [True, False]
        elif resid != 0:
            # inventory-aware: work the complement of what we are long, and
            # EXTEND the long side too while the imbalance is within bounds.
            # Refusing to extend would hard-code away 70.8% of L5's observed
            # actions, so the bound is a risk limit, not a behavioural claim.
            sides = [resid < 0]
            if abs(resid) < MAX_EXTEND:
                sides.append(resid > 0)
        else:
            s0 = _entry_side(obs[i])
            if s0 is None:
                i += 1
                continue
            sides = [s0]

        best: Optional[dict] = None
        for sy in sides:
            px = _place_px(obs[i], sy, off)
            if px is None or px > D("0.90"):
                continue
            # a side we are already long of may not be extended past the
            # point where the pair can still close under the ceiling
            if (sy and resid > 0) or ((not sy) and resid < 0):
                # extending the long side: the new average must still leave
                # at least one tick of room under the pair ceiling
                lq = sum(D(str(l["qty"])) for l in legs if l["side_yes"] == sy)
                lc = sum(D(str(l["qty"])) * D(str(l["px"])) for l in legs
                         if l["side_yes"] == sy)
                if lq + UNIT > 0 and (lc + px * UNIT) / (lq + UNIT) > \
                        CEILING - TICK:
                    continue
            else:
                cap = _budget(legs, sy)
                if cap is not None and px > cap:
                    continue
            requotes += 1
            f = resting_fill(obs, i, sy, px, UNIT, REQUOTE_S)
            if not f.get("filled"):
                unfilled += 1
                continue
            if best is None or f["i"] < best["f"]["i"]:
                best = {"f": f, "sy": sy, "px": px,
                        "mid": own_side(obs[i], sy)["mid"]}

        if best is None:
            nxt = _at_or_after(obs, t0 + REQUOTE_S, i + 1)
            if nxt is None or nxt <= i:
                break
            i = nxt
            continue

        f, sy = best["f"], best["sy"]
        legs.append({"side_yes": sy, "px": best["px"], "qty": f["qty"],
                     "t": f["t"], "latency_s": f["latency_s"],
                     "queue_ahead": f["queue_ahead"],
                     "traded_through": f["traded_through"], "maker": True,
                     "markout": markout(obs, f["t"], sy, best["mid"])})
        i = max(f["i"], i + 1)

    if not legs:
        return {"policy": policy, "slug": slug, "skipped": "NO_FILL",
                "requotes": requotes}
    yes_q = sum(float(l["qty"]) for l in legs if l["side_yes"])
    no_q = sum(float(l["qty"]) for l in legs if not l["side_yes"])
    row = _pair_row(policy=policy, slug=slug, legs=legs, obs=obs,
                    fees=D("0"), complete=(yes_q > 0 and no_q > 0),
                    why=("BOTH_SIDES_FILLED" if yes_q > 0 and no_q > 0
                         else "ONE_SIDED_ONLY"))
    row["requotes"] = requotes
    row["requotes_unfilled"] = unfilled
    row["fill_rate_per_requote"] = (round(len(legs) / requotes, 4)
                                    if requotes else None)
    # time from each long-side fill to the first complement fill after it
    waits = []
    comp = sorted(l["t"] for l in legs if not l["side_yes"]) or []
    for l in legs:
        if not l["side_yes"]:
            continue
        nxt = next((t for t in comp if t > l["t"]), None)
        if nxt is not None:
            waits.append(nxt - l["t"])
    row["time_to_complement_s_median"] = (round(sorted(waits)[len(waits) // 2],
                                                1) if waits else None)
    row["matched_frac_of_purchased"] = (
        round(2 * min(yes_q, no_q) / (yes_q + no_q), 4)
        if (yes_q + no_q) else None)
    return row


def study(obs_by_slug: Mapping[str, Sequence[Mapping[str, Any]]],
          policies: Sequence[str] = ALL_POLICIES) -> dict:
    """Ranked comparison, in-sample and out-of-sample by market TIME.

    The split is by the market's own first observation, so the
    out-of-sample half is strictly later wall-clock data and no policy can
    be tuned on it -- none is tuned at all here, but the split is what makes
    that checkable.
    """
    slugs = sorted(obs_by_slug,
                   key=lambda s: _f(obs_by_slug[s][0].get("_poll_t")) or 0)
    cut = len(slugs) // 2
    halves = {"in_sample": slugs[:cut], "out_of_sample": slugs[cut:]}
    out: Dict[str, Any] = {
        "markets": len(slugs),
        "observations": sum(len(v) for v in obs_by_slug.values()),
        "ceiling": str(CEILING),
        "max_wait_s": MAX_WAIT_S, "max_accumulate": MAX_ACCUM,
        "RULES": ["chronological", "no assumed fills",
                  "queue at placement must be cleared by printed volume",
                  "no retroactive pricing", "fees charged where we cross",
                  "residual P&L excluded for want of a settlement feed"],
        "splits": {},
    }
    # settlement is inferred ONCE per market and shared by every policy, so
    # no policy can be scored against a different outcome than its rivals
    settled = {s: infer_settlement(obs_by_slug[s]) for s in slugs}
    out["settlement_coverage"] = {
        "markets": len(settled),
        "decidable": sum(1 for v in settled.values() if v.get("resolved")),
        "undecidable": sum(1 for v in settled.values()
                           if not v.get("resolved")),
        "bases": _mix([v.get("basis") for v in settled.values()]),
        "why_undecidable": _mix([v.get("why") for v in settled.values()]),
        "SOURCE": ("the market's own terminal prices; PMUS settles on CF "
                   "BRTI and no BRTI history exists in this repository"),
        "LABEL": "SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK",
    }
    for name, group in halves.items():
        rows: List[dict] = []
        for s in group:
            for p in policies:
                rows.append(replay_placement(s, obs_by_slug[s], p)
                            if p in PLACEMENT
                            else replay(s, obs_by_slug[s], p))
        sums = [summarize(rows, p) for p in policies]
        full = {p: settled_summary(rows, settled, p) for p in policies}
        ranked = sorted(
            [s for s in sums if s.get("entries")],
            key=lambda d: (-(d["matched_pair_cost"]["frac_under_one"] or 0),
                           d["matched_pair_cost"]["median"]
                           if d["matched_pair_cost"]["median"] is not None
                           else 9))
        out["splits"][name] = {
            "markets": len(group),
            "full_inventory_after_settlement": full,
            "by_policy": {s["policy"]: s for s in sums},
            "ranking": [{"policy": s["policy"],
                         "entries": s["entries"],
                         "completion_rate": s["completion_rate"],
                         "median_pair_cost": s["matched_pair_cost"]["median"],
                         "frac_under_one":
                             s["matched_pair_cost"]["frac_under_one"],
                         "frac_at_or_below_ceiling":
                             s["matched_pair_cost"]["frac_at_or_below_ceiling"],
                         "net_per_entry_usd_matched_only":
                             s["net_per_entry_usd_matched_only"],
                         "net_per_entry_usd_FULL_INVENTORY":
                             (full.get(s["policy"]) or {}).get(
                                 "net_per_entry_usd_FULL_INVENTORY")}
                        for s in ranked],
        }
    return out


# ------------------------------------------- settlement, from our own tape --
# WHY NOT A REFERENCE PRICE. PMUS settles BTC15m on CF Benchmarks BRTI --
# "Up iff BRTI(T_end) >= BRTI(T_start)", a 60-price average to 2 dp -- which
# `analysis/pmus/P24_PMUS_BTC15M.md` documents and `src/l5_venue_adapter.py`
# carries as contract metadata. The repository holds NO BRTI history: what
# exists is a Coinbase 1-minute series explicitly labelled
# PROXY_NOT_SETTLEMENT_INDEX covering 2026-08-20..25, and Gemini's
# Kaiko-RFR strike recordings from 2026-08-31 whose own status file reads
# Z_STATUS: NOT_IDENTIFIED. Neither is BRTI and neither covers the replay
# windows. No external price host is reachable from the analysis container.
#
# WHAT IS AVAILABLE INSTEAD. A binary market converges at expiry: the
# winning side's own-side price goes to 1.00 and the loser's to 0.00. Our
# own tape polls each window to its end, so the market's TERMINAL state
# names the outcome without any external index. That is an inference from
# prices, not a venue resolution, so it is labelled
# SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK everywhere it is used, and a
# window whose terminal state is not decisive is UNDECIDABLE rather than
# guessed.
SETTLE_TAIL_S = float(os.getenv("L5_STUDY_SETTLE_TAIL_S", "120"))
SETTLE_HI = D(os.getenv("L5_STUDY_SETTLE_HI", "0.95"))
SETTLE_LO = D(os.getenv("L5_STUDY_SETTLE_LO", "0.05"))


def infer_settlement(obs: Sequence[Mapping[str, Any]],
                     window_end: Optional[float] = None) -> dict:
    """Outcome from the market's own terminal prices, or UNDECIDABLE.

    Evidence, in order: the last two-sided mid inside the tail window, then
    the last trade price. Both must be decisive (>= 0.95 or <= 0.05); a mid
    of 0.62 at expiry means our tape stopped before convergence and the
    honest answer is that we do not know.
    """
    if not obs:
        return {"resolved": False, "why": "NO_TAPE"}
    t_last = _f(obs[-1].get("_poll_t")) or 0.0
    cutoff = (window_end if window_end is not None else t_last) - SETTLE_TAIL_S
    tail = [r for r in obs if (_f(r.get("_poll_t")) or 0) >= cutoff]
    if not tail:
        tail = obs[-1:]
    for r in reversed(tail):
        q = own_side(r, True)
        if q is None:
            continue
        mid = q["mid"]
        if mid >= SETTLE_HI:
            return {"resolved": True, "yes_won": True, "basis": "terminal_mid",
                    "terminal_mid": str(mid),
                    "LABEL": "SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK"}
        if mid <= SETTLE_LO:
            return {"resolved": True, "yes_won": False,
                    "basis": "terminal_mid", "terminal_mid": str(mid),
                    "LABEL": "SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK"}
        break
    for r in reversed(tail):
        lp = _f(r.get("last_trade_px"))
        if lp is None:
            continue
        if lp >= float(SETTLE_HI):
            return {"resolved": True, "yes_won": True,
                    "basis": "terminal_last_trade", "last_trade_px": lp,
                    "LABEL": "SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK"}
        if lp <= float(SETTLE_LO):
            return {"resolved": True, "yes_won": False,
                    "basis": "terminal_last_trade", "last_trade_px": lp,
                    "LABEL": "SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK"}
        break
    return {"resolved": False,
            "why": "TERMINAL_STATE_NOT_DECISIVE",
            "LABEL": "SETTLEMENT_UNDECIDABLE"}


def settle_row(row: Mapping[str, Any], outcome: Mapping[str, Any]) -> dict:
    """FULL-INVENTORY P&L for one replayed sequence: pairs AND residual.

    Matched shares pay exactly 1.00 whichever way the market resolves. The
    residual pays 1.00 or 0.00 on the inferred outcome, at its own cost
    basis -- so every losing residual is counted, which is the whole point
    of this stage. Fees are whatever the policy actually incurred.
    """
    out: Dict[str, Any] = {
        "policy": row.get("policy"), "slug": row.get("slug"),
        "fees_usd": float(row.get("fees_usd") or 0),
        "LABEL": "REPLAY_SETTLED_INFERRED_NOT_REALIZED",
    }
    legs = row.get("fill_evidence") or []
    yes_q = sum(float(l["qty"]) for l in legs if l["side"] == "YES")
    no_q = sum(float(l["qty"]) for l in legs if l["side"] == "NO")
    yes_c = sum(float(l["qty"]) * float(l["px"]) for l in legs
                if l["side"] == "YES")
    no_c = sum(float(l["qty"]) * float(l["px"]) for l in legs
               if l["side"] == "NO")
    matched = min(yes_q, no_q)
    pair_pnl = 0.0
    if matched > 0 and yes_q > 0 and no_q > 0:
        pair_pnl = matched * (1.0 - (yes_c / yes_q + no_c / no_q))
    out["matched_shares"] = round(matched, 4)
    out["matched_pair_pnl_usd"] = round(pair_pnl, 5)
    resid = yes_q - no_q
    out["residual_shares_signed"] = round(resid, 4)
    if not outcome.get("resolved"):
        out["settled"] = False
        out["why"] = outcome.get("why")
        return out
    yes_won = bool(outcome["yes_won"])
    resid_pnl = 0.0
    if abs(resid) > 1e-9:
        long_yes = resid > 0
        qty = abs(resid)
        avg = (yes_c / yes_q) if long_yes else (no_c / no_q)
        payout = 1.0 if (long_yes == yes_won) else 0.0
        resid_pnl = qty * (payout - avg)
    out.update({
        "settled": True, "yes_won": yes_won,
        "settlement_basis": outcome.get("basis"),
        "residual_pnl_usd": round(resid_pnl, 5),
        "net_usd": round(pair_pnl + resid_pnl - out["fees_usd"], 5),
    })
    return out


def _boot_ci(xs: Sequence[float], iters: int = 2000,
             seed: int = 11) -> Optional[dict]:
    """Bootstrap 90% interval on the mean. Small n is the reason this is
    here: a point estimate over 6 entries reads as knowledge."""
    if not xs:
        return None
    import random
    rng = random.Random(seed)
    n = len(xs)
    means = []
    for _ in range(iters):
        means.append(sum(xs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return {"n": n, "mean": round(sum(xs) / n, 5),
            "p05": round(means[int(0.05 * iters)], 5),
            "p95": round(means[int(0.95 * iters)], 5)}
