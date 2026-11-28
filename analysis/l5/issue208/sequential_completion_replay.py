"""SEQUENTIAL completion: one leg fills, then the other is repriced (#208).

WHY THIS IS A DIFFERENT EXPERIMENT FROM `queue_scenarios.py`, and why the
earlier numbers must not be carried across. That harness rests BOTH legs at
once and scores the pair they were quoted at. ROUTE B faces something else
entirely:

    leg A fills at a realized price -> the book has MOVED -> the complement
    is priced against the NEW touch -> the pair is whatever those two
    realized prices add up to, which can exceed the opening ceiling.

That is where a 0.98 pair comes from, and no simultaneous model produces
one. It is also why "the touch failed the conservative arm" in the
simultaneous replay says nothing directly about the completion depth: the
alternative there was a cheaper simultaneous pair, and the alternative here
is carrying an unmatched leg.

WHAT IS COMPARED, on the SAME inventory at the SAME frame, which is the
owner's requirement:

  COMPLETE   rest the complement at `d_complete` behind the new touch and
             walk the ladder forward for its fill.
  REPRICE    the same, one tick cheaper -- the downward-only move the
             controller actually permits. Modelled as its own depth so the
             two can be read side by side rather than argued about.
  HOLD       carry the unmatched leg to settlement at the measured
             -0.0236/contract.
  REDUCE     cross the spread to sell the leg back, paying the taker fee.
             A real alternative and the only one with a certain outcome.

EVERY FILL IS STILL A BOUND. There is no trade-print series (see
`src/l5_queue_depletion`), so leg fills are PROVEN / POSSIBLE / IMPOSSIBLE
under a stated queue assumption, exactly as before. Nothing here is a
realized result and no pair is credited as completed.

NOTHING HERE CALLS THE HISTORICAL CANCELS MISTAKES. Those orders faced
their own inventory, prices and runway; this harness is a different
population and is not evidence about them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src import l5_queue_depletion as QD          # noqa: E402
import queue_scenarios as QS                      # noqa: E402

TICK = QS.TICK
UP, DOWN = QS.UP, QS.DOWN

#: BC.decide is the DEPLOYED policy and is what must be replayed. An
#: unconditional "complete at the touch" harness completed pairs averaging
#: 1.06 -- a guaranteed loss the real controller refuses outright -- and
#: reported completion as worse than holding on that basis. The gate is part
#: of the strategy, so it is called rather than imitated.
os.environ.setdefault("L5_BOOK_COMPLETION", "1")

WINDOW_S = 900.0

#: THE DEPLOYED DECISION FUNCTION CANNOT BE REPLAYED ON THIS DATA, and the
#: reason is measured rather than guessed. `BC.decide` requires
#: `stream_age_s <= TS.MAX_BOOK_AGE_S = 2.0`, and the median frame gap in
#: every ladder recording in the repository is 6.4-6.9 s, so an honest book
#: age is always about three times the freshness limit. Asked at each frame
#: of the out-of-sample set it returned STALE_BOOK 55 times and
#: PAST_TERMINAL 201 times out of 265 sequential cases -- and NOT ONCE for
#: an economic reason. Nothing about pair economics was being measured.
#:
#: So there are two gates, and which one ran is always reported:
#:
#:   "controller"  call BC.decide. Faithful, and BLOCKED on this data. Kept
#:                 so the blocker stays reproducible instead of becoming a
#:                 remark in a document.
#:   "econ"        an EXPLICIT rule standing in for the rescue branch:
#:                 complete iff the pair beats carrying the leg. This is a
#:                 MODEL OF the policy and not the policy, and every number
#:                 produced under it is labelled that way.
GATES = ("econ", "controller")

#: published taker fee coefficient, used only for the REDUCE alternative.
TAKER_THETA = D("0.0695")

#: completion depths compared. 0 is what ROUTE B uses today.
COMPLETE_DEPTHS = (0, 1, 2)

#: TTL on the COMPLETION order only. None holds to the end of the recording.
TTLS: Tuple[Optional[float], ...] = (None, 120.0, 60.0)


def window_start_of(slug: str, fallback: float) -> float:
    """Epoch of the 15-minute window named in the slug.

    `cpc-btc-updown-15m-2026-11-06-1130z` -> that minute, UTC. The horizon
    and time-left checks inside `BC.decide` are measured from it, so a
    fallback to the first frame would make every entry look early.
    """
    import re
    from datetime import datetime, timezone
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})z", slug or "")
    if not m:
        return fallback
    y, mo, d, hh, mm = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d, hh, mm,
                        tzinfo=timezone.utc).timestamp()
    except BaseException:                                      # noqa: BLE001
        return fallback


def book_for_decide(frame: Mapping[str, Any],
                    age_s: float) -> Optional[Dict[str, Any]]:
    """The ladder frame in the shape `BC.decide` reads.

    `stream_age_s` is the real gap to the previous frame, so a stale book is
    stale here too rather than being presented as fresh.
    """
    tt = QS.touches(frame)
    if tt is None:
        return None
    bid, ask = tt
    return {"bid": str(bid), "ask": str(ask), "spread": str(ask - bid),
            "stream_age_s": age_s, "age_s": age_s,
            "observed_t": float(frame["t"]),
            "last_update_t": float(frame["t"])}


def taker_fee(price: Any, qty: Any = D(1)) -> D:
    """theta * C * p * (1 - p), the published form."""
    p, q = QD._d(price), QD._d(qty)
    if p is None or q is None:
        return D(0)
    return (TAKER_THETA * q * p * (D(1) - p)).quantize(D("0.000001"))


def reduce_outcome(held_side: str, own_paid: D, qty: D,
                   frame: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Sell the held leg back by crossing. Certain, and usually a loss.

    A held UP is sold by hitting the BID, receiving `bid` per contract. A
    held DOWN is sold by buying YES at the ASK, i.e. receiving `1 - ask` in
    own terms. The taker fee is charged on the WIRE price, which is what the
    schedule is defined on.
    """
    tt = QS.touches(frame)
    if tt is None:
        return None
    bid, ask = tt
    if held_side == UP:
        recv, wire = bid, bid
    else:
        recv, wire = (D(1) - ask), ask
    fee = taker_fee(wire, qty)
    return {"action": "REDUCE", "receive_own": str(recv),
            "fee": str(fee),
            "pnl": str(((recv - own_paid) * qty - fee)
                       .quantize(D("0.000001")))}


