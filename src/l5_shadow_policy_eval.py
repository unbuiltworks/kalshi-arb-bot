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
                 open_grace_s: float = 30.0,
                 replenish: bool = False, max_generations: int = 1,
                 fixed_qty: Any = None, capital_ceiling: Any = None):
        self.slug = str(slug)
        self.lim = dict(tick=tick, min_qty=min_qty, depth_ticks=depth_ticks,
                        cash_envelope=cash_envelope,
                        max_worst_case_loss=max_worst_case_loss)
        self.book_age_s = book_age_s
        self.open_grace_s = float(open_grace_s)
        #: REPLENISHMENT, off by default so the deployed behaviour is
        #: unchanged. L5 takes a median 133 fills per BTC15m window; this
        #: accumulator models 2 and then stops, which is the behavioural
        #: bottleneck section 28 measures. With replenish on, a new quote
        #: generation is opened at the CURRENT book once both legs of the
        #: previous one have modelled fills, so turnover can be compared
        #: like for like instead of against a single pair.
        self.replenish = bool(replenish)
        self.max_generations = max(1, int(max_generations))
        self.generations: list = []
        #: a fixed clip size, when the experiment fixes one instead of
        #: letting build_orders size to the envelope.
        self.fixed_qty = _d(fixed_qty)
        #: TOTAL committed capital allowed across the whole window. Charged
        #: on the engine's OWN basis (`cash_gate_obligation_usd`, which
        #: prices a resting DOWN leg at a dollar a share) and NEVER released
        #: by a completed pair: nothing settles until the window ends, so
        #: this only ever goes up.
        self.capital_ceiling = _d(capital_ceiling)
        self.committed = D(0)
        self.capital_refusals = 0
        #: set once replenishment has stopped. Without it `on_book` falls
        #: back into the WINDOW-open path, re-applies the 30s grace check
        #: mid-window and reports MISSED_WINDOW_OPEN for a window that in
        #: fact quoted and filled -- which is exactly what it did.
        self.done_quoting = False
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
            if self.done_quoting:
                return            # quoting is over; keep ingesting prints
            self._open(snap)
            return
        if self.refused is not None:
            return
        self._decide(snap)
        self._maybe_replenish(snap)

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

    def _open(self, snap: Mapping[str, Any], *,
              replenishing: bool = False) -> None:
        t = float(snap["t"])
        if self.t_start is None:
            self.refused = {"ok": False, "reason": "WINDOW_START_UNREADABLE"}
            return
        # THE GRACE CHECK GUARDS THE WINDOW OPEN, NOT A RE-QUOTE. A
        # replenishment quote is placed mid-window on purpose; applying the
        # same 30s bound to it refuses every generation after the first and
        # silently turns replenishment off, which is exactly what it did the
        # first time this was wired.
        if not replenishing and t - self.t_start > self.open_grace_s:
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
        if self.fixed_qty is not None:
            if self.fixed_qty > self.qty:
                # the experiment's clip does not fit what the engine's own
                # sizing allows here; refuse rather than quietly oversize.
                self.refused = {"ok": False, "reason": "CLIP_EXCEEDS_ENGINE_SIZE",
                                "engine_qty": str(self.qty),
                                "clip": str(self.fixed_qty)}
                return
            self.qty = self.fixed_qty
        self.up_px = _d((legs.get("UP") or {}).get("price"))
        self.dn_px = _d((legs.get("DOWN") or {}).get("price"))
        if self.qty is None or self.up_px is None or self.dn_px is None:
            self.refused = {"ok": False, "reason": "PLAN_UNREADABLE"}
            return
        oblig = self.qty * (D(1) + self.up_px)      # the engine's cash basis
        if (self.capital_ceiling is not None
                and self.committed + oblig > self.capital_ceiling):
            self.capital_refusals += 1
            self.refused = {"ok": False, "reason": "CAPITAL_CEILING",
                            "committed_usd": str(self.committed),
                            "needed_usd": str(oblig),
                            "ceiling_usd": str(self.capital_ceiling)}
            return
        self.committed += oblig
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

    def _close_generation(self) -> dict:
        """Freeze the generation just completed. Pure bookkeeping."""
        fu = self.f_up or {}; fd = self.f_dn or {}
        return {"index": len(self.generations),
                "qty": str(self.qty),
                "up_px": str(self.up_px), "dn_px": str(self.dn_px),
                "up_filled": bool(fu.get("filled")),
                "dn_filled": bool(fd.get("filled")),
                "up_t": fu.get("t"), "dn_t": fd.get("t"),
                "up_queue": fu.get("queue_ahead"),
                "dn_queue": fd.get("queue_ahead")}

    def _maybe_replenish(self, snap: Mapping[str, Any]) -> None:
        """Open the next quote generation once this one is fully filled.

        MODELLED, and bounded by `max_generations`. Each generation deploys
        the SAME authorized envelope again, so `finish()` reports cumulative
        cash deployed: the comparison is faithful only if that number is
        read alongside it.
        """
        if not self.replenish or self.refused is not None or self.done_quoting:
            return
        if len(self.generations) + 1 >= self.max_generations:
            return
        if not ((self.f_up or {}).get("filled")
                and (self.f_dn or {}).get("filled")):
            return
        t = float(snap["t"])
        if t >= TS.cancel_cutoff_from_start(float(self.t_start)):
            return
        self.generations.append(self._close_generation())
        prev_books = self.books
        # reset ONLY the per-generation quote state; counters and census
        # carry across so the window still reads as one window.
        self.opened = False
        self.f_up = self.f_dn = None
        self._cum_up = self._cum_dn = D(0)
        self._n_up = self._n_dn = 0
        self._rp_px = self._rp_side = self._rp_from = None
        self._rp_queue = None; self._rp_cum = D(0); self._rp_n = 0
        self._rp_fill_t = None; self._rp_exec = None
        self.first = {"REPRICE": None, "CROSS": None, "BLIND": None}
        self._open(snap, replenishing=True)
        if self.refused is not None:          # a refusal ENDS replenishment
            self.last_stop = dict(self.refused)
            self.refused = None               # without voiding the window
            self.opened = False
            self.done_quoting = True
        self.books = prev_books

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
        if not self.opened and not self.generations:
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
        gens = list(self.generations)
        if self.opened and ((self.f_up or {}).get("filled")
                            or (self.f_dn or {}).get("filled")):
            gens = gens + [self._close_generation()]
        legs = sum(int(g["up_filled"]) + int(g["dn_filled"]) for g in gens)
        matched = sum(D(g["qty"]) for g in gens
                      if g["up_filled"] and g["dn_filled"])
        cash = sum(D(g["qty"]) * (D(g["up_px"]) + D(g["dn_px"])) for g in gens
                   if g["up_filled"] and g["dn_filled"])
        rec["process"] = {
            "replenish": self.replenish,
            "committed_capital_usd": str(self.committed),
            "capital_ceiling_usd": (None if self.capital_ceiling is None
                                    else str(self.capital_ceiling)),
            "capital_refusals": self.capital_refusals,
            "stopped_because": (getattr(self, "last_stop", None) or {}).get("reason"),
            "gens_detail": gens,
            "generations": len(gens),
            "modelled_legs_filled": legs,
            "matched_qty": str(matched),
            "cash_deployed_usd": str(cash),
            "labels": ("MODELLED turnover. Each generation redeploys the "
                       "SAME authorized envelope, so cash_deployed_usd is "
                       "the number that must be read beside the turnover.")}
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
        self.settled: set = set()
        self.latest: Dict[str, Any] = {}
        self.attempts: Dict[str, int] = {}
        self.errors = 0
        self.reconcile_errors = 0
        self._load()

    def _load(self) -> None:
        """Rebuild `done`, `latest` and `settled` from the append-only store.

        LAST LINE WINS per market, because a reconciliation is APPENDED
        rather than written over the original: the scoring record stays on
        disk exactly as it was emitted, and the reconciled view is a later
        line that supersedes it. Reading the last one is what makes a
        restart resume with the reconciliation already applied instead of
        re-doing it.
        """
        import json
        try:
            with open(self.path) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    mk = r.get("market")
                    if not mk:
                        continue
                    mk = str(mk)
                    self.done.add(mk)
                    self.latest[mk] = r
                    if r.get("settlement_evidence") or r.get(
                            "reconcile_closed"):
                        self.settled.add(mk)
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
            self.latest[slug] = rec
            self._append(rec)
            out.append(rec)
        return out

    def pending_markets(self) -> list:
        """Slugs whose residual is unresolved and still inside the budget."""
        return _pending_markets(self)

    def reconcile_pending(self, get_json, *,
                          now: Optional[float] = None) -> list:
        """Bounded, idempotent, append-only settlement reconciliation."""
        return _reconcile_pending(self, get_json, now=now)

    def records_view(self, *, limit: int = 50) -> Dict[str, Any]:
        """A bounded, allow-listed projection of the store."""
        return records_view(self, limit=limit)

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
#: retry budget spent or the settlement window lapsed. A TERMINAL state that
#: is NOT a settlement: the net stays null, no settlement_evidence is ever
#: attached, and nothing is attributed to the venue. It exists so an
#: unresolved residual is reported as unresolved rather than quietly dropped
#: out of the pending count and read as if it had settled.
BASIS_UNRESOLVED_EXPIRED = "UNRESOLVED_EXPIRED"

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
    if (rec or {}).get("reconcile_closed"):
        return BASIS_UNRESOLVED_EXPIRED
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
        "unresolved_expired": {"windows": 0, "net_usd": None,
                               "labels": ("retry budget spent or settlement "
                                          "window lapsed. Never priced, "
                                          "never attributed to the venue.")},
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
        if basis == BASIS_UNRESOLVED_EXPIRED:
            out["unresolved_expired"]["windows"] += 1
            continue
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


