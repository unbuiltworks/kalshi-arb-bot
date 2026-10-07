"""The H-TURN mechanism replayed against L5's own tape. SHADOW ONLY.

ONE WINDOW, TWO LEDGERS, SAME ARRIVALS.

    L5 LEDGER      every one of L5's public fills, booked in order. The
                   reference trajectory. Relations (`open`/`extend`/
                   `complete`) come from `src/polymarket_l5/action_model.py ::
                   _relation`, imported UNMODIFIED, so the vocabulary is the
                   scorecard's and not a local re-definition.

    SHADOW LEDGER  the mechanism. It mirrors L5's SURPLUS-SIDE fills -- those
                   are the run, the exogenous arrivals H-TURN says a persistent
                   bid collects -- and it does NOT take his `complete` fills.
                   Its deficit side comes only from ITS OWN resting complement
                   being hit in the complement token's book. That is the whole
                   experiment: the turn is produced by a FILL, never predicted.

WHY THE SHADOW DOES NOT TAKE L5'S COMPLETE FILLS. If it did, it would inherit
his turn timing by construction and the comparison would be vacuous. The
mechanism must find the turn itself or not at all.

THE COMPARISON IS ANCHORED ON L5'S TURNS, which is the forensic's own unit:
`FORENSIC.md` §1 localises 94.3% of the clone's error in the cell immediately
after an `extend`. `turn_rows()` emits ONE ROW PER L5 TURN -- the first
`complete` ending a same-side run -- and asks, at that instant, what the
mechanism had resting and when it was hit. Side, price, fill timing, inventory
trajectory, pair cost and residual are compared on that row.

THE MIRROR IS AN ASSUMPTION AND IS LABELLED ONE. Mirroring a fill assumes our
resting bid would have been hit by the same flow. `ZENODO_OVERLAP_FINDINGS.md`
§2 shows 26.4% of L5's BTC15m maker fills price AT OR ABOVE the prevailing
ask -- a complete-set mint, which a resting bid cannot do. A run whose arrivals
are not post-only achievable is reported as such and never counted as a
shadow success.

UNEVALUABLE IS NOT A DEFAULT. If the complement has no quote, or the nearest
snapshot is older than the capture's own 60 s resync bound, or no price at or
above one tick keeps the pair under the frozen 0.96 ceiling, the row is
UNEVALUABLE and says which. The mechanism's dependency on a two-token book is
explicit; no book is ever simulated.

Nothing here is submitted, filled, settled or realized.
"""
from __future__ import annotations

import bisect
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.hturn_shadow import mechanism as M

LABEL = M.LABEL


def _bbo():
    """`analysis/l5/historical_l2/bbo_offset.py`, reused not duplicated.

    `Book`, the 60 s carry bound and `placement_consistency` all come from
    there; writing a second book reader would be the same mistake as a second
    order serializer.
    """
    from analysis.l5.historical_l2 import bbo_offset as B
    return B


def relation(prev_residual: float, outcome: str, side: str) -> str:
    from src.polymarket_l5.action_model import _relation
    return _relation(prev_residual, outcome, side)


# ------------------------------------------------------------------ book walk
def snapshot_at(book: Any, t: float) -> Optional[Dict[str, Any]]:
    """Latest snapshot at or before `t`, carried at most MAX_CARRY_S.

    An old row is the CURRENT state (the capture writes only on change); a row
    older than the capture's REST resync bound is refused rather than trusted.
    """
    return None if book is None else book.at(t)


def snapshots_between(book: Any, lo: float, hi: float
                      ) -> List[Tuple[float, Dict[str, Any]]]:
    if book is None or not book.t:
        return []
    i = bisect.bisect_right(book.t, lo)
    j = bisect.bisect_right(book.t, hi)
    return [(book.t[k], book.rows[k]) for k in range(i, j)]


