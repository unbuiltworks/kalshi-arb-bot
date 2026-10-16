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
                    decisions_evaluated=0, refusal_census={},
                    first_action={"REPRICE": None, "CROSS": None,
                                  "BLIND": None},
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


# ------------------------------------------- the same evaluation, INCREMENTALLY

class WindowAccumulator:
    """`evaluate_window`'s result, built from a live event stream in O(1) state.

    WHY THIS EXISTS. Scoring a window at rollover from retained history needs
    the whole window: measured on 2026-10-16, a BTC15m window carries
    **5,100-6,000 ladder snapshots and 3,700-4,500 trade prints**. The
    recorder retains 240 snapshots by default (hard cap 1,000) and the last
    500 trades, so a rollover-time evaluation would need that history raised
    six- to nine-fold -- a real change to a healthy read-only service.

    It does not need the history. Every quantity the record reports is either
    fixed at the open (the quote, the queues ahead) or a running tally (cumulative
    qualifying volume per resting price, the refusal census, the first action
    of each policy). So this holds the OPENING snapshot and a handful of
    counters -- a few kilobytes per market, independent of window length --
    and `finish()` returns the same record shape `evaluate_window` returns.

    FEED IT IN TIMESTAMP ORDER. `on_book` and `on_trade` assume the order the
    recorder actually sees events in. A print delivered before the book
    snapshot that preceded it would be counted against the wrong queue, so
    out-of-order input is dropped and counted rather than guessed at.

    RESTARTS FAIL CLOSED. An accumulator that never saw the opening snapshot
    cannot know the quote or the queues ahead, and `finish()` refuses with
    `MISSED_WINDOW_OPEN` instead of scoring a partial window as a complete
    one. A window a restart interrupted is absent from the results, never
    present and wrong.
    """

    def __init__(self, slug: str, *, tick: Any, min_qty: Any,
                 depth_ticks: int, cash_envelope: Any,
                 max_worst_case_loss: Any, book_age_s: Any = D("0.5"),
                 open_grace_s: float = 30.0):
        self.slug = str(slug)
        self.lim = dict(tick=tick, min_qty=min_qty, depth_ticks=depth_ticks,
                        cash_envelope=cash_envelope,
                        max_worst_case_loss=max_worst_case_loss)
        self.book_age_s = book_age_s
        self.open_grace_s = float(open_grace_s)
        self.t_start = window_start_epoch(slug)
        self.opened = False
        self.refused: Optional[Dict[str, Any]] = None
        self.last_t: Optional[float] = None
        self.out_of_order = 0
        self.books = 0
        self.prints = 0
        # fixed at the open
        self.t0 = None; self.qty = None
        self.up_px = None; self.dn_px = None; self.dn_ask = None
        self.q_up = None; self.q_dn = None
        self.plan_reason = None
        # running
        self._cum_up = D(0); self._cum_dn = D(0)
        self._n_up = 0; self._n_dn = 0
        self.f_up = None; self.f_dn = None
        self._rp_px = None; self._rp_side = None; self._rp_from = None
        self._rp_queue = None; self._rp_cum = D(0); self._rp_n = 0
        self._rp_fill_t = None; self._rp_exec = None
        self.census: Dict[str, int] = {}
        self.first = {"REPRICE": None, "CROSS": None, "BLIND": None}
        self.evaluated = 0

    # -------------------------------------------------------------- ingestion

    def _stale(self, t: float) -> bool:
        if self.last_t is not None and t < self.last_t:
            self.out_of_order += 1
            return True
        self.last_t = t
        return False

    def on_book(self, snap: Mapping[str, Any]) -> None:
        t = snap.get("t")
        if t is None or self._stale(float(t)):
            return
        self.books += 1
        if not self.opened and self.refused is None:
            self._open(snap)
            return
        if self.refused is not None:
            return
        self._decide(snap)

    def on_trade(self, t: Any, px: Any, qty: Any) -> None:
        tt = None if t is None else float(t)
        if tt is None or self._stale(tt):
            return
        p = _d(px); q = _d(qty)
        if p is None or q is None or not self.opened or self.refused:
            return
        self.prints += 1
        if self.f_up is None and p <= self.up_px:
            self._cum_up += q; self._n_up += 1
            if self._cum_up > self.q_up:
                self.f_up = {"filled": True, "t": tt,
                             "queue_ahead": str(self.q_up),
                             "cum_qualifying": str(self._cum_up),
                             "prints_counted": self._n_up, "modelled": True}
        if self.f_dn is None and p >= self.dn_ask:
            self._cum_dn += q; self._n_dn += 1
            if self._cum_dn > self.q_dn:
                self.f_dn = {"filled": True, "t": tt,
                             "queue_ahead": str(self.q_dn),
                             "cum_qualifying": str(self._cum_dn),
                             "prints_counted": self._n_dn, "modelled": True}
        if (self._rp_px is not None and self._rp_fill_t is None
                and self._rp_queue is not None and tt >= self._rp_from):
            hit = (p <= self._rp_px) if self._rp_side == "bid" \
                else (p >= self._rp_px)
            if hit:
                self._rp_cum += q; self._rp_n += 1
                if self._rp_cum > self._rp_queue:
                    self._rp_fill_t = tt

    # ----------------------------------------------------------------- stages

    def _open(self, snap: Mapping[str, Any]) -> None:
        t = float(snap["t"])
        if self.t_start is None:
            self.refused = {"ok": False, "reason": "WINDOW_START_UNREADABLE"}
            return
        if t - self.t_start > self.open_grace_s:
            # a restart mid-window: the opening book is gone for good
            self.refused = {"ok": False, "reason": "MISSED_WINDOW_OPEN",
                            "first_book_t_rel_s": round(t - self.t_start, 3),
                            "open_grace_s": self.open_grace_s}
            return
        b = _d(snap.get("bid")); a = _d(snap.get("ask"))
        plan = TS.build_orders(
            market=self.slug, bid=snap.get("bid"), ask=snap.get("ask"),
            bid_depth=(rung_depth(snap, "bids", b) if b is not None else None),
            ask_depth=(rung_depth(snap, "asks", a) if a is not None else None),
            book_age_s=self.book_age_s, **self.lim)
        self.plan_reason = plan.get("reason")
        if not plan.get("ok"):
            self.refused = {"ok": False, "reason": "NOT_ELIGIBLE",
                            "detail": plan.get("reason")}
            return
        legs = {str(l.get("side")): l for l in (plan.get("legs") or [])}
        self.qty = _d(plan["qty_per_leg"])
        self.up_px = _d((legs.get("UP") or {}).get("price"))
        self.dn_px = _d((legs.get("DOWN") or {}).get("price"))
        if self.qty is None or self.up_px is None or self.dn_px is None:
            self.refused = {"ok": False, "reason": "PLAN_UNREADABLE"}
            return
        self.dn_ask = D(1) - self.dn_px
        self.q_up = rung_depth(snap, "bids", self.up_px)
        self.q_dn = rung_depth(snap, "asks", self.dn_ask)
        if self.q_up is None:
            self.f_up = {"filled": False, "t": None, "queue_ahead": None,
                         "cum_qualifying": "0", "prints_counted": 0,
                         "modelled": True,
                         "reason": "QUEUE_AHEAD_UNREADABLE"}
        if self.q_dn is None:
            self.f_dn = {"filled": False, "t": None, "queue_ahead": None,
                         "cum_qualifying": "0", "prints_counted": 0,
                         "modelled": True,
                         "reason": "QUEUE_AHEAD_UNREADABLE"}
        self.t0 = t
        self.opened = True

    def _acquired(self):
        """(t_acq, t_second, held_is_up) once one leg has modelled a fill."""
        fu = self.f_up if (self.f_up or {}).get("filled") else None
        fd = self.f_dn if (self.f_dn or {}).get("filled") else None
        if not fu and not fd:
            return None
        if fu and fd:
            held_up = fu["t"] <= fd["t"]
            return (min(fu["t"], fd["t"]), max(fu["t"], fd["t"]), held_up)
        return ((fu or fd)["t"], None, bool(fu))

    def _decide(self, snap: Mapping[str, Any]) -> None:
        acq = self._acquired()
        if acq is None:
            return
        t_acq, t_second, held_up = acq
        t = float(snap["t"])
        if t <= t_acq:
            return
        if t_second is not None and t > t_second:
            return
        if t >= TS.cancel_cutoff_from_start(float(self.t_start)):
            return
        tk = _d(self.lim["tick"])
        defic = "DOWN" if held_up else "UP"
        held_px = self.up_px if held_up else self.dn_px
        resting_px = self.dn_px if held_up else self.up_px
        up_q = self.qty if held_up else D(0)
        dn_q = D(0) if held_up else self.qty
        up_c = self.up_px if held_up else None
        dn_c = None if held_up else self.dn_px
        b = _d(snap.get("bid")); a = _d(snap.get("ask"))
        aware = TS.inventory_aware_quote(
            up_qty=up_q, dn_qty=dn_q, up_cost_px=up_c, dn_cost_px=dn_c,
            blind_up_px=self.up_px, blind_dn_px=self.dn_px, tick=tk)
        if defic == "UP":
            taker_px = a
            taker_depth = rung_depth(snap, "asks", a) if a is not None else None
        else:
            taker_px = (D(1) - b) if b is not None else None
            taker_depth = rung_depth(snap, "bids", b) if b is not None else None
        raw = TS.completion_decision(
            up_qty=up_q, dn_qty=dn_q, up_cost_px=up_c, dn_cost_px=dn_c,
            taker_price=taker_px, available_depth=taker_depth,
            tick=tk, min_qty=self.lim["min_qty"],
            cash_headroom=self.lim["cash_envelope"],
            worst_case_ceiling=self.lim["max_worst_case_loss"],
            book_age_s=self.book_age_s, gap_suspected=False)
        guarded = TS.completion_vs_resting(
            held_cost_px=held_px, resting_completion_px=resting_px,
            resting_still_live=True, decision=raw)
        for k, v in (("REPRICE", aware.get("action")),
                     ("CROSS_RAW", raw.get("reason")),
                     ("CROSS_GUARDED", guarded.get("vs_resting_reason"))):
            key = "%s:%s" % (k, v)
            self.census[key] = self.census.get(key, 0) + 1
        if aware.get("reprice") and self.first["REPRICE"] is None:
            aw = _d(aware.get("aware_px"))
            if defic == "UP":
                execu = a is not None and aw is not None and aw < a
                self._rp_px = aw; self._rp_side = "bid"
                self._rp_queue = (rung_depth(snap, "bids", aw)
                                  if aw is not None else None)
            else:
                aw_ask = (D(1) - aw) if aw is not None else None
                execu = b is not None and aw_ask is not None and aw_ask > b
                self._rp_px = aw_ask; self._rp_side = "ask"
                self._rp_queue = (rung_depth(snap, "asks", aw_ask)
                                  if aw_ask is not None else None)
            self._rp_from = t; self._rp_exec = bool(execu)
            self.first["REPRICE"] = {"t_rel_s": round(t - self.t0, 3),
                                     "aware_px": aware.get("aware_px"),
                                     "executable": bool(execu)}
        if guarded.get("vs_resting_ok") and self.first["CROSS"] is None:
            self.first["CROSS"] = {
                "t_rel_s": round(t - self.t0, 3), "price": raw.get("price"),
                "qty": raw.get("qty"),
                "taker_fee_usd": raw.get("taker_fee_usd"),
                "completed_pair": guarded.get("completed_pair")}
        if (TS.completion_phase(t, float(self.t_start)) == "COMPLETION"
                and raw.get("ok") and self.first["BLIND"] is None):
            self.first["BLIND"] = {
                "t_rel_s": round(t - self.t0, 3), "price": raw.get("price"),
                "qty": raw.get("qty"),
                "taker_fee_usd": raw.get("taker_fee_usd")}
        self.evaluated += 1

    # ------------------------------------------------------------------ result

    def finish(self, *, yes_value: Any = None) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "market": self.slug, "shadow_only": True, "submitted": False,
            "fills_are_modelled": True, "incremental": True,
            "books_seen": self.books, "prints_seen": self.prints,
            "out_of_order_dropped": self.out_of_order}
        if self.refused is not None:
            if self.refused.get("reason") == "NOT_ELIGIBLE":
                rec["eligibility"] = {"ok": False, "reason": self.plan_reason}
            return dict(rec, **self.refused)
        if not self.opened:
            return dict(rec, ok=False, reason="MISSED_WINDOW_OPEN")
        rec["eligibility"] = {"ok": True, "reason": self.plan_reason}
        rec["quote"] = {
            "qty": str(self.qty), "up_px": str(self.up_px),
            "dn_px": str(self.dn_px), "dn_rests_as_ask_at": str(self.dn_ask),
            "pair_cost": str(self.up_px + self.dn_px),
            "depth_ticks": int(self.lim["depth_ticks"]),
            "cash_envelope": str(_d(self.lim["cash_envelope"])),
            "worst_case_ceiling": str(_d(self.lim["max_worst_case_loss"]))}
        f_up = self.f_up or {"filled": False, "t": None,
                             "queue_ahead": str(self.q_up),
                             "cum_qualifying": str(self._cum_up),
                             "prints_counted": self._n_up, "modelled": True,
                             "reason": "QUEUE_NEVER_CLEARED"}
        f_dn = self.f_dn or {"filled": False, "t": None,
                             "queue_ahead": str(self.q_dn),
                             "cum_qualifying": str(self._cum_dn),
                             "prints_counted": self._n_dn, "modelled": True,
                             "reason": "QUEUE_NEVER_CLEARED"}
        rec["resting"] = {"up": f_up, "dn": f_dn}
        rec["refusal_census"] = dict(self.census)
        rec["decisions_evaluated"] = self.evaluated
        acq = self._acquired()
        yv = _d(yes_value)
        if acq is None:
            return dict(rec, ok=True, outcome="NEITHER", one_sided_s=None,
                        selected="HOLD",
                        first_action=dict(self.first),
                        net={p: settlement_net(qty_up=D(0), qty_dn=D(0),
                                               cash_up=D(0), cash_dn=D(0),
                                               yes_value=yv)
                             for p in POLICIES})
        t_acq, t_second, held_up = acq
        if t_second is not None:
            rec["outcome"] = "BOTH"
        else:
            rec["outcome"] = "UP_ONLY" if held_up else "DOWN_ONLY"
        rec["one_sided_s"] = (None if t_second is None
                              else round(t_second - t_acq, 3))
        defic = "DOWN" if held_up else "UP"
        held_px = self.up_px if held_up else self.dn_px
        resting_px = self.dn_px if held_up else self.up_px
        rec["inventory_after_acquisition"] = {
            "held_side": "UP" if held_up else "DOWN",
            "held_qty": str(self.qty),
            "held_acquisition_px": str(held_px),
            "deficient_side": defic, "resting_px": str(resting_px),
            "resting_order_live": True}
        first = {k: (dict(v) if v else None) for k, v in self.first.items()}
        if first["REPRICE"] is not None:
            first["REPRICE"]["modelled_fill"] = {
                "filled": self._rp_fill_t is not None,
                "t": self._rp_fill_t,
                "queue_ahead": (None if self._rp_queue is None
                                else str(self._rp_queue)),
                "cum_qualifying": str(self._rp_cum),
                "prints_counted": self._rp_n, "modelled": True}
        rec["first_action"] = first
        held_cash = _leg_cash(self.qty, held_px, maker=True)
        other_filled = bool((f_dn if defic == "DOWN" else f_up).get("filled"))

        def book(policy: str) -> Dict[str, Any]:
            got_up = self.qty if held_up else D(0)
            got_dn = D(0) if held_up else self.qty
            cash_up = held_cash if held_up else D(0)
            cash_dn = D(0) if held_up else held_cash
            act = None
            if policy in ("CROSS", "BLIND") and first[policy]:
                px = _d(first[policy]["price"])
                cq = _d(first[policy]["qty"]) or D(0)
                if px is not None and cq > 0:
                    c = _leg_cash(cq, px, maker=False)
                    if defic == "UP":
                        got_up += cq; cash_up += c
                    else:
                        got_dn += cq; cash_dn += c
                    act = "CROSS@%s" % px
            elif policy == "REPRICE" and first["REPRICE"]:
                r = first["REPRICE"]
                if r.get("executable") and (r.get("modelled_fill")
                                            or {}).get("filled"):
                    px = _d(r.get("aware_px"))
                    c = _leg_cash(self.qty, px, maker=True)
                    if defic == "UP":
                        got_up += self.qty; cash_up += c
                    else:
                        got_dn += self.qty; cash_dn += c
                    act = "REPRICE@%s" % px
            if act is None:
                if other_filled:
                    c = _leg_cash(self.qty, resting_px, maker=True)
                    if defic == "UP":
                        got_up += self.qty; cash_up += c
                    else:
                        got_dn += self.qty; cash_dn += c
                    act = "RESTING_FILLED@%s" % resting_px
                else:
                    act = "ONE_SIDED_CARRIED"
            out = settlement_net(qty_up=got_up, qty_dn=got_dn,
                                 cash_up=cash_up, cash_dn=cash_dn,
                                 yes_value=yv)
            return dict(out, action=act, fee_model="maker=%s taker=%s" % (
                TS.MAKER_THETA, TS.TAKER_THETA))

        nets = {p: book(p) for p in POLICIES}
        base = nets["BLIND"].get("net_usd")
        for p in POLICIES:
            n = nets[p].get("net_usd")
            nets[p]["vs_blind_usd"] = (None if n is None or base is None
                                       else str(D(n) - D(base)))
        rec["net"] = nets
        rec["selected"] = ("CROSS" if first["CROSS"] else
                           "REPRICE" if first["REPRICE"] else "HOLD")
        rec["ok"] = True
        return rec


