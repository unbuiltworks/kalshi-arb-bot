"""Continuous SHADOW-ONLY evaluation of the three completion policies.

WHAT THIS IS. One record per eligible BTC15m window, produced from evidence
the recorder ALREADY emits (`PMUS_LADDER_ROWS`, `PMUS_TRADE_ROWS`), scoring
three policies against each other and against the inventory-blind baseline:

  HOLD    keep the resting opposite quote and its queue priority
  REPRICE re-join at the fee-safe ceiling `inventory_aware_quote` computes
  CROSS   complete at the actually executable price, through the EXISTING
          `completion_decision`, narrowed by `completion_vs_resting`

WHAT THIS IS NOT. It submits nothing, cancels nothing, reads no credential
and touches no ledger. Every "fill" here is MODELLED from queue evidence and
is labelled as such; none of it is a fill, a position, or realized profit.

WHY IT LIVES OUTSIDE THE RECORDER PROCESS. Queue-cleared fill times need the
whole window's trade prints from the open (~3,700 on a BTC15m window), while
the recorder keeps only the last 500 per market in memory. The emitted log
rows are the complete record, so the evaluation reads those rather than
asking the read-only recorder to retain eight times more state. Nothing has
to be deployed for this to run.

EVERY POLICY IS PRICED BY THE SAME FEE MODEL -- `l5_two_sided_probe.fee`,
maker theta for a resting leg and taker theta for a crossing one. There is no
second fee table here, which is the whole reason the comparison is meaningful.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import l5_two_sided_probe as TS

#: the policies scored, in the order they are reported
POLICIES = ("HOLD", "REPRICE", "CROSS", "BLIND")


def _d(v: Any) -> Optional[D]:
    return TS._d(v)


def window_start_epoch(slug: str) -> Optional[float]:
    """Epoch seconds of the window START, which the slug names."""
    parts = str(slug).split("-")
    if len(parts) < 5:
        return None
    try:
        day = "-".join(parts[-4:-1])
        hhmm = parts[-1][:4]
        return dt.datetime.strptime(day + hhmm, "%Y-%m-%d%H%M").replace(
            tzinfo=dt.timezone.utc).timestamp()
    except Exception:
        return None


def rung_depth(snap: Mapping[str, Any], side: str, px: D) -> Optional[D]:
    """Displayed size at exactly `px` on `side`, or None if it is not a rung.

    NONE IS NOT ZERO AND IS NOT INFINITY. A price that is not a visible rung
    has an UNKNOWN queue ahead of it, and the caller treats unknown as
    never-filling. Inventing a zero queue there is how a replay manufactures
    instant fills at prices nobody was quoting.
    """
    for p, q in (snap.get(side) or []):
        if _d(p) == px:
            return _d(q)
    return None


def queue_fill(trades: Sequence[Mapping[str, Any]], *, t_join: float,
               px: D, side: str, queue_ahead: Optional[D]) -> Dict[str, Any]:
    """MODELLED fill: when cumulative qualifying volume exceeds the queue.

    `side` is where OUR order rests: "bid" fills on prints at or below `px`,
    "ask" on prints at or above it. Volume is counted only from `t_join`
    forward -- a print before we joined cannot have been ahead of us -- and an
    unknown `queue_ahead` never fills.

    THIS IS NOT A FILL. Displayed depth is an upper bound on the queue ahead
    and a lower bound on what may join behind; the result is a model, and the
    evidence it rests on is returned alongside it so the model can be argued
    with rather than believed.
    """
    out = {"filled": False, "t": None, "queue_ahead":
           None if queue_ahead is None else str(queue_ahead),
           "cum_qualifying": "0", "prints_counted": 0,
           "modelled": True}
    if queue_ahead is None:
        out["reason"] = "QUEUE_AHEAD_UNREADABLE"
        return out
    cum = D(0)
    n = 0
    for r in trades:
        t = r.get("t")
        if t is None or t < t_join:
            continue
        p = _d(r.get("px"))
        q = _d(r.get("qty"))
        if p is None or q is None:
            continue
        if (p <= px) if side == "bid" else (p >= px):
            cum += q
            n += 1
            if cum > queue_ahead:
                out.update(filled=True, t=t, cum_qualifying=str(cum),
                           prints_counted=n)
                return out
    out.update(cum_qualifying=str(cum), prints_counted=n,
               reason="QUEUE_NEVER_CLEARED")
    return out


def _leg_cash(qty: D, px: D, *, maker: bool) -> D:
    """Cash out for one leg under the SHARED fee model."""
    theta = TS.MAKER_THETA if maker else TS.TAKER_THETA
    return qty * px + TS.fee(theta, qty, px)


def settlement_net(*, qty_up: D, qty_dn: D, cash_up: D, cash_dn: D,
                   yes_value: Optional[D]) -> Dict[str, Any]:
    """Settlement-adjusted net for a book, given the venue's YES value.

    `yes_value` 1 means UP redeemed at a dollar, 0 means DOWN did. None means
    the window has not settled: the matched portion is still exact (a pair
    pays a dollar on either outcome) but the residual is NOT, so the residual
    is reported separately and left unresolved rather than marked at a price.
    """
    matched = min(qty_up, qty_dn)
    res_up = qty_up - matched
    res_dn = qty_dn - matched
    cash = cash_up + cash_dn
    out = {"matched_qty": str(matched), "cash_out_usd": str(cash),
           "residual_up": str(res_up), "residual_dn": str(res_dn)}
    if res_up == 0 and res_dn == 0:
        out.update(settled=True, redemption_usd=str(matched),
                   net_usd=str(matched - cash), pending_residual=False)
        return out
    if yes_value is None:
        out.update(settled=False, pending_residual=True,
                   redemption_usd=None, net_usd=None,
                   reason="RESIDUAL_UNSETTLED")
        return out
    yv = D(yes_value)
    redeem = matched + res_up * yv + res_dn * (D(1) - yv)
    out.update(settled=True, pending_residual=False,
               redemption_usd=str(redeem), net_usd=str(redeem - cash))
    return out


def evaluate_window(slug: str, ladder: Sequence[Mapping[str, Any]],
                    trades: Sequence[Mapping[str, Any]], *,
                    tick: Any, min_qty: Any, depth_ticks: int,
                    cash_envelope: Any, max_worst_case_loss: Any,
                    yes_value: Any = None,
                    book_age_s: Any = D("0.5")) -> Dict[str, Any]:
    """Score HOLD / REPRICE / CROSS / BLIND on one window. Pure."""
    rec: Dict[str, Any] = {"market": slug, "shadow_only": True,
                           "submitted": False, "fills_are_modelled": True}
    lad = sorted([r for r in ladder if r.get("t") is not None],
                 key=lambda r: r["t"])
    trd = sorted([r for r in trades if r.get("t") is not None],
                 key=lambda r: r["t"])
    if not lad:
        return dict(rec, ok=False, reason="NO_LADDER_EVIDENCE")
    t_start = window_start_epoch(slug)
    if t_start is None:
        return dict(rec, ok=False, reason="WINDOW_START_UNREADABLE")
    s0 = lad[0]
    t0 = s0["t"]
    tk = _d(tick)
    if tk is None or tk <= 0:
        return dict(rec, ok=False, reason="TICK_UNKNOWN")

    # ---- ELIGIBILITY AND SIZE through the EXISTING builder, not a copy of it.
    plan = TS.build_orders(
        market=slug, bid=s0.get("bid"), ask=s0.get("ask"),
        bid_depth=rung_depth(s0, "bids", _d(s0.get("bid")) or D(0)),
        ask_depth=rung_depth(s0, "asks", _d(s0.get("ask")) or D(0)),
        tick=tk, min_qty=min_qty, depth_ticks=depth_ticks,
        cash_envelope=cash_envelope,
        max_worst_case_loss=max_worst_case_loss, book_age_s=book_age_s)
    rec["eligibility"] = {"ok": bool(plan.get("ok")),
                          "reason": plan.get("reason")}
    if not plan.get("ok"):
        return dict(rec, ok=False, reason="NOT_ELIGIBLE",
                    detail=plan.get("reason"))
    qty = _d(plan["qty_per_leg"])
    legs = {str(l.get("side")): l for l in (plan.get("legs") or [])}
    up_px = _d((legs.get("UP") or {}).get("price"))
    dn_px = _d((legs.get("DOWN") or {}).get("price"))
    if qty is None or up_px is None or dn_px is None:
        return dict(rec, ok=False, reason="PLAN_UNREADABLE")
    dn_ask = D(1) - dn_px          # where a DOWN buy rests on the UP book
    rec["quote"] = {"qty": str(qty), "up_px": str(up_px),
                    "dn_px": str(dn_px), "dn_rests_as_ask_at": str(dn_ask),
                    "pair_cost": str(up_px + dn_px),
                    "depth_ticks": int(depth_ticks),
                    "cash_envelope": str(_d(cash_envelope)),
                    "worst_case_ceiling": str(_d(max_worst_case_loss))}

    # ---- MODELLED fills of the two resting legs, from the open.
    f_up = queue_fill(trd, t_join=t0, px=up_px, side="bid",
                      queue_ahead=rung_depth(s0, "bids", up_px))
    f_dn = queue_fill(trd, t_join=t0, px=dn_ask, side="ask",
                      queue_ahead=rung_depth(s0, "asks", dn_ask))
    rec["resting"] = {"up": f_up, "dn": f_dn}
    if not f_up["filled"] and not f_dn["filled"]:
        return dict(rec, ok=True, outcome="NEITHER",
                    one_sided_s=None, decisions=[],
                    net={p: settlement_net(qty_up=D(0), qty_dn=D(0),
                                           cash_up=D(0), cash_dn=D(0),
                                           yes_value=_d(yes_value))
                         for p in POLICIES},
                    selected="HOLD")

    if f_up["filled"] and f_dn["filled"]:
        outcome = "BOTH"
        t_acq = min(f_up["t"], f_dn["t"])
        t_second = max(f_up["t"], f_dn["t"])
    else:
        outcome = "UP_ONLY" if f_up["filled"] else "DOWN_ONLY"
        t_acq = f_up["t"] if f_up["filled"] else f_dn["t"]
        t_second = None
    held_is_up = bool(f_up["filled"] and (not f_dn["filled"]
                                          or f_up["t"] <= f_dn["t"]))
    defic = "DOWN" if held_is_up else "UP"
    held_px = up_px if held_is_up else dn_px
    resting_px = dn_px if held_is_up else up_px
    up_q = qty if held_is_up else D(0)
    dn_q = D(0) if held_is_up else qty
    up_c = up_px if held_is_up else None
    dn_c = None if held_is_up else dn_px
    rec["inventory_after_acquisition"] = {
        "held_side": "UP" if held_is_up else "DOWN",
        "held_qty": str(qty), "held_acquisition_px": str(held_px),
        "deficient_side": defic, "resting_px": str(resting_px),
        "resting_order_live": True}
    rec["outcome"] = outcome
    rec["one_sided_s"] = (None if t_second is None
                          else round(t_second - t_acq, 3))

    # ---- THE SEQUENTIAL DECISION WALK. Information available AT each t only.
    cut = TS.cancel_cutoff_from_start(float(t_start))
    decisions: List[Dict[str, Any]] = []
    first = {"REPRICE": None, "CROSS": None, "BLIND": None}
    census: Dict[str, int] = {}
    for s in lad:
        t = s["t"]
        if t <= t_acq:
            continue
        if t_second is not None and t > t_second:
            break               # the resting leg filled; nothing left to decide
        if t >= cut:
            break               # LATE: nothing may rest and nothing is decided
        phase = TS.completion_phase(t, float(t_start))
        b = _d(s.get("bid"))
        a = _d(s.get("ask"))
        aware = TS.inventory_aware_quote(
            up_qty=up_q, dn_qty=dn_q, up_cost_px=up_c, dn_cost_px=dn_c,
            blind_up_px=up_px, blind_dn_px=dn_px, tick=tk)
        # own-outcome executable price for the DEFICIENT side
        if defic == "UP":
            taker_px = a
            taker_depth = rung_depth(s, "asks", a) if a is not None else None
        else:
            taker_px = (D(1) - b) if b is not None else None
            taker_depth = rung_depth(s, "bids", b) if b is not None else None
        raw = TS.completion_decision(
            up_qty=up_q, dn_qty=dn_q, up_cost_px=up_c, dn_cost_px=dn_c,
            taker_price=taker_px, available_depth=taker_depth,
            tick=tk, min_qty=min_qty, cash_headroom=cash_envelope,
            worst_case_ceiling=max_worst_case_loss,
            book_age_s=book_age_s, gap_suspected=False)
        guarded = TS.completion_vs_resting(
            held_cost_px=held_px, resting_completion_px=resting_px,
            resting_still_live=True, decision=raw)
        row = {"t": t, "t_rel_s": round(t - t0, 3), "phase": phase,
               "bid": s.get("bid"), "ask": s.get("ask"),
               "reprice": {"action": aware.get("action"),
                           "reprice": bool(aware.get("reprice")),
                           "aware_px": aware.get("aware_px"),
                           "reason": aware.get("reason")},
               "cross_raw": {"ok": bool(raw.get("ok")),
                             "reason": raw.get("reason"),
                             "price": raw.get("price"),
                             "qty": raw.get("qty"),
                             "taker_fee_usd": raw.get("taker_fee_usd")},
               "cross_guarded": {"ok": bool(guarded.get("vs_resting_ok")),
                                 "reason": guarded.get("vs_resting_reason"),
                                 "completed_pair":
                                     guarded.get("completed_pair")}}
        for k, v in (("REPRICE", aware.get("action")),
                     ("CROSS_RAW", raw.get("reason")),
                     ("CROSS_GUARDED", guarded.get("vs_resting_reason"))):
            key = "%s:%s" % (k, v)
            census[key] = census.get(key, 0) + 1
        if aware.get("reprice") and first["REPRICE"] is None:
            aw = _d(aware.get("aware_px"))
            if defic == "UP":
                execu = a is not None and aw is not None and aw < a
                mf = queue_fill(trd, t_join=t, px=aw, side="bid",
                                queue_ahead=rung_depth(s, "bids", aw)) \
                    if aw is not None else None
            else:
                aw_ask = (D(1) - aw) if aw is not None else None
                execu = (b is not None and aw_ask is not None
                         and aw_ask > b)
                mf = queue_fill(trd, t_join=t, px=aw_ask, side="ask",
                                queue_ahead=rung_depth(s, "asks", aw_ask)) \
                    if aw_ask is not None else None
            first["REPRICE"] = {"t_rel_s": round(t - t0, 3),
                                "aware_px": aware.get("aware_px"),
                                "executable": bool(execu),
                                "modelled_fill": mf}
        if guarded.get("vs_resting_ok") and first["CROSS"] is None:
            first["CROSS"] = {"t_rel_s": round(t - t0, 3),
                              "price": raw.get("price"),
                              "qty": raw.get("qty"),
                              "taker_fee_usd": raw.get("taker_fee_usd"),
                              "completed_pair": guarded.get("completed_pair")}
        # THE BASELINE. The deployed path consults completion_decision only in
        # the COMPLETION phase and applies no resting-quote guard, so that is
        # exactly what BLIND is scored on.
        if (phase == "COMPLETION" and raw.get("ok")
                and first["BLIND"] is None):
            first["BLIND"] = {"t_rel_s": round(t - t0, 3),
                              "price": raw.get("price"),
                              "qty": raw.get("qty"),
                              "taker_fee_usd": raw.get("taker_fee_usd")}
        decisions.append(row)
    rec["decisions_evaluated"] = len(decisions)
    rec["refusal_census"] = census
    rec["first_action"] = first
    rec["decisions"] = decisions

    # ---- SETTLEMENT-ADJUSTED NET PER POLICY, one fee model throughout.
    yv = _d(yes_value)
    held_cash = _leg_cash(qty, held_px, maker=True)

    def book(policy: str) -> Dict[str, Any]:
        """Resulting book under `policy`, priced once, the same way."""
        got_up = up_q
        got_dn = dn_q
        cash_up = held_cash if held_is_up else D(0)
        cash_dn = D(0) if held_is_up else held_cash
        act = None
        if policy in ("CROSS", "BLIND") and first[policy]:
            px = _d(first[policy]["price"])
            cq = _d(first[policy]["qty"]) or D(0)
            # a cross is immediate at the displayed price: no queue to clear
            if px is not None and cq > 0:
                c = _leg_cash(cq, px, maker=False)
                if defic == "UP":
                    got_up += cq
                    cash_up += c
                else:
                    got_dn += cq
                    cash_dn += c
                act = "CROSS@%s" % px
        elif policy == "REPRICE" and first["REPRICE"]:
            r = first["REPRICE"]
            mf = r.get("modelled_fill") or {}
            if r.get("executable") and mf.get("filled"):
                px = _d(r.get("aware_px"))
                c = _leg_cash(qty, px, maker=True)
                if defic == "UP":
                    got_up += qty
                    cash_up += c
                else:
                    got_dn += qty
                    cash_dn += c
                act = "REPRICE@%s" % px
        if act is None:
            # HOLD, and every policy that declined to act, keeps the resting
            # quote -- which fills only if its own queue cleared.
            other = f_dn if defic == "DOWN" else f_up
            if other.get("filled"):
                c = _leg_cash(qty, resting_px, maker=True)
                if defic == "UP":
                    got_up += qty
                    cash_up += c
                else:
                    got_dn += qty
                    cash_dn += c
                act = "RESTING_FILLED@%s" % resting_px
            else:
                act = "ONE_SIDED_CARRIED"
        out = settlement_net(qty_up=got_up, qty_dn=got_dn, cash_up=cash_up,
                             cash_dn=cash_dn, yes_value=yv)
        return dict(out, action=act, fee_model="maker=%s taker=%s" % (
            TS.MAKER_THETA, TS.TAKER_THETA))

    nets = {p: book(p) for p in POLICIES}
    rec["net"] = nets
    base = nets["BLIND"].get("net_usd")
    for p in POLICIES:
        n = nets[p].get("net_usd")
        nets[p]["vs_blind_usd"] = (None if n is None or base is None
                                   else str(D(n) - D(base)))
    # the policy the AWARE engine would actually run: cross only when the
    # guard allows it, else reprice when proposed, else hold.
    rec["selected"] = ("CROSS" if first["CROSS"] else
                       "REPRICE" if first["REPRICE"] else "HOLD")
    rec["ok"] = True
    return rec


def summarize(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Cumulative result across consecutive completed windows."""
    tot: Dict[str, Any] = {"windows": 0, "eligible": 0, "settled": 0,
                           "outcomes": {}, "selected": {},
                           "one_sided_s": [], "net_usd": {},
                           "pending_residual": 0}
    for p in POLICIES:
        tot["net_usd"][p] = D(0)
    for r in records:
        tot["windows"] += 1
        if not r.get("ok"):
            continue
        tot["eligible"] += 1
        o = r.get("outcome") or "NEITHER"
        tot["outcomes"][o] = tot["outcomes"].get(o, 0) + 1
        sel = r.get("selected") or "HOLD"
        tot["selected"][sel] = tot["selected"].get(sel, 0) + 1
        if r.get("one_sided_s") is not None:
            tot["one_sided_s"].append(r["one_sided_s"])
        nets = r.get("net") or {}
        if any((nets.get(p) or {}).get("net_usd") is None for p in POLICIES):
            tot["pending_residual"] += 1
            continue            # an unsettled residual is NOT scored as zero
        tot["settled"] += 1
        for p in POLICIES:
            tot["net_usd"][p] += D(nets[p]["net_usd"])
    tot["net_usd"] = {p: str(v) for p, v in tot["net_usd"].items()}
    base = D(tot["net_usd"]["BLIND"])
    tot["vs_blind_usd"] = {p: str(D(tot["net_usd"][p]) - base)
                           for p in POLICIES}
    os_ = sorted(tot["one_sided_s"])
    tot["one_sided_s"] = {"n": len(os_),
                          "median": (os_[len(os_) // 2] if os_ else None),
                          "max": (os_[-1] if os_ else None)}
    tot["labels"] = ("MODELLED, SHADOW ONLY. No order was submitted, no fill "
                     "occurred and none of these figures is realized profit. "
                     "Windows with an unsettled residual are counted in "
                     "pending_residual and excluded from net_usd rather than "
                     "marked at a price.")
    return tot
