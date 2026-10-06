"""DIAGNOSTIC S2 -- historical only. Not a replacement for prospective S1.

AUTHORIZATION. This module is a HISTORICAL DIAGNOSTIC. It has **no shadow and
no live authorization**, no order path, no credential and no network call. It
exists to test whether the mechanisms the August forensic replay implicated
actually close the observed L5 gap. Frozen prospective S1 is imported
read-only and is never written; its fingerprint is asserted before any tick.

S2 CHANGES EXACTLY THREE THINGS, each one named by the trace:

  1. QUOTE MAINTENANCE. S1's `order_lifetime_s: None` plus "replace only
     after an evidenced fill" stranded a bid at 0.3800 for 876 s while the
     touch ran to 0.999. S2 adds two CAUSAL triggers that fire on observed
     market movement rather than on the order having filled:
       REPRICE_ON_DISPLACEMENT  touch more than 6 ticks above our price
       REPRICE_ON_STALENESS     our price below the touch for over 69 s
     Both thresholds are read off L5's OWN behaviour in the diagnostic window
     (offset p99 = 6; maximum same-token inter-fill gap = 69 s) in
     EVIDENCE_REFRESH.md, which was computed and committed before this file
     existed. Neither was swept and neither was chosen by looking at an S2
     outcome. There is still NO fixed lifetime: an order at the touch in a
     quiet book is never replaced however old.

  2. MINT OPPORTUNITY, RECORDED AND NEVER CREDITED. 27.5% of L5's volume in
     the window priced at or above the ask and was mint-feasible -- the CTF
     complete-set match, which S1 cannot represent at all. S2 records the
     opportunity from the two token books. It is labelled MINT_OPPORTUNITY.
     It does **not** move inventory, cost, matched or residual. Public data
     cannot prove our hypothetical order would have received the match, so
     calling it a fill would be inventing one.

  3. AN INDEPENDENT EVIDENCE ARM. Arm C feeds the frozen classifier prints
     from which EVERY leg involving L5 has been removed, so S2 is never
     credited off L5's own activity.

WHAT S2 DOES **NOT** CHANGE, DELIBERATELY:

  * `classify_fill` is IMPORTED VERBATIM from frozen S1 and is not weakened.
    Same graded standard, same NEVER touch=fill, same uncertain verdicts
    recorded and excluded.
  * `residual_cap_shares` stays **80** and `residual_control` stays
    QUOTE_SUPPRESSION_ONLY. The brief is explicit: do not simply raise the
    cap. S1 hit it because the fill mechanism loaded one side while the
    opposite quote went stale, so the question to answer FIRST is whether
    quote maintenance alone improves two-sided acquisition. Raising the cap
    before answering that would destroy the answer.
  * quantity 23.61, levels 1, offset 0 ticks, TTE shutdown 60, the absent 5c
    EXTEND rule and no aggressive opposite side are all unchanged.

A SELECTION BIAS THAT MUST BE STATED WHEREVER THESE FILLS ARE QUOTED.
EVIDENCED_LEVEL_CONSUMED fires if and only if the best bid has fallen
STRICTLY BELOW our own price -- that is, only when the market has crossed our
bid. It therefore mechanically selects the subset of hypothetical maker fills
whose contemporaneous book movement went against us, and **systematically
excludes benign fills** (size leaving our level while the touch holds), which
the frozen classifier returns as UNCERTAIN_PARTIAL and excludes from
economics. The resulting acquisition prices and settlement figures are NOT an
unbiased estimate of what a real resting order would have experienced; they
are a lower bound biased toward adverse selection. Arm C exists because
third-party prints can evidence a fill WITHOUT requiring the touch to cross
us, which is the only way to see past that bias with this data.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.august_replay.replay import (EVIDENCED, UNCERTAIN, Ledger,
                                              ladders)
from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from src.polymarket_l5.owned_order_shadow import (CANDIDATE as S1_CANDIDATE,
                                                  FINGERPRINT as S1_FP, Order,
                                                  best_ask, best_bid,
                                                  classify_fill, size_at)

TICK = 0.01
PAR = 1.00

S2_CANDIDATE = {
    "name": "DIAGNOSTIC_S2_HISTORICAL_ONLY",
    "classification": "HISTORICAL_DIAGNOSTIC_NO_SHADOW_NO_LIVE_AUTHORIZATION",
    "derived_from_frozen_s1_fingerprint": S1_FP,
    # ---- unchanged from frozen S1
    "levels": 1,
    "offset_from_bid_ticks": 0,
    "quantity_shares": 23.61,
    "replenish_after_evidenced_fill": True,
    "replenish_after_uncertain_fill": False,
    "order_lifetime_s": None,
    "residual_cap_shares": 80,
    "residual_control": "QUOTE_SUPPRESSION_ONLY",
    "tte_shutdown_s": 60,
    "no_5c_extend_rule": True,
    "no_aggressive_opposite_side": True,
    "fill_standard": "evidenced only; uncertain tracked separately",
    "fill_classifier": "imported verbatim from frozen S1, not weakened",
    # ---- the three changes, each implicated by the August trace
    "reprice_on_displacement_ticks": 6,
    "reprice_on_displacement_source":
        "EVIDENCE_REFRESH.md offset_ticks p99 of L5's own fills",
    "reprice_on_staleness_s": 69,
    "reprice_on_staleness_source":
        "EVIDENCE_REFRESH.md max observed same-token inter-fill gap",
    "mint_opportunity_recorded": True,
    "mint_never_credited_as_fill": True,
    "evidence_arms": ["A_book_only", "C_third_party_prints_l5_excluded"],
}
S2_FINGERPRINT = hashlib.sha256(
    json.dumps(S2_CANDIDATE, sort_keys=True).encode()).hexdigest()


def assert_s1_frozen() -> None:
    expect = ("441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea"
              "76caf333fe24b6fe")
    assert S1_FP == expect, "FROZEN S1 CHANGED -- refusing to run"
    assert S1_CANDIDATE["quantity_shares"] == 23.61
    # S2 must not have silently drifted from S1 where it claims to match
    for k in ("levels", "offset_from_bid_ticks", "quantity_shares",
              "residual_cap_shares", "residual_control", "tte_shutdown_s",
              "no_5c_extend_rule", "no_aggressive_opposite_side",
              "replenish_after_evidenced_fill",
              "replenish_after_uncertain_fill", "order_lifetime_s"):
        assert S2_CANDIDATE[k] == S1_CANDIDATE[k], (
            "S2 diverges from S1 on %s, which it claims to leave alone" % k)


def displacement_ticks(bb: Optional[float], px: float) -> Optional[int]:
    """How many ticks the touch sits ABOVE our price. Negative is impossible
    for a resting bid at or below the touch and returns 0."""
    if bb is None:
        return None
    return max(0, int(round((bb - px) / TICK)))


def mint_opportunity(our_px: float, our_bids: List[Tuple[float, float]],
                     comp_bids: List[Tuple[float, float]], our_qty: float
                     ) -> Optional[Dict[str, Any]]:
    """Could a complete-set mint have paid for our resting bid at `our_px`?

    CTF MECHANICS. A complete set is minted by depositing $1, yielding one of
    each outcome. The venue can match two BUYERS of opposite outcomes: if our
    bid at p and a counterparty's bid on the complement at q satisfy
    p + q >= 1.00, the pair funds a mint and each takes one token. No seller
    and no offer on our own book is required -- which is exactly why S1, a
    resting bid with no mint representation, cannot express it.

    WHAT WE CANNOT PROVE, AND THEREFORE DO NOT CLAIM. We cannot prove the
    venue would have selected OUR order. Better-priced bids on our own side
    are matched ahead of us, and bids at our own price sit ahead of us in
    queue, so the mint volume that could reach us is bounded by the
    complement's capacity MINUS everything ahead of us. That bound is
    computed and returned; the row is labelled an OPPORTUNITY and never a
    fill, and it never moves inventory.
    """
    need = PAR - our_px
    cap = sum(sz for pr, sz in comp_bids if pr >= need - 1e-9)
    if cap <= 0:
        return None
    ahead = sum(sz for pr, sz in our_bids if pr >= our_px - 1e-9)
    reachable = max(0.0, cap - ahead)
    cb = best_bid(comp_bids)
    return {
        "our_price": our_px,
        "complement_bid_needed": round(need, 6),
        "complement_best_bid": cb,
        "pair_sum_at_best": (round(our_px + cb, 6) if cb is not None
                             else None),
        "complement_capacity_at_or_above_need": round(cap, 4),
        "our_side_size_at_or_above_our_price": round(ahead, 4),
        "capacity_reachable_after_queue_ahead": round(reachable, 4),
        "opportunity_shares_bounded": round(min(our_qty, reachable), 4),
        "label": "MINT_OPPORTUNITY",
        "is_a_fill": False,
        "why_not_a_fill": ("public data cannot prove our hypothetical order "
                           "would have received the mint match"),
    }


def replay_s2(books: Dict[str, Book], tok_up: str, tok_dn: str,
              start: int, end: int, l5_by_s: Dict[int, List[dict]],
              prints_by_s: Dict[int, List[dict]], *, use_prints: bool
              ) -> Dict[str, Any]:
    """Frozen S1's tick order, plus quote maintenance and mint recording."""
    assert_s1_frozen()
    names = [tok_up, tok_dn]
    label = {tok_up: "up", tok_dn: "down"}
    orders: Dict[str, Optional[Order]] = {tok_up: None, tok_dn: None}
    s2 = Ledger(tok_up, tok_dn)
    l5 = Ledger(tok_up, tok_dn)
    last_prints: Dict[str, List[Tuple[float, float]]] = {n: [] for n in names}
    #: when our current price last stood at or above the touch
    at_touch_since: Dict[str, Optional[float]] = {n: None for n in names}
    rows: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    uncertain: Dict[str, int] = {}
    reprices: Dict[str, int] = {"REPRICE_ON_DISPLACEMENT": 0,
                                "REPRICE_ON_STALENESS": 0}
    mint_ticks = 0
    mint_shares_bounded = 0.0
    stale_side_ticks = 0
    unresolvable = 0
    seq = 0

    def place(tok: str, bids, t: int) -> Optional[Order]:
        nonlocal seq
        p = best_bid(bids)
        if p is None:
            return None
        seq += 1
        o = Order("s2-%d" % seq, tok, label[tok], p,
                  S2_CANDIDATE["quantity_shares"], t,
                  size_at(bids, p), size_at(bids, p))
        at_touch_since[tok] = float(t)
        return o

    for t in range(start, end):
        tte = end - t

        # L5's actual fills -- his ledger only, before the book check, because
        # a chain fill does not depend on the L2 capture resolving.
        l5_here = []
        for f in l5_by_s.get(t, []):
            tok = f["token_id"]
            if tok not in (tok_up, tok_dn):
                continue
            px, sz = float(f["price"]), float(f["size"])
            l5.buy(tok, px, sz)
            l5_here.append({"outcome": label[tok], "price": px, "size": sz,
                            "t_minus_1_class": f.get("_class"),
                            "mint_consistent": f.get("_mint_consistent")})

        snaps = {}
        missing = False
        for tok in names:
            sp = books[tok].at(float(t), max_age_s=MAX_CARRY_S)
            if sp is None:
                missing = True
                break
            snaps[tok] = sp
        if missing:
            unresolvable += 1
            rows.append({"t": t, "tte_s": tte,
                         "book": "UNRESOLVABLE_WITHIN_CARRY_BOUND",
                         "l5_fills_this_second": l5_here,
                         "l5_ledger": l5.snap()})
            continue

        bids: Dict[str, List[Tuple[float, float]]] = {}
        asks: Dict[str, List[Tuple[float, float]]] = {}
        for tok in names:
            b, a = ladders(snaps[tok])
            bids[tok], asks[tok] = b, a

        if use_prints:
            for tok in names:
                last_prints[tok] = [
                    (float(p["price"]), float(p["size"]))
                    for p in prints_by_s.get(t, [])
                    if p["token_id"] == tok]

        s2_fills_here = []
        mints_here = []
        for tok in names:
            o = orders[tok]
            # ---- 1) the FROZEN classifier, unchanged, first
            if o is not None:
                v = classify_fill(o, bids[tok], last_prints[tok])
                if v in EVIDENCED:
                    o.state, o.evidence, o.state_t = "FILLED", v, t
                    s2.buy(tok, o.px, o.qty)
                    s2_fills_here.append({"outcome": label[tok],
                                          "price": o.px, "size": o.qty,
                                          "evidence": v, "order_id": o.oid,
                                          "age_s": t - o.placed_t,
                                          "queue_ahead_at_placement":
                                              o.queue_ahead})
                    events.append({"t": t, "who": "S2", "kind": "fill",
                                   "outcome": label[tok], "price": o.px,
                                   "size": o.qty, "evidence": v,
                                   "order_id": o.oid,
                                   "age_s": t - o.placed_t})
                    orders[tok] = None if S2_CANDIDATE[
                        "replenish_after_evidenced_fill"] else o
                elif v in UNCERTAIN:
                    uncertain[v] = uncertain.get(v, 0) + 1
                    events.append({"t": t, "who": "S2", "kind": "uncertain",
                                   "outcome": label[tok], "verdict": v,
                                   "order_id": o.oid, "price": o.px,
                                   "excluded_from_primary_economics": True})
                    o.last_size_at = size_at(bids[tok], o.px)

            # ---- 2) QUOTE MAINTENANCE: the only new decision in the loop
            o = orders[tok]
            if o is not None:
                bb = best_bid(bids[tok])
                disp = displacement_ticks(bb, o.px)
                if bb is not None and o.px >= bb - 1e-9:
                    at_touch_since[tok] = float(t)
                stale_for = (None if at_touch_since[tok] is None
                             else t - at_touch_since[tok])
                if disp is not None and disp > 0:
                    stale_side_ticks += 1
                why = None
                if disp is not None and disp > S2_CANDIDATE[
                        "reprice_on_displacement_ticks"]:
                    why = "REPRICE_ON_DISPLACEMENT"
                elif (stale_for is not None
                      and stale_for > S2_CANDIDATE["reprice_on_staleness_s"]):
                    why = "REPRICE_ON_STALENESS"
                if why:
                    reprices[why] += 1
                    events.append({"t": t, "who": "S2", "kind": "reprice",
                                   "outcome": label[tok], "why": why,
                                   "old_price": o.px, "new_touch": bb,
                                   "displacement_ticks": disp,
                                   "stale_for_s": stale_for,
                                   "age_s": t - o.placed_t,
                                   "is_a_fill": False})
                    orders[tok] = None            # cancelled, not filled

            # ---- 3) place / replenish, under the UNCHANGED suppression rules
            if orders[tok] is None:
                other = tok_dn if tok == tok_up else tok_up
                if s2.inv[tok] - s2.inv[other] >= S2_CANDIDATE[
                        "residual_cap_shares"]:
                    continue
                if (end - t) < S2_CANDIDATE["tte_shutdown_s"]:
                    continue
                o = place(tok, bids[tok], t)
                if o is None:
                    continue
                orders[tok] = o
                events.append({"t": t, "who": "S2", "kind": "order_placed",
                               "outcome": label[tok], "price": o.px,
                               "order_id": o.oid,
                               "queue_ahead": o.queue_ahead})

            # ---- 4) MINT OPPORTUNITY: recorded, never a fill
            o = orders[tok]
            if o is not None:
                other = tok_dn if tok == tok_up else tok_up
                m = mint_opportunity(o.px, bids[tok], bids[other], o.qty)
                if m and m["opportunity_shares_bounded"] > 0:
                    m["outcome"] = label[tok]
                    m["order_id"] = o.oid
                    mints_here.append(m)
                    mint_shares_bounded += m["opportunity_shares_bounded"]

        if mints_here:
            mint_ticks += 1
            events.append({"t": t, "who": "S2", "kind": "mint_opportunity",
                           "n": len(mints_here), "rows": mints_here,
                           "is_a_fill": False})

        resting = {}
        for tok in names:
            o = orders[tok]
            bb = best_bid(bids[tok])
            resting[label[tok]] = None if o is None else {
                "order_id": o.oid, "price": o.px, "qty": o.qty,
                "age_s": t - o.placed_t,
                "queue_ahead_at_placement": o.queue_ahead,
                "size_at_level_now": size_at(bids[tok], o.px),
                "displacement_ticks": displacement_ticks(bb, o.px)}

        row = {"t": t, "tte_s": tte}
        for tok in names:
            n = label[tok]
            bb, ba = best_bid(bids[tok]), best_ask(asks[tok])
            row[n + "_bid"] = bb
            row[n + "_ask"] = ba
            row[n + "_bid_depth"] = (size_at(bids[tok], bb)
                                     if bb is not None else None)
            row[n + "_carried_s"] = snaps[tok].get("carried_s")
        bbs = [row["up_bid"], row["down_bid"]]
        row["mint_sum"] = (round(bbs[0] + bbs[1], 6)
                           if None not in bbs else None)
        row["l5_fills_this_second"] = l5_here
        row["l5"] = l5.snap()
        row["s2_resting_orders"] = resting
        row["s2_fills_this_second"] = s2_fills_here
        row["s2_mint_opportunities"] = mints_here
        row["s2"] = s2.snap()
        rows.append(row)

    return {"rows": rows, "events": events,
            "s2_final": s2.snap(), "l5_final": l5.snap(),
            "s2_uncertain_counts": uncertain,
            "reprices": reprices,
            "reprices_total": sum(reprices.values()),
            "mint_opportunity_ticks": mint_ticks,
            "mint_opportunity_shares_bounded": round(mint_shares_bounded, 2),
            "stale_side_ticks_any_displacement": stale_side_ticks,
            "unresolvable_ticks": unresolvable,
            "orders_placed": seq, "used_prints": use_prints,
            "s2_fingerprint": S2_FINGERPRINT,
            "selection_bias_note": (
                "EVIDENCED_LEVEL_CONSUMED fires only when the best bid has "
                "fallen strictly below our price, so these fills are the "
                "subset whose book movement went against us. Benign fills "
                "are returned as UNCERTAIN_PARTIAL and excluded. Acquisition "
                "prices and settlement here are NOT an unbiased estimate of "
                "hypothetical maker fills.")}