# ------------------------------------------------- the per-process shadow log

#: where the record store lives. Default is the recorder's mounted volume, so
#: records survive a restart and a redeploy.
STORE_ENV = "L5_SHADOW_POLICY_STORE"
DEFAULT_STORE = "/state/pmus-ws/l5_shadow_policy.jsonl"
#: OFF unless explicitly YES. An unreadable value reads as OFF.
ENABLE_ENV = "L5_SHADOW_POLICY_EVAL"


def enabled() -> bool:
    import os
    return str(os.getenv(ENABLE_ENV) or "").strip().upper() == "YES"


def store_path() -> str:
    import os
    return str(os.getenv(STORE_ENV) or DEFAULT_STORE)


class ShadowPolicyLog:
    """Owns one accumulator per live window; finishes each at rollover.

    IDEMPOTENT ACROSS RESTARTS. The store is read once at construction and a
    slug already present is never scored again, so a redeploy mid-session
    cannot double-count a window, and a window interrupted by a restart is
    refused `MISSED_WINDOW_OPEN` rather than scored from a partial book.

    OFF BY DEFAULT and READ-ONLY. It formats data the process already holds.
    It opens no socket, signs nothing, and has no path to an order.
    """

    def __init__(self, *, tick: Any, min_qty: Any, depth_ticks: int,
                 cash_envelope: Any, max_worst_case_loss: Any,
                 path: Optional[str] = None):
        self.lim = dict(tick=tick, min_qty=min_qty, depth_ticks=depth_ticks,
                        cash_envelope=cash_envelope,
                        max_worst_case_loss=max_worst_case_loss)
        self.path = path or store_path()
        self.live: Dict[str, WindowAccumulator] = {}
        self.done: set = set()
        self.errors = 0
        self._load()

    def _load(self) -> None:
        import json
        try:
            with open(self.path) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("market"):
                        self.done.add(str(r["market"]))
        except (OSError, ValueError):
            pass            # no store yet, or an unusable path, is not a crash

    def _append(self, rec: Mapping[str, Any]) -> None:
        import json
        import os
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except (OSError, ValueError):
            self.errors += 1   # a store we cannot write is NOT a crash

    def observe_book(self, slug: str, snap: Mapping[str, Any]) -> None:
        slug = str(slug)
        if slug in self.done:
            return
        acc = self.live.get(slug)
        if acc is None:
            acc = WindowAccumulator(slug, **self.lim)
            self.live[slug] = acc
        try:
            acc.on_book(snap)
        except Exception:
            self.errors += 1

    def observe_trade(self, slug: str, t: Any, px: Any, qty: Any) -> None:
        acc = self.live.get(str(slug))
        if acc is None:
            return
        try:
            acc.on_trade(t, px, qty)
        except Exception:
            self.errors += 1

    def rollover(self, keep: Any, *, now: Optional[float] = None,
                 settlement: Optional[Mapping[str, Any]] = None) -> list:
        """Finish and persist every window no longer in `keep`.

        `keep` is the recorder's OWN ladder allowlist, so a window is scored
        exactly when the recorder stops retaining its rungs -- one rollover
        rule, not a second clock to disagree with the first.
        """
        out = []
        for slug in [s for s in self.live if s not in set(keep or ())]:
            acc = self.live.pop(slug)
            try:
                rec = acc.finish(
                    yes_value=(settlement or {}).get(slug))
            except Exception:
                self.errors += 1
                continue
            self.done.add(slug)
            self._append(rec)
            out.append(rec)
        return out

    def emit_document(self, records: Sequence[Mapping[str, Any]]) -> dict:
        """The one log line a finished window produces. Deliberately small."""
        return {"rows": len(records), "store": self.path,
                "store_errors": self.errors,
                "windows": [{
                    "market": r.get("market"),
                    "ok": r.get("ok"),
                    "reason": r.get("reason"),
                    "outcome": r.get("outcome"),
                    "one_sided_s": r.get("one_sided_s"),
                    "selected": r.get("selected"),
                    "decisions_evaluated": r.get("decisions_evaluated"),
                    "net_usd": {p: (r.get("net") or {}).get(p, {}).get(
                        "net_usd") for p in POLICIES},
                    "vs_blind_usd": (r.get("net") or {}).get(
                        "CROSS", {}).get("vs_blind_usd"),
                } for r in records],
                "labels": ("MODELLED, SHADOW ONLY. Nothing was submitted and "
                           "no figure here is realized profit.")}