def sequential(sources: Sequence[Tuple[str, str]], *, entry_depth: int,
               complete_depth: int, scenario: Tuple[str, str, D],
               qty: D = D(1), completion_ttl_s: Optional[float] = None,
               rebate_per_fill: D = D(0), residual_budget_usd: str = "1.00",
               gate: str = "econ", root: str = "") -> List[Dict[str, Any]]:
    """Both legs quoted at entry; whichever fills alone drives a completion.

    If BOTH legs fill the outcome is the simultaneous pair and is labelled
    `PAIR_AT_ENTRY`, kept separate so it cannot be credited to the
    completion logic. The sequential case is exactly one leg filling.
    """
    from src import l5_book_completion as BC
    name, arm, frac = scenario
    rows: List[Dict[str, Any]] = []
    for path, shape in sources:
        full = os.path.join(root, path) if root else path
        if not os.path.exists(full):
            continue
        for slug, frames in sorted(QS.load(full, shape).items()):
            for i in range(len(frames) - 1):
                tt = QS.touches(frames[i])
                if tt is None:
                    continue
                bid, ask = tt
                up_own = bid - entry_depth * TICK
                dn_own = (D(1) - ask) - entry_depth * TICK
                if up_own <= 0 or dn_own <= 0:
                    continue
                up = QS.leg_outcome(UP, up_own, frames, start=i,
                                    queue_fraction=frac, own_qty=qty,
                                    ttl_s=None)
                dn = QS.leg_outcome(DOWN, dn_own, frames, start=i,
                                    queue_fraction=frac, own_qty=qty,
                                    ttl_s=None)
                uf = QS.filled(up, arm=arm)
                df = QS.filled(dn, arm=arm)
                if uf is None or df is None:
                    continue
                base = {"source": os.path.basename(path), "slug": slug,
                        "frame": i, "scenario": name, "gate": gate,
                        "entry_depth": entry_depth,
                        "complete_depth": complete_depth,
                        "completion_ttl_s": completion_ttl_s}
                if uf and df:
                    pair = up_own + dn_own
                    rows.append(dict(base, state="PAIR_AT_ENTRY",
                                     pair_cost=str(pair),
                                     pnl=str(((D(1) - pair) * qty
                                              + rebate_per_fill * qty * 2)
                                             .quantize(D("0.000001")))))
                    continue
                if not uf and not df:
                    rows.append(dict(base, state="NO_FILL", pnl="0"))
                    continue
                # ---------- THE SEQUENTIAL CASE ----------
                held = UP if uf else DOWN
                paid = up_own if uf else dn_own
                want = DOWN if uf else UP
                leg = up if uf else dn
                j = leg.get("fill_index")
                if j is None or j >= len(frames) - 1:
                    # no frame left to quote a complement into. HOLD and
                    # REDUCE are still the alternatives and are still
                    # reported, so the comparison columns cover every
                    # sequential row rather than a subset of them.
                    alt0 = reduce_outcome(held, paid, qty,
                                          frames[min(j or i,
                                                     len(frames) - 1)]) or {}
                    rows.append(dict(
                        base, state="RESIDUAL_NO_RUNWAY", held_side=held,
                        held_px=str(paid),
                        hold_pnl=str((QS.RESIDUAL_PER_CONTRACT * qty)
                                     .quantize(D("0.000001"))),
                        reduce_pnl=alt0.get("pnl", ""),
                        pnl=str((QS.RESIDUAL_PER_CONTRACT * qty)
                                .quantize(D("0.000001")))))
                    continue
                alt = reduce_outcome(held, paid, qty, frames[j]) or {}
                hold_pnl = (QS.RESIDUAL_PER_CONTRACT * qty).quantize(
                    D("0.000001"))
                rec = dict(base, held_side=held, held_px=str(paid),
                           complement_side=want, first_fill_frame=j,
                           frames_left=len(frames) - 1 - j,
                           hold_pnl=str(hold_pnl),
                           reduce_pnl=alt.get("pnl", ""))
                if gate == "econ":
                    k, comp_own, reason = _econ_gate(
                        want, paid, qty, frames, j, complete_depth,
                        completion_ttl_s)
                    rec["decision"] = ("COMPLETE" if comp_own is not None
                                       else "HOLD")
                    rec["decision_reason"] = reason
                    rec["quoted_at_frame"] = k if comp_own is not None else None
                    rec["wait_s"] = (round(float(frames[k]["t"])
                                           - float(frames[j]["t"]), 1)
                                     if comp_own is not None else None)
                    if comp_own is None:
                        rows.append(dict(rec,
                                         state="DECLINED_BY_CONTROLLER",
                                         pnl=str(hold_pnl)))
                        continue
                    pair = paid + comp_own
                    comp = QS.leg_outcome(want, comp_own, frames, start=k,
                                          queue_fraction=frac, own_qty=qty,
                                          ttl_s=completion_ttl_s)
                    cf = QS.filled(comp, arm=arm)
                    rec.update(complement_px=str(comp_own),
                               pair_cost=str(pair),
                               complement_verdict=comp.get("verdict"),
                               complement_queue_ahead=comp.get("queue_ahead"))
                    if cf:
                        rec.update(state="PAIR_SEQUENTIAL",
                                   pnl=str(((D(1) - pair) * qty
                                            + rebate_per_fill * qty * 2)
                                           .quantize(D("0.000001"))))
                    else:
                        rec.update(state="RESIDUAL",
                                   pnl=str((QS.RESIDUAL_PER_CONTRACT * qty
                                            + rebate_per_fill * qty)
                                           .quantize(D("0.000001"))))
                    rows.append(rec)
                    continue
                # ---- THE CONTROLLER IS ASKED EVERY FRAME, NOT ONCE ----
                #
                # ROUTE B re-decides every `DECIDE_EVERY_S = 5.0` s, which
                # is the frame interval of these recordings. Asking once at
                # the instant of the fill is the WRONG model and reads as a
                # 97% decline rate, because that instant is the most adverse
                # one available: a leg fails to fill precisely when the book
                # moved away from it. What the scheduler actually does is
                # keep asking until the book comes back.
                ws = window_start_of(slug, float(frames[0]["t"]))
                k = j
                dec = None
                leg_q = None
                asked = 0
                while k < len(frames) - 1:
                    if (completion_ttl_s is not None
                            and float(frames[k]["t"]) - float(frames[j]["t"])
                            > completion_ttl_s):
                        break        # no time left to even place it
                    age = (float(frames[k]["t"])
                           - float(frames[k - 1]["t"])) if k else 0.0
                    bk = book_for_decide(frames[k], age)
                    if bk is None:
                        k += 1
                        continue
                    asked += 1
                    dec = BC.decide(
                        market=slug.split("|")[0], held_side=held,
                        held_qty=str(qty), held_cost_usd=str(paid * qty),
                        deficient_qty="0", deficient_cost_usd="0", book=bk,
                        now_t=float(frames[k]["t"]), window_start=ws,
                        resting=(), tick="0.01", min_qty="0.01",
                        depth_ticks=complete_depth,
                        residual_budget_usd=residual_budget_usd,
                        submissions_left=2, outstanding_qty="0",
                        last_decide_t=None)
                    leg_q = (dec.get("leg") or {}).get("price")
                    if dec.get("action") in ("COMPLETE", "REPRICE") \
                            and leg_q is not None:
                        break
                    leg_q = None
                    k += 1
                rec["decision"] = (dec or {}).get("action")
                rec["decision_reason"] = (dec or {}).get("reason")
                rec["decisions_asked"] = asked
                rec["quoted_at_frame"] = k if leg_q is not None else None
                rec["wait_s"] = (round(float(frames[k]["t"])
                                       - float(frames[j]["t"]), 1)
                                 if leg_q is not None else None)
                if leg_q is None:
                    rows.append(dict(rec, state="DECLINED_BY_CONTROLLER",
                                     pnl=str(hold_pnl)))
                    continue
                comp_own = D(str(leg_q))
                pair = paid + comp_own
                comp = QS.leg_outcome(want, comp_own, frames, start=k,
                                      queue_fraction=frac, own_qty=qty,
                                      ttl_s=completion_ttl_s)
                cf = QS.filled(comp, arm=arm)
                rec.update(complement_px=str(comp_own), pair_cost=str(pair),
                           complement_verdict=comp.get("verdict"),
                           complement_queue_ahead=comp.get("queue_ahead"))
                if cf:
                    rec.update(state="PAIR_SEQUENTIAL",
                               pnl=str(((D(1) - pair) * qty
                                        + rebate_per_fill * qty * 2)
                                       .quantize(D("0.000001"))))
                else:
                    rec.update(state="RESIDUAL",
                               pnl=str((QS.RESIDUAL_PER_CONTRACT * qty
                                        + rebate_per_fill * qty)
                                       .quantize(D("0.000001"))))
                rows.append(rec)
    return rows