# ------------------------------- reading a settlement off the venue's own doc

#: the venue states that may carry a final settlement, copied from
#: l5_pmus_transport.TERMINAL_MARKET_STATES rather than imported, because
#: importing that module would pull an order-capable transport into the
#: recorder's import closure. The constant is asserted equal in tests.
TERMINAL_MARKET_STATES = ("MARKET_STATE_EXPIRED", "MARKET_STATE_SETTLED",
                          "MARKET_STATE_RESOLVED")


def settlement_from_bbo(doc: Any, slug: str) -> Dict[str, Any]:
    """Positive settlement evidence from `/v1/markets/<slug>/bbo`, or a refusal.

    THE SAME FOUR BARS THE TRANSPORT ALREADY APPLIES, and for the same
    reasons it learned them: a terminal STATE alone is not a settlement, the
    `settlementPx` field is the only admissible one on a document that also
    carries a last price and a mid, a binary contract that settled at neither
    0 nor 1 has not finished, and a document naming a different market is not
    this market's answer. Stage A booked a phantom credit because 0.01 -- the
    price an expired book still carried -- reached a ledger as a payout.

    PURE. It performs no request; the caller supplies the document.
    """
    r = doc if isinstance(doc, Mapping) else {}
    md = r.get("marketData") or r
    md = md if isinstance(md, Mapping) else {}
    state = str(md.get("state") or "")
    raw = md.get("settlementPx")
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    px = None
    if raw is not None:
        try:
            d = D(str(raw))
            px = d if D(0) <= d <= D(1) else None
        except Exception:                                      # noqa: BLE001
            px = None
    named = None
    for key in ("slug", "ticker", "marketSlug", "market"):
        v = md.get(key) or r.get(key)
        if isinstance(v, str) and v.strip():
            named = v.strip()
            break
    if state not in TERMINAL_MARKET_STATES:
        reason = "STATE_NOT_TERMINAL"
    elif px is None:
        reason = "SETTLEMENT_ABSENT"
    elif px not in (D(0), D(1)):
        reason = "SETTLEMENT_NOT_BINARY"
    elif named is not None and named != slug:
        reason = "RESOLUTION_MARKET_MISMATCH"
    else:
        reason = None
    if reason is not None:
        return {"ok": False, "reason": reason, "market": slug,
                "state": state or None}
    return {"ok": True, "reason": None, "market": slug,
            "resolution_source": "VENUE_SETTLEMENT_PX",
            "yes_value": str(px), "state": state}