# ----------------------------------- settlement: modelled vs venue-confirmed

#: how a record's net came to be. These are the only three answers.
BASIS_MODELLED_MATCHED = "MODELLED_EXECUTION_OUTCOME_INDEPENDENT"
BASIS_VENUE_SETTLED = "MODELLED_EXECUTION_VENUE_SETTLED_RESIDUAL"
BASIS_PENDING = "PENDING_SETTLEMENT"

#: the only settlement evidence accepted. A book price at expiry is not one.
POSITIVE_SETTLEMENT_SOURCES = ("VENUE_SETTLEMENT_PX",)


def settlement_basis(rec: Mapping[str, Any]) -> str:
    """Which of the three bases a record's net rests on.

    THE DISTINCTION THAT MATTERS. Every fill in every record is MODELLED --
    that never changes and is never upgraded by settlement. What settlement
    changes is only whether the RESIDUAL leg can be priced. A matched pair
    needs no settlement at all, because it pays a dollar on either outcome;
    an unmatched leg cannot be priced without the venue saying which outcome
    paid. Calling the first one "settled" without this field invites reading
    an outcome-independent arithmetic identity as a venue confirmation, which
    it is not.
    """
    nets = (rec or {}).get("net") or {}
    hold = nets.get("HOLD") or {}
    if hold.get("pending_residual"):
        return BASIS_PENDING
    if (rec or {}).get("settlement_evidence"):
        return BASIS_VENUE_SETTLED
    if hold.get("net_usd") is None:
        return BASIS_PENDING
    res_up = _d(hold.get("residual_up")) or D(0)
    res_dn = _d(hold.get("residual_dn")) or D(0)
    if res_up == 0 and res_dn == 0:
        return BASIS_MODELLED_MATCHED
    return BASIS_VENUE_SETTLED