def _econ_gate(want: str, paid: D, qty: D,
               frames: Sequence[Mapping[str, Any]], j: int,
               depth: int, ttl_s: Optional[float]):
    """Wait for a complement that beats carrying, then quote it.

    THE RULE, stated so it can be disagreed with: complete only if the pair
    the complement would make is worth more than the measured cost of
    carrying the leg, i.e. `1 - pair > RESIDUAL_PER_CONTRACT`, which is
    `pair < 1.0236`. Asked once a frame, exactly as the 5 s cadence does,
    until the TTL. This is the economic content of the rescue branch and
    nothing else: no ceiling, no budget, no claim, no slot.
    """
    limit = D(1) - QS.RESIDUAL_PER_CONTRACT          # 1.0236
    for k in range(j, len(frames) - 1):
        if (ttl_s is not None
                and float(frames[k]["t"]) - float(frames[j]["t"]) > ttl_s):
            return k, None, "TTL_BEFORE_A_WORTHWHILE_COMPLEMENT"
        tt = QS.touches(frames[k])
        if tt is None:
            continue
        bid, ask = tt
        own = ((bid if want == UP else (D(1) - ask)) - depth * TICK)
        if own <= 0:
            continue
        if paid + own < limit:
            return k, own, "PAIR_BEATS_CARRY_MODELLED"
    return len(frames) - 1, None, "NO_COMPLEMENT_EVER_BEAT_CARRY"