#: bounded retry policy for a settlement that has not appeared yet
RECONCILE_MAX_ATTEMPTS = 8
#: markets a single pass may touch, so one pass is bounded even on the worker
RECONCILE_MAX_PER_PASS = 4
RECONCILE_MAX_AGE_S = 7200.0        # two hours after the window ends


def _pending_markets(log: "ShadowPolicyLog") -> list:
    """Slugs whose residual is unresolved and still inside the retry budget."""
    out = []
    for mk, rec in log.latest.items():
        if mk in log.settled:
            continue
        if not rec.get("ok"):
            continue
        if settlement_basis(rec) != BASIS_PENDING:
            continue
        out.append(mk)
    return sorted(out)


def _reconcile_pending(log: "ShadowPolicyLog", get_json, *,
                       now: Optional[float] = None) -> list:
    """Try to resolve pending residuals. Bounded, idempotent, fail-closed.

    FAILS OPEN FOR CAPTURE. Every call to `get_json` is wrapped: a reader
    that is down, slow or returning nonsense costs this pass and nothing
    else. The recorder keeps capturing either way, which is the property
    worth more than any reconciliation.

    FAILS CLOSED FOR ACCOUNTING. Only `settlement_from_bbo` evidence is
    accepted, and only through the existing `reconcile_settlement` bars. A
    window the venue has not settled stays PENDING; one still unsettled
    after RECONCILE_MAX_ATTEMPTS tries or RECONCILE_MAX_AGE_S past its close
    is CLOSED as unresolved -- recorded as such, never priced.

    NEVER OVERWRITES. A result is APPENDED as a new line carrying
    `supersedes`, so the original scoring record stays on disk verbatim.
    """
    import time as _t
    now = _t.time() if now is None else float(now)
    out = []
    for mk in _pending_markets(log)[:RECONCILE_MAX_PER_PASS]:
        rec = log.latest.get(mk) or {}
        start = window_start_epoch(mk)
        closed = None if start is None else start + float(TS.WINDOW_S)
        n = log.attempts.get(mk, 0)
        expired = (closed is not None
                   and now - closed > RECONCILE_MAX_AGE_S)
        if n >= RECONCILE_MAX_ATTEMPTS or expired:
            closed_rec = dict(rec, reconciled=False, reconcile_closed=True,
                              settlement_basis=BASIS_UNRESOLVED_EXPIRED,
                              reconcile_reason=("RETRY_BUDGET_EXHAUSTED"
                                                if not expired
                                                else "SETTLEMENT_WINDOW_LAPSED"),
                              reconcile_attempts=n,
                              supersedes=rec.get("record_id") or mk,
                              labels=("UNRESOLVED_EXPIRED. The residual was "
                                      "never settled by the venue within the "
                                      "retry budget. The net stays null, no "
                                      "settlement evidence is attached and "
                                      "nothing is attributed to the venue."))
            closed_rec.pop("settlement_evidence", None)
            log.settled.add(mk)
            log.latest[mk] = closed_rec
            log._append(closed_rec)
            out.append(closed_rec)
            continue
        log.attempts[mk] = n + 1
        try:
            doc = get_json("/v1/markets/%s/bbo" % mk)
        except Exception:
            log.reconcile_errors += 1
            continue                 # the reader is the thing that failed
        ev = settlement_from_bbo(doc, mk)
        if not ev.get("ok"):
            continue                 # not settled yet: stays PENDING
        new = reconcile_settlement(rec, ev)
        if not new.get("reconciled"):
            log.reconcile_errors += 1
            continue
        new = dict(new, reconcile_attempts=log.attempts[mk],
                   supersedes=rec.get("record_id") or mk)
        log.settled.add(mk)
        log.latest[mk] = new
        log._append(new)
        out.append(new)
    return out