def reconcile_settlement(rec: Mapping[str, Any],
                         evidence: Optional[Mapping[str, Any]]
                         ) -> Dict[str, Any]:
    """Re-price a PENDING record once positive settlement evidence exists.

    NEVER MUTATES AND NEVER DOUBLE-COUNTS. It returns a NEW record. A record
    that is not pending is returned unchanged with a reason, so running the
    reconciler twice over the same store is a no-op on the second pass --
    the second run sees a record that already carries `settlement_evidence`
    and declines it as `ALREADY_RECONCILED`.

    POSITIVE EVIDENCE ONLY. `evidence` must name a source in
    POSITIVE_SETTLEMENT_SOURCES and carry a yes_value of exactly 0 or 1. An
    expired book price, a last trade, a market marked inactive and a missing
    record are all refused -- an unresolved outcome stays unresolved.
    """
    out = dict(rec or {})
    if not out.get("ok"):
        return dict(out, reconciled=False,
                    reconcile_reason="RECORD_NOT_SCORED")
    if out.get("settlement_evidence"):
        return dict(out, reconciled=False,
                    reconcile_reason="ALREADY_RECONCILED")
    if settlement_basis(out) != BASIS_PENDING:
        return dict(out, reconciled=False,
                    reconcile_reason="NOTHING_PENDING")
    ev = dict(evidence or {})
    src = str(ev.get("resolution_source") or "")
    if src not in POSITIVE_SETTLEMENT_SOURCES:
        return dict(out, reconciled=False,
                    reconcile_reason="NO_POSITIVE_SETTLEMENT_EVIDENCE",
                    offered_source=(src or None))
    yv = _d(ev.get("yes_value"))
    if yv is None or yv not in (D(0), D(1)):
        return dict(out, reconciled=False,
                    reconcile_reason="YES_VALUE_NOT_BINARY",
                    offered_yes_value=ev.get("yes_value"))
    nets = {}
    for p, n in (out.get("net") or {}).items():
        cash = _d(n.get("cash_out_usd"))
        m = _d(n.get("matched_qty"))
        ru = _d(n.get("residual_up")); rd = _d(n.get("residual_dn"))
        if cash is None or m is None or ru is None or rd is None:
            nets[p] = dict(n)
            continue
        re_ = settlement_net(qty_up=m + ru, qty_dn=m + rd,
                             cash_up=cash, cash_dn=D(0), yes_value=yv)
        # cash was already split across the legs when the record was built;
        # only the redemption side is re-derived here, so the net is
        # redemption minus the SAME cash the record already carries.
        nets[p] = dict(n, redemption_usd=re_["redemption_usd"],
                       net_usd=re_["net_usd"], settled=True,
                       pending_residual=False)
    base = (nets.get("BLIND") or {}).get("net_usd")
    for p in nets:
        v = nets[p].get("net_usd")
        nets[p]["vs_blind_usd"] = (None if v is None or base is None
                                   else str(D(v) - D(base)))
    return dict(out, net=nets, reconciled=True, reconcile_reason=None,
                settlement_evidence={
                    "resolution_source": src, "yes_value": str(yv),
                    "observed_at": ev.get("observed_at"),
                    "labels": ("venue-confirmed OUTCOME only. The fills in "
                               "this record remain MODELLED and are not "
                               "confirmed by this evidence.")})