def summarise(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    keys = sorted({(r["scenario"], r["complete_depth"],
                    r["completion_ttl_s"]) for r in rows},
                  key=lambda k: (k[1], k[2] or 1e9,
                                 [n for n, _, _ in QS.SCENARIOS].index(k[0])))
    for scen, cd, ttl in keys:
        sel = [r for r in rows if r["scenario"] == scen
               and r["complete_depth"] == cd and r["completion_ttl_s"] == ttl]
        seq = [r for r in sel
               if r["state"] in ("PAIR_SEQUENTIAL", "RESIDUAL",
                                 "RESIDUAL_NO_RUNWAY",
                                 "DECLINED_BY_CONTROLLER")]
        if not seq:
            continue
        pairs = [r for r in seq if r["state"] == "PAIR_SEQUENTIAL"]
        resid = [r for r in seq if r["state"] != "PAIR_SEQUENTIAL"]
        cheap = [r for r in pairs if D(r["pair_cost"]) <= D("0.96")]
        dear = [r for r in pairs if D(r["pair_cost"]) > D("0.96")]
        # D(0) starts every sum: an empty selection makes `sum` return the
        # int 0, which has no `quantize`.
        pnl = sum((D(r["pnl"]) for r in seq), D(0))
        # the alternatives, on the SAME inventory
        hold = sum((D(r["hold_pnl"]) for r in seq if r.get("hold_pnl")), D(0))
        red = sum((D(r["reduce_pnl"]) for r in seq if r.get("reduce_pnl")),
                  D(0))
        out.append({"scenario": scen, "complete_depth": cd,
                    "ttl": "hold" if ttl is None else ("%.0fs" % ttl),
                    "sequential": len(seq), "pairs": len(pairs),
                    "pairs_le_096": len(cheap), "pairs_gt_096": len(dear),
                    "residual": len(resid),
                    "declined": sum(1 for r in seq
                                    if r["state"]
                                    == "DECLINED_BY_CONTROLLER"),
                    "completion_rate": ("%.1f%%" % (100.0 * len(pairs)
                                                    / len(seq))),
                    "complete_pnl": str(pnl.quantize(D("0.0001"))),
                    "hold_pnl": str(hold.quantize(D("0.0001"))),
                    "reduce_pnl": str(red.quantize(D("0.0001"))),
                    "complete_per_leg": str((pnl / D(len(seq)))
                                            .quantize(D("0.000001")))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=("oos", "in"), default="oos")
    ap.add_argument("--entry-depth", type=int, default=2)
    ap.add_argument("--rebate", default="0")
    ap.add_argument("--gate", choices=GATES, default="econ")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    sources = QS.OUT_OF_SAMPLE if a.group == "oos" else QS.IN_SAMPLE
    rows: List[Dict[str, Any]] = []
    for cd in COMPLETE_DEPTHS:
        for ttl in TTLS:
            for scen in QS.SCENARIOS:
                rows += sequential(sources, entry_depth=a.entry_depth,
                                   complete_depth=cd, scenario=scen,
                                   completion_ttl_s=ttl, gate=a.gate,
                                   rebate_per_fill=D(a.rebate), root=root)
    print("SEQUENTIAL COMPLETION   %s   entry depth %d   rebate/contract %s"
          % ("OUT OF SAMPLE" if a.group == "oos" else "IN SAMPLE",
             a.entry_depth, a.rebate))
    print("gate: %s%s" % (a.gate, "  -- A MODEL OF the rescue rule, not the"
                          " deployed decision function"
                          if a.gate == "econ" else
                          "  -- BC.decide; BLOCKED on this data, see GATES"))
    print("one leg filled, then the complement priced against the NEW touch")
    print()
    hdr = ("  cd ttl   scenario      seq  pairs  <=.96  >.96 resid declin "
           "rate   POLICY      HOLD      REDUCE   policy/leg")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for s in summarise(rows):
        print("  %-2d %-5s %-13s %4d %6d %6d %5d %5d %6d %6s %9s %9s %9s %12s"
              % (s["complete_depth"], s["ttl"], s["scenario"],
                 s["sequential"], s["pairs"], s["pairs_le_096"],
                 s["pairs_gt_096"], s["residual"], s["declined"],
                 s["completion_rate"], s["complete_pnl"], s["hold_pnl"],
                 s["reduce_pnl"], s["complete_per_leg"]))
    print()
    print("COMPLETE / HOLD / REDUCE are the SAME inventory at the SAME frame.")
    print("PAIR_AT_ENTRY (both legs filled at entry) is excluded from these")
    print("rows: it is the simultaneous outcome and is not the completion")
    print("logic's to claim.")
    print()
    print("Bounds, not fills. No trade-print series exists; every fill is")
    print("PROVEN/POSSIBLE under a stated queue assumption. Nothing is")
    print("realized, and this harness is NOT evidence about the historical")
    print("cancels, which faced their own inventory, prices and runway.")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"rows": rows, "summary": summarise(rows)}, fh)
        print("\nwrote", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