#: fields the bounded read-only view exposes per record. Nothing outside this
#: list is served, so a field added to a record later cannot leak by default.
#: a short, explicit timeout for the settlement read. The collector default
#: is 15s; four of those back to back is a minute, which is far too long to
#: hold anything up, so the caller always passes this instead.
SETTLEMENT_HTTP_TIMEOUT_S = 3.0

VIEW_FIELDS = ("market", "ok", "reason", "outcome", "one_sided_s",
               "settlement_basis",
               "selected", "decisions_evaluated", "quote",
               "inventory_after_acquisition", "resting", "net",
               "refusal_census", "first_action", "settlement_evidence",
               "reconciled", "reconcile_reason", "reconcile_attempts",
               "reconcile_closed", "books_seen", "prints_seen",
               "out_of_order_dropped", "shadow_only", "submitted",
               "fills_are_modelled")

VIEW_MAX_RECORDS = 200


def records_view(log: "ShadowPolicyLog", *, limit: int = 50) -> Dict[str, Any]:
    """A BOUNDED projection of the store: newest records, allow-listed fields.

    Bounded three ways -- a field allow-list, a record cap, and newest-first
    ordering -- so this cannot become an unbounded dump of a growing file,
    and a field added to the record later is not served until it is named
    here.
    """
    n = max(1, min(int(limit or 1), VIEW_MAX_RECORDS))
    items = sorted(log.latest.values(),
                   key=lambda r: str(r.get("market")), reverse=True)[:n]
    rows = [dict({k: r[k] for k in VIEW_FIELDS if k in r},
                 settlement_basis=settlement_basis(r)) for r in items]
    return {"records": rows, "returned": len(rows),
            "stored_markets": len(log.latest),
            "pending": len(_pending_markets(log)),
            "store_errors": log.errors,
            "reconcile_errors": log.reconcile_errors,
            "dashboard": dashboard(list(log.latest.values())),
            "labels": ("MODELLED, SHADOW ONLY. No order was submitted, no "
                       "fill occurred and no figure here is realized "
                       "profit.")}