def dashboard(records: Sequence[Mapping[str, Any]],
              live: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The four figures, kept apart on purpose.

    THE SEPARATION IS THE POINT. A modelled matched-pair net is arithmetic on
    fills that never happened; a pending residual has no net at all and is
    carried as EXPOSURE, not as zero; a venue-settled modelled net has a
    confirmed outcome but still-modelled fills; and realized P&L is what the
    account actually did. Summing any two of these produces a number that
    means nothing, so this never sums them.
    """
    out: Dict[str, Any] = {
        "modelled_matched_pair": {"windows": 0,
                                  "net_usd": {p: D(0) for p in POLICIES}},
        "pending_residual": {"windows": 0, "exposure_usd": D(0),
                             "residual_qty": D(0), "net_usd": None},
        "venue_settled_modelled": {"windows": 0,
                                   "net_usd": {p: D(0) for p in POLICIES}},
        "not_scored": {"windows": 0, "reasons": {}},
    }
    for r in records:
        if not r.get("ok"):
            n = out["not_scored"]
            n["windows"] += 1
            k = str(r.get("reason") or r.get("detail") or "UNKNOWN")
            n["reasons"][k] = n["reasons"].get(k, 0) + 1
            continue
        basis = settlement_basis(r)
        nets = r.get("net") or {}
        if basis == BASIS_PENDING:
            p = out["pending_residual"]
            p["windows"] += 1
            hold = nets.get("HOLD") or {}
            p["exposure_usd"] += _d(hold.get("cash_out_usd")) or D(0)
            p["residual_qty"] += ((_d(hold.get("residual_up")) or D(0))
                                  + (_d(hold.get("residual_dn")) or D(0)))
            continue
        bucket = ("modelled_matched_pair"
                  if basis == BASIS_MODELLED_MATCHED
                  else "venue_settled_modelled")
        out[bucket]["windows"] += 1
        for pol in POLICIES:
            v = _d((nets.get(pol) or {}).get("net_usd"))
            if v is not None:
                out[bucket]["net_usd"][pol] += v
    for b in ("modelled_matched_pair", "venue_settled_modelled"):
        out[b]["net_usd"] = {k: str(v) for k, v in out[b]["net_usd"].items()}
        base = D(out[b]["net_usd"]["BLIND"])
        out[b]["vs_blind_usd"] = {
            k: str(D(v) - base) for k, v in out[b]["net_usd"].items()}
    p = out["pending_residual"]
    p["exposure_usd"] = str(p["exposure_usd"])
    p["residual_qty"] = str(p["residual_qty"])
    p["labels"] = ("carried as EXPOSURE, never as zero and never in a net. "
                   "Reconcile with positive venue settlement evidence.")
    out["actual_trading"] = dict({
        "submitted": 0, "filled": 0, "settled": None, "realized_usd": None,
        "source": None}, **dict(live or {}))
    out["labels"] = ("MODELLED and REALIZED are different kinds of number "
                     "and are NOT summed here. No modelled figure is "
                     "realized profit, and a pending residual has no net.")
    return out