def _cash_per_share(f: Dict[str, Any]) -> D:
    """CASH per share -- `usdcSize / size` where the corpus carries it, so the
    fee L5 was actually charged is already inside the basis. Falls back to the
    quoted price only when `usdcSize` is absent."""
    try:
        sz = D(str(f["size"]))
        usdc = f.get("usdcSize")
        if usdc is not None and sz > 0:
            return (D(str(usdc)) / sz).quantize(D("0.000001"))
    except Exception:                                          # noqa: BLE001
        pass
    return D(str(f["price"]))


# --------------------------------------------------------------------- replay
def replay_window(slug: str, fills: Sequence[Dict[str, Any]],
                  books: Dict[str, Any], *,
                  fee_allowance: D = M.FEE_ALLOWANCE_PER_SHARE,
                  ceiling: Optional[D] = None) -> Dict[str, Any]:
    """Run both ledgers over one window. Returns the comparison, not a verdict.

    `books` maps outcome ("Up"/"Down") to a `bbo_offset.Book`. A missing
    outcome is a missing book, and every row that needs it is UNEVALUABLE.
    """
    M.assert_mutation_incapable()
    B = _bbo()
    l5_inv = M.Inventory()
    sh_inv = M.Inventory()
    rest: Optional[M.RestingComplement] = None
    l5_actions: List[Dict[str, Any]] = []
    sh_fills: List[Dict[str, Any]] = []
    quote_log: List[Dict[str, Any]] = []
    sh_traj: List[Dict[str, Any]] = []
    sells = 0
    state = {"last_t": None}

    def scan_complement(upto: float) -> None:
        """Advance the resting complement through the complement's own book.

        THE FILL EVENT IS THE MARKET ARRIVING. A resting bid is hit when size
        is offered at or below its price. Inventory is booked on EVERY hit,
        including a partial, and the remainder keeps resting.
        """
        nonlocal rest
        if rest is None or state["last_t"] is None:
            return
        bk = books.get(rest.outcome)
        lo = max(float(state["last_t"]), rest.placed_t)
        for t, row in snapshots_between(bk, lo, upto):
            if rest is None or rest.qty_open <= 0:
                break
            avail = M.ask_liquidity_at_or_below(row, rest.price)
            if avail <= 0:
                continue
            got = rest.take(min(avail, rest.qty_open), t)
            if got <= 0:
                continue
            delta = sh_inv.apply_fill(rest.outcome, got, rest.price)
            sh_fills.append({"t": t, "outcome": rest.outcome, "qty": str(got),
                             "price": str(rest.price),
                             "remaining_open": str(rest.qty_open),
                             "is_partial": rest.qty_open > 0,
                             "matched_now": delta["matched_now"],
                             "avg_pair_cost": (
                                 None if sh_inv.avg_pair_cost() is None
                                 else str(sh_inv.avg_pair_cost())),
                             "residual_after": delta["residual_after"],
                             "label": LABEL})
            sh_traj.append({"t": t, "source": "SHADOW_COMPLEMENT_FILL",
                            **sh_inv.state()})
            if rest.qty_open <= 0:
                rest = None

    def maintain_complement(t: float) -> Dict[str, Any]:
        """Keep exactly ONE fee-safe, post-only complement resting."""
        nonlocal rest
        surplus = sh_inv.surplus_outcome
        if surplus is None:
            rest = None
            return {"status": "FLAT_NO_COMPLEMENT_NEEDED"}
        comp = M.flip(surplus)
        bk = books.get(comp)
        if bk is None:
            return {"status": M.U_NO_BOOK, "surplus": surplus,
                    "why": "no book on the complement token"}
        snap = snapshot_at(bk, t)
        if snap is None:
            return {"status": M.U_STALE, "surplus": surplus,
                    "why": ("no complement snapshot within the capture's "
                            "%.0fs carry bound" % B.MAX_CARRY_S)}
        basis = sh_inv.unmatched_avg_cost(surplus)
        px = M.fee_safe_complement_price(basis, snap.get("best_ask"),
                                         ceiling=ceiling,
                                         fee_allowance=fee_allowance)
        px = {**px, "surplus": surplus, "surplus_cash_basis": str(basis)}
        if px["status"] != M.EVALUABLE:
            return px
        q = D(px["price"])
        need = abs(sh_inv.residual_signed)
        if rest is not None and rest.outcome == comp:
            if rest.price != q or rest.qty_open != need:
                rest.reprice(q, need, t)
        else:
            rest = M.RestingComplement(comp, q, need, t)
        if M.crosses(q, snap.get("best_ask")):
            raise AssertionError("post-only violated: %s >= ask %s"
                                 % (q, snap.get("best_ask")))
        return {**px, "resting_qty": str(need), "reprices": rest.reprices,
                "quote": M.quote_repr(slug=slug, outcome=comp, price=q,
                                      qty=need)}

    for f in sorted(fills, key=lambda r: (r["t"], r.get("_i", 0))):
        t = float(f["t"])
        outcome, side = f["outcome"], f.get("side", "BUY")
        cash = _cash_per_share(f)

        # ---- L5 ledger: every fill, in order
        prev_res = float(l5_inv.residual_signed)
        rel = relation(prev_res, outcome, side)
        l5_inv.apply_fill(outcome, f["size"], cash) if side != "SELL" else None
        l5_actions.append({"t": t, "relation": rel, "outcome": outcome,
                           "side": side, "price": str(f["price"]),
                           "cash_per_share": str(cash),
                           "size": str(f["size"]),
                           "residual_after": str(l5_inv.residual_signed),
                           "avg_pair_cost": (
                               None if l5_inv.avg_pair_cost() is None
                               else str(l5_inv.avg_pair_cost()))})
        if side == "SELL":
            # L5's sells are a rounding error on this corpus and the mechanism
            # has no sell leg. Counted and skipped, never folded into a buy.
            sells += 1
            continue

        # ---- shadow ledger
        if state["last_t"] is None:
            state["last_t"] = t
        scan_complement(t)
        state["last_t"] = t

        sh_rel = relation(float(sh_inv.residual_signed), outcome, side)
        if sh_rel == "complete":
            # L5's own turn. The shadow does NOT take it.
            quote_log.append({"t": t, "event": "L5_TURN_NOT_MIRRORED",
                              "status": "SKIPPED_BY_DESIGN"})
            continue

        own_snap = snapshot_at(books.get(outcome), t - 1.0)
        consistency = B.placement_consistency(own_snap, f["price"])
        sh_inv.apply_fill(outcome, f["size"], cash)
        sh_traj.append({"t": t, "source": "MIRRORED_SURPLUS_ARRIVAL",
                        "outcome": outcome, "qty": str(f["size"]),
                        "post_only_achievable":
                            consistency == "RESTING_BID_CONSISTENT",
                        "consistency": consistency, **sh_inv.state()})
        q = maintain_complement(t)
        quote_log.append({"t": t, "event": "MIRRORED_%s" % sh_rel.upper(),
                          "mirror_consistency": consistency,
                          "post_only_achievable":
                              consistency == "RESTING_BID_CONSISTENT",
                          **{k: v for k, v in q.items() if k != "quote"}})

    if fills:
        tail = max(float(f["t"]) for f in fills)
        scan_complement(tail + 1e-6)

    out = {"market_slug": slug, "label": LABEL,
           "n_l5_fills": len(fills), "n_l5_sells": sells,
           "l5_relations": _counts(a["relation"] for a in l5_actions),
           "l5_final": l5_inv.state(), "shadow_final": sh_inv.state(),
           "n_shadow_complement_fills": len(sh_fills),
           "n_shadow_partial_fills": sum(1 for x in sh_fills
                                         if x["is_partial"]),
           "shadow_complement_still_open_at_end": (
               None if rest is None else {"outcome": rest.outcome,
                                          "price": str(rest.price),
                                          "qty_open": str(rest.qty_open)}),
           "l5_actions": l5_actions, "shadow_fills": sh_fills,
           "quote_log": quote_log, "shadow_trajectory": sh_traj}
    out["turns"] = turn_rows(out)
    out["inventory_comparison"] = inventory_comparison(out)
    return out