def generations_pnl(rec: Mapping[str, Any],
                    yes_value: Any = None) -> Dict[str, Any]:
    """Settlement-adjusted P&L across EVERY generation of a window.

    WHY THIS EXISTS. `rec["net"]` models ONE generation -- it was written
    before replenishment and prices a single quote pair. Reading it for a
    multi-generation run silently reports the last generation as if it were
    the window. This accounts for all of them, from `process.gens_detail`.

    A matched generation pays a dollar a pair on either outcome, so it needs
    no settlement. An unmatched leg cannot be priced without one, and
    `priced` is False until the venue supplies `yes_value`.
    """
    proc = (rec or {}).get("process") or {}
    gens = proc.get("gens_detail") or []
    yv = _d(yes_value)
    net = D(0); unmatched = D(0); unmatched_cost = D(0)
    matched = D(0); fees = D(0)
    for g in gens:
        q = _d(g.get("qty")) or D(0)
        u = _d(g.get("up_px")); d = _d(g.get("dn_px"))
        if u is None or d is None or q <= 0:
            continue
        if g.get("up_filled") and g.get("dn_filled"):
            f = fee(TS.MAKER_THETA, q, u) + fee(TS.MAKER_THETA, q, d)
            net += q * (D(1) - u - d) - f
            matched += q; fees += f
        elif g.get("up_filled") or g.get("dn_filled"):
            px = u if g.get("up_filled") else d
            unmatched += q; unmatched_cost += q * px
            f = fee(TS.MAKER_THETA, q, px)
            fees += f; net -= f
    priced = True
    if unmatched > 0:
        if yv is None:
            priced = False
        else:
            for g in gens:
                if g.get("up_filled") and g.get("dn_filled"):
                    continue
                q = _d(g.get("qty")) or D(0)
                if g.get("up_filled"):
                    net += q * (yv - (_d(g.get("up_px")) or D(0)))
                elif g.get("dn_filled"):
                    net += q * ((D(1) - yv) - (_d(g.get("dn_px")) or D(0)))
    return {"generations": len(gens), "matched_qty": str(matched),
            "unmatched_qty": str(unmatched),
            "worst_case_residual_loss_usd": str(unmatched_cost),
            "fees_usd": str(fees),
            "net_usd": (str(net) if priced else None),
            "priced": priced,
            "labels": ("accounts for EVERY generation. rec['net'] models one "
                       "generation and is invalid for a replenished run.")}


def fee(theta: Any, qty: Any, price: Any) -> D:
    """Thin re-export so this module never grows a second fee table."""
    return TS.fee(theta, _d(qty) or D(0), _d(price) or D(0))


# ------------------------- OFFLINE ONLY: per-side independent replenishment

class IndependentReplenishSim:
    """Re-quote EACH side the moment it fills, instead of waiting for both.

    OFFLINE EXPERIMENT. Nothing in the deployed path constructs this. It
    reuses `build_orders` for eligibility and prices, the same fee model and
    the same conservative queue rule as `WindowAccumulator`.

    VOLUME IS NEVER REUSED. A resting order counts only prints with
    timestamp STRICTLY AFTER it joined, and the print that fills it is
    consumed: the replacement order joins at that instant and starts from
    zero, so the same historical volume cannot fill two orders on one side.
    The two sides read opposite sides of the book, so they cannot consume
    each other's prints either.

    CAPITAL NEVER COMES BACK. Charged at placement on the engine's own basis
    and never released by a completed pair, because nothing settles until the
    window ends.
    """

    def __init__(self, slug: str, *, tick: Any, min_qty: Any,
                 depth_ticks: int, cash_envelope: Any,
                 max_worst_case_loss: Any, clip: Any,
                 capital_ceiling: Any,
                 max_unmatched_qty: Any,
                 max_residual_loss_usd: Any,
                 book_age_s: Any = D("0.5")):
        self.slug = str(slug)
        self.lim = dict(tick=tick, min_qty=min_qty, depth_ticks=depth_ticks,
                        cash_envelope=cash_envelope,
                        max_worst_case_loss=max_worst_case_loss)
        self.book_age_s = book_age_s
        self.clip = _d(clip)
        self.cap = _d(capital_ceiling)
        self.max_unmatched = _d(max_unmatched_qty)
        self.max_resid_usd = _d(max_residual_loss_usd)
        self.t_start = window_start_epoch(slug)
        self.committed = D(0)
        self.up_qty = D(0); self.dn_qty = D(0)
        self.up_cost = D(0); self.dn_cost = D(0)
        self.fills: list = []          # (t, side, px, qty)
        self.resting: Dict[str, Any] = {"UP": None, "DOWN": None}
        self.refusals: Dict[str, int] = {}
        self.last_t = None
        self.peak_unmatched = D(0)
        self.peak_committed = D(0)

    # ---- helpers

    def _no(self, why: str) -> None:
        self.refusals[why] = self.refusals.get(why, 0) + 1

    def _unmatched(self) -> D:
        return abs(self.up_qty - self.dn_qty)

    def _residual_cost(self) -> D:
        """Worst-case settlement loss: the unmatched leg's own cost."""
        if self.up_qty > self.dn_qty and self.up_qty > 0:
            return self.up_cost * (self.up_qty - self.dn_qty) / self.up_qty
        if self.dn_qty > self.up_qty and self.dn_qty > 0:
            return self.dn_cost * (self.dn_qty - self.up_qty) / self.dn_qty
        return D(0)

    def _place(self, side: str, snap: Mapping[str, Any]) -> None:
        t = float(snap["t"])
        if t >= TS.cancel_cutoff_from_start(float(self.t_start)):
            return self._no("PAST_CUTOFF")
        b = _d(snap.get("bid")); a = _d(snap.get("ask"))
        plan = TS.build_orders(
            market=self.slug, bid=snap.get("bid"), ask=snap.get("ask"),
            bid_depth=(rung_depth(snap, "bids", b) if b is not None else None),
            ask_depth=(rung_depth(snap, "asks", a) if a is not None else None),
            book_age_s=self.book_age_s, **self.lim)
        if not plan.get("ok"):
            return self._no(str(plan.get("reason")))
        legs = {str(l.get("side")): l for l in (plan.get("legs") or [])}
        engine_qty = _d(plan.get("qty_per_leg")) or D(0)
        qty = self.clip if self.clip is not None else engine_qty
        if qty > engine_qty:
            return self._no("CLIP_EXCEEDS_ENGINE_SIZE")
        px = _d((legs.get("UP") if side == "UP" else legs.get("DOWN"))
                .get("price"))
        if px is None:
            return self._no("PRICE_UNREADABLE")
        # PER-LEG obligation. build_orders' `cash_gate_obligation_usd` is
        # qty*(1+up_px) for the two-legged PLAN: up_px a share for the UP buy
        # and a dollar a share for the DOWN buy, which is a BUY_SHORT and
        # owes a dollar if YES settles 1. Charging each side the whole plan
        # obligation double-counts and starves the sim of capital -- which is
        # exactly what it did on the first run of this experiment.
        oblig = qty * (px if side == "UP" else D(1))
        if self.cap is not None and self.committed + oblig > self.cap:
            return self._no("CAPITAL_CEILING")
        # inventory and worst-case bounds, evaluated on the state this fill
        # WOULD create -- the bound must hold before the order rests.
        would = abs((self.up_qty + qty) - self.dn_qty) if side == "UP" \
            else abs(self.up_qty - (self.dn_qty + qty))
        if self.max_unmatched is not None and would > self.max_unmatched:
            return self._no("UNMATCHED_INVENTORY_CAP")
        # WORST-CASE COST, NOT WORST-CASE QUANTITY AT TODAY'S PRICE. The
        # first version multiplied the whole resulting imbalance by the NEW
        # clip's price, so a second clip bought cheaper understated a
        # position already paid for dearer: 5@0.39 + 5@0.25 scored as
        # 10x0.25 = $2.50 and passed a $2.50 cap while actually costing
        # $3.20. The bound is the cost ALREADY sunk in the unmatched leg
        # plus what this clip would add to it.
        grows = ((side == "UP" and self.up_qty >= self.dn_qty)
                 or (side == "DOWN" and self.dn_qty >= self.up_qty))
        projected = self._residual_cost() + (qty * px if grows else D(0))
        if self.max_resid_usd is not None and projected > self.max_resid_usd:
            return self._no("WORST_CASE_RESIDUAL_CAP")
        rest_px = px if side == "UP" else (D(1) - px)   # where it sits on UP book
        qa = rung_depth(snap, "bids" if side == "UP" else "asks", rest_px)
        if qa is None:
            return self._no("QUEUE_AHEAD_UNREADABLE")
        self.committed += oblig
        self.peak_committed = max(self.peak_committed, self.committed)
        self.resting[side] = {"px": px, "rest_px": rest_px, "qty": qty,
                              "join_t": t, "queue": qa, "cum": D(0)}

    # ---- the event stream

    def on_book(self, snap: Mapping[str, Any]) -> None:
        t = snap.get("t")
        if t is None:
            return
        t = float(t)
        if self.last_t is not None and t < self.last_t:
            return
        self.last_t = t
        for side in ("UP", "DOWN"):
            if self.resting[side] is None:
                self._place(side, snap)

    def on_trade(self, t: Any, px: Any, qty: Any) -> None:
        tt = None if t is None else float(t)
        p = _d(px); q = _d(qty)
        if tt is None or p is None or q is None:
            return
        if self.last_t is not None and tt < self.last_t:
            return
        self.last_t = tt
        for side in ("UP", "DOWN"):
            o = self.resting[side]
            if o is None or tt <= o["join_t"]:
                continue        # STRICTLY after the join: no volume reuse
            hit = (p <= o["rest_px"]) if side == "UP" else (p >= o["rest_px"])
            if not hit:
                continue
            o["cum"] += q
            if o["cum"] > o["queue"]:
                self.fills.append((tt, side, str(o["px"]), str(o["qty"])))
                if side == "UP":
                    self.up_qty += o["qty"]; self.up_cost += o["qty"] * o["px"]
                else:
                    self.dn_qty += o["qty"]; self.dn_cost += o["qty"] * o["px"]
                self.peak_unmatched = max(self.peak_unmatched,
                                          self._unmatched())
                self.resting[side] = None      # free to re-quote at once

    # ---- result

    def result(self, yes_value: Any = None) -> Dict[str, Any]:
        matched = min(self.up_qty, self.dn_qty)
        yv = _d(yes_value)
        up_avg = (self.up_cost / self.up_qty) if self.up_qty else D(0)
        dn_avg = (self.dn_cost / self.dn_qty) if self.dn_qty else D(0)
        cash = self.up_cost + self.dn_cost
        fees = sum(fee(TS.MAKER_THETA, _d(q), _d(p))
                   for _t, _s, p, q in self.fills)
        res_qty = self._unmatched()
        res_cost = self._residual_cost()
        priced = res_qty == 0 or yv is not None
        net = None
        if priced:
            redeem = matched
            if res_qty > 0:
                redeem += res_qty * (yv if self.up_qty > self.dn_qty
                                     else (D(1) - yv))
            net = redeem - cash - fees
        return {"market": self.slug, "shadow_only": True, "submitted": False,
                "fills_are_modelled": True,
                "fills": len(self.fills),
                "up_qty": str(self.up_qty), "dn_qty": str(self.dn_qty),
                "up_avg_px": str(up_avg), "dn_avg_px": str(dn_avg),
                "matched_qty": str(matched),
                "turnover": (str(matched / self.peak_unmatched)
                             if self.peak_unmatched else None),
                "peak_unmatched_qty": str(self.peak_unmatched),
                "worst_case_residual_loss_usd": str(res_cost),
                "max_committed_capital_usd": str(self.peak_committed),
                "cash_out_usd": str(cash), "fees_usd": str(fees),
                "residual_qty": str(res_qty),
                "net_usd": (str(net) if priced else None), "priced": priced,
                "refusals": dict(self.refusals),
                "fill_sequence": [(round(t - self.t_start, 1), s, p, q)
                                  for t, s, p, q in self.fills],
                "labels": ("MODELLED, OFFLINE. No order was submitted. "
                           "Capital is charged at placement and never "
                           "released by a completed pair.")}