# ------------------------------------------------------- turn-anchored compare
def turn_rows(w: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per L5 TURN: what did the mechanism have resting, and when was
    it hit?

    An L5 TURN is a `complete` whose previous relation is `extend` -- the
    forensic's cell, 94.3% of the clone's error and 84% of its dollars. The
    run it ends begins at the first fill after the previous `complete`.
    """
    acts = w["l5_actions"]
    sh = w["shadow_fills"]
    sh_t = [x["t"] for x in sh]
    quotes = [q for q in w["quote_log"] if q.get("event", "").startswith(
        "MIRRORED_")]
    q_t = [q["t"] for q in quotes]
    rows: List[Dict[str, Any]] = []
    run_start: Optional[float] = None
    prev_rel: Optional[str] = None
    for i, a in enumerate(acts):
        rel = a["relation"]
        if rel in ("open", "extend") and run_start is None:
            run_start = a["t"]
        if rel == "complete" and prev_rel == "extend":
            nxt = next((b["t"] for b in acts[i + 1:]
                        if b["relation"] in ("open", "extend")), None)
            horizon = nxt if nxt is not None else acts[-1]["t"]
            lo = run_start if run_start is not None else a["t"]
            # the mechanism's quote state through this run
            qi = bisect.bisect_right(q_t, a["t"]) - 1
            qrun = [quotes[k] for k in range(len(quotes))
                    if lo <= q_t[k] <= a["t"]] or (
                        [quotes[qi]] if qi >= 0 else [])
            statuses = [q.get("status") for q in qrun]
            postable = [q for q in qrun if q.get("status") == M.EVALUABLE]
            non_postonly = sum(1 for q in qrun
                               if q.get("post_only_achievable") is False)
            # the mechanism's own turn: its first complement fill in the run,
            # or up to the start of the next run if it was late
            j = bisect.bisect_left(sh_t, lo)
            hit_in_run = sh[j] if (j < len(sh) and sh[j]["t"] <= a["t"]) \
                else None
            hit_by_horizon = sh[j] if (j < len(sh)
                                       and sh[j]["t"] <= horizon) else None
            if not postable:
                status = (statuses[-1] if statuses and statuses[-1]
                          in M.UNEVALUABLE_LABELS else M.U_NO_BOOK)
                why = (qrun[-1].get("why") if qrun else
                       "no mechanism quote state covers this run")
            elif non_postonly and not any(
                    q.get("post_only_achievable") for q in postable):
                status, why = M.U_MIRROR, (
                    "every arrival in this run priced at or above the "
                    "prevailing ask: a post-only resting bid could not have "
                    "been those fills")
            else:
                status, why = M.EVALUABLE, None
            row = {
                "market_slug": w["market_slug"], "label": LABEL,
                "run_start_t": lo, "l5_turn_t": a["t"],
                "run_duration_s": round(a["t"] - lo, 1),
                "status": status, "why": why,
                "quote_states_in_run": _counts(s for s in statuses if s),
                "arrivals_in_run": len(qrun),
                "arrivals_not_post_only_achievable": non_postonly,
                # ---- side
                "l5_turn_outcome": a["outcome"],
                "shadow_complement_outcome": (postable[-1].get("surplus")
                                              and M.flip(postable[-1]["surplus"]))
                if postable else None,
                # ---- price
                "l5_turn_price": a["price"],
                "l5_turn_cash_per_share": a["cash_per_share"],
                "shadow_complement_price": (postable[-1].get("price")
                                            if postable else None),
                "shadow_fee_safe_target": (postable[-1].get("fee_safe_target")
                                           if postable else None),
                "shadow_surplus_cash_basis": (
                    postable[-1].get("surplus_cash_basis")
                    if postable else None),
                # ---- fill timing
                "shadow_turn_t": (hit_in_run or {}).get("t"),
                "shadow_turn_t_by_next_run": (hit_by_horizon or {}).get("t"),
                "shadow_turn_qty": (hit_in_run or {}).get("qty"),
                "shadow_turn_was_partial": (hit_in_run or {}).get("is_partial"),
                # ---- pair cost
                "l5_pair_cost_running": a["avg_pair_cost"],
                "shadow_pair_cost_if_hit": (
                    None if not postable or postable[-1].get(
                        "pair_cost_if_filled") is None
                    else postable[-1]["pair_cost_if_filled"]),
                "shadow_pair_cost_running": (hit_in_run or {}).get(
                    "avg_pair_cost"),
                # ---- residual
                "l5_residual_after_turn": a["residual_after"],
                "shadow_residual_after_hit": (hit_in_run or {}).get(
                    "residual_after"),
            }
            row["turn_timing_delta_s"] = (
                None if row["shadow_turn_t"] is None
                else round(row["shadow_turn_t"] - a["t"], 1))
            row["turn_timing_delta_s_by_next_run"] = (
                None if row["shadow_turn_t_by_next_run"] is None
                else round(row["shadow_turn_t_by_next_run"] - a["t"], 1))
            row["side_agrees"] = (row["shadow_complement_outcome"]
                                  == a["outcome"]) if postable else None
            row["price_delta"] = (
                None if row["shadow_complement_price"] is None
                else str(D(row["shadow_complement_price"])
                         - D(row["l5_turn_price"])))
            rows.append(row)
            run_start = None
        prev_rel = rel
    return rows


def inventory_comparison(w: Dict[str, Any]) -> Dict[str, Any]:
    """Residual trajectories side by side. Time-weighted, not just endpoints.

    `mean_abs_residual` integrates |residual| over the window's own clock, so
    a ledger that carries 300 shares for ten minutes is not scored the same as
    one that touches 300 for a second.
    """
    def integrate(points: Sequence[Tuple[float, D]]) -> Optional[D]:
        if len(points) < 2:
            return None
        num = D(0)
        for (t0, r0), (t1, _r1) in zip(points, points[1:]):
            num += abs(r0) * D(str(t1 - t0))
        span = D(str(points[-1][0] - points[0][0]))
        return None if span <= 0 else (num / span).quantize(D("0.01"))

    l5_pts = [(a["t"], D(a["residual_after"])) for a in w["l5_actions"]]
    sh_pts = [(x["t"], D(x["residual_signed"])) for x in w["shadow_trajectory"]]
    return {
        "l5_mean_abs_residual_time_weighted": (
            None if integrate(l5_pts) is None else str(integrate(l5_pts))),
        "shadow_mean_abs_residual_time_weighted": (
            None if integrate(sh_pts) is None else str(integrate(sh_pts))),
        "l5_peak_abs_residual": str(max((abs(r) for _t, r in l5_pts),
                                        default=D(0))),
        "shadow_peak_abs_residual": str(max((abs(r) for _t, r in sh_pts),
                                            default=D(0))),
        "l5_residual_end": w["l5_final"]["residual_signed"],
        "shadow_residual_end": w["shadow_final"]["residual_signed"],
        "l5_matched_qty": w["l5_final"]["matched_qty"],
        "shadow_matched_qty": w["shadow_final"]["matched_qty"],
        "l5_avg_pair_cost": w["l5_final"]["avg_pair_cost"],
        "shadow_avg_pair_cost": w["shadow_final"]["avg_pair_cost"],
        "note": "residual is UNSETTLED SHADOW inventory. Not submitted, not "
                "filled, not settled, not realized.",
    }


def _counts(it) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out
