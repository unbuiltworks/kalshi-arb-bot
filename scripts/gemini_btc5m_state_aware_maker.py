"""STATE_AWARE_MAKER extension. RESEARCH ONLY. No live maker authorization.

THE ONE QUESTION: does CAUSAL CANCELLATION BEFORE FILL reduce the adverse
selection that static inside-spread maker orders suffer? Nothing else. It is
not a better maker strategy, it is a test of one mechanism.

THREE POLICIES ARE KEPT SEPARATE, on purpose, so the attribution question
is answerable rather than assumed:

  TAKER            -- cross immediately. Direction only; no passive pricing.
  STATIC_MAKER     -- rest inside the spread and never cancel. Direction +
                      passive pricing. The FROZEN baseline, fingerprint
                      267ef1d15944feb49c5e25c897e61064e446f9d5a5e4ce7d7a33ac965f763a63.
  STATE_AWARE_MAKER-- rest inside the spread and cancel on a predeclared
                      causal condition. Direction + passive pricing +
                      stale-fill avoidance.

So STATE_AWARE minus STATIC isolates stale-fill avoidance, STATIC minus
TAKER isolates passive pricing, and TAKER itself is the directional
decision alone. An improvement that shows up in all three is directional and
has nothing to do with making.

NOT FITTED ON THE SEVEN PROOF-RUN FILLS. Every cancellation constant below
was written before this file was first executed, and the variants are
reported INDEPENDENTLY -- there is no sweep, no argmax, no best-variant
output and no combination chosen after seeing results. The one combined
variant (ALL) is predeclared as the conjunction of the others, not selected.

CAUSALITY IS STRUCTURAL, not a promise:
  * a cancel decision reads ONLY observations strictly AFTER placement and
    strictly BEFORE the hypothetical execution instant;
  * `assert_causal()` refuses any cancel feature whose name mentions an
    outcome, and the settlement is fetched only when the P&L is computed,
    never passed into a cancel rule;
  * NO TOUCH = FILL is inherited unchanged from the frozen baseline: a fill
    needs a qualifying later print, aggressor on the right side, with
    enough size behind it;
  * THE RACE IS RESOLVED AGAINST THE CANCEL. If the cancel condition and
    the qualifying fill fall on the same observation, the order is treated
    as FILLED -- the policy is denied the benefit of a tie, because a
    backtest that wins ties is measuring its own optimism.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from scripts import gemini_btc5m_maker_study as BASE

LABEL = "GEMINI_BTC5M_STATE_AWARE_MAKER_V1_PREREGISTERED"
STATIC_BASELINE_FINGERPRINT = (
    "267ef1d15944feb49c5e25c897e61064e446f9d5a5e4ce7d7a33ac965f763a63")

UNIT = BASE.UNIT
TICK = BASE.TICK

#: PREDECLARED CANCELLATION VARIANTS. Fixed constants, written before this
#: file was first run, reported independently. No sweep and no selection.
#:
#:  C1_PASSIVE_RECEDED -- the passive touch we were improving has moved
#:      against us by >= 1 tick. Our quote is now stale-rich: we are bidding
#:      above a market that has stepped down.
#:  C2_SPREAD_LEFT     -- our resting price is no longer strictly inside the
#:      spread. The book repriced past us.
#:  C3_QUEUE_GREW      -- the size displayed ahead of us has at least
#:      doubled since placement, so our realistic fill is now a worse one.
#:  C4_REFERENCE_MOVED -- the published settlement reference has moved
#:      adversely by >= 5 bps of the strike since placement. Causal: the
#:      last value published at or before the observation, never later.
#:  C5_AGE             -- unfilled after 60s. A time-in-force, not a signal.
#:  ALL                -- the conjunction of C1..C5: cancel on whichever
#:      fires first. Predeclared, not chosen.
VARIANTS: Tuple[str, ...] = ("C1_PASSIVE_RECEDED", "C2_SPREAD_LEFT",
                             "C3_QUEUE_GREW", "C4_REFERENCE_MOVED",
                             "C5_AGE", "ALL")
PASSIVE_RECEDE_TICKS = 1
QUEUE_GROWTH_MULTIPLE = D("2")
REFERENCE_ADVERSE_BPS = D("5")
MAX_RESTING_AGE_S = 60.0
#: a reference value older than this is stale and is NOT used
MAX_REF_AGE_S = 10.0

FUTURE_WORDS = ("won", "resolution", "settle", "eventual", "winner",
                "payout", "future", "outcome")


def assert_causal(feat: Dict[str, Any]) -> None:
    for k in feat:
        low = k.lower()
        for w in FUTURE_WORDS:
            if w in low:
                raise AssertionError(
                    "cancel feature %r names the future (%r)" % (k, w))


def self_fingerprint() -> str:
    return hashlib.sha256(open(__file__, "rb").read()).hexdigest()


def reference_series(rows: List[Dict[str, Any]]) -> List[Tuple[float, D]]:
    ser = []
    for r in rows:
        if r.get("kind") != "index_price":
            continue
        t = r.get("parsed_event_time")
        v = BASE._d(r.get("parsed_value")
                    or ((r.get("raw") or {}).get("p")))
        if t is not None and v is not None:
            ser.append((float(t), v))
    ser.sort()
    return ser


def reference_at(ser, t: float) -> Optional[D]:
    """The last value published AT OR BEFORE t, or None if stale/absent."""
    import bisect
    if not ser:
        return None
    i = bisect.bisect_right([x[0] for x in ser], t) - 1
    if i < 0 or (t - ser[i][0]) > MAX_REF_AGE_S:
        return None
    return ser[i][1]


def cancel_time(variant: str, later_cps: List[Dict[str, Any]], *,
                side: str, px: D, t0: float, bk0: Dict[str, Any],
                ahead0: D, strike: Optional[D], ser) -> Optional[Dict]:
    """The FIRST instant the predeclared condition fires, or None.

    Reads only observations strictly after placement. The settlement is not
    a parameter of this function, by construction.
    """
    ref0 = reference_at(ser, t0)
    for cp in later_cps:
        t = BASE._ns(cp)
        if t is None or t <= t0:
            continue
        bk = BASE.book(cp)
        feat: Dict[str, Any] = {"age_s": round(t - t0, 3)}
        fired = None
        if variant in ("C5_AGE", "ALL") and (t - t0) >= MAX_RESTING_AGE_S:
            fired = "C5_AGE"
        if bk is not None:
            # our passive side, and the size ahead of us, as of NOW
            passive_now = bk["bid"] if side == "UP" else bk["ask"]
            passive_0 = bk0["bid"] if side == "UP" else bk0["ask"]
            receded = ((passive_0 - passive_now) if side == "UP"
                       else (passive_now - passive_0))
            feat["passive_recede_ticks"] = float(receded / TICK)
            feat["inside_spread_now"] = bool(bk["bid"] < px < bk["ask"])
            ahead_now = BASE.ahead_size(bk, side, px)
            feat["queue_ahead_now"] = str(ahead_now)
            if fired is None and variant in ("C1_PASSIVE_RECEDED", "ALL") \
                    and receded >= PASSIVE_RECEDE_TICKS * TICK:
                fired = "C1_PASSIVE_RECEDED"
            if fired is None and variant in ("C2_SPREAD_LEFT", "ALL") \
                    and not feat["inside_spread_now"]:
                fired = "C2_SPREAD_LEFT"
            if fired is None and variant in ("C3_QUEUE_GREW", "ALL") \
                    and ahead0 > 0 \
                    and ahead_now >= ahead0 * QUEUE_GROWTH_MULTIPLE:
                fired = "C3_QUEUE_GREW"
        if fired is None and variant in ("C4_REFERENCE_MOVED", "ALL") \
                and strike is not None and ref0 is not None:
            ref_now = reference_at(ser, t)
            if ref_now is not None:
                d0 = (ref0 - strike) / strike * D("10000")
                dn = (ref_now - strike) / strike * D("10000")
                # adverse = the reference moving AGAINST the side we hold
                move = (dn - d0) if side == "DOWN" else (d0 - dn)
                feat["reference_adverse_bps"] = float(move)
                if move >= REFERENCE_ADVERSE_BPS:
                    fired = "C4_REFERENCE_MOVED"
        assert_causal(feat)
        if fired:
            return {"cancelled": True, "rule": fired, "t": t,
                    "age_s": round(t - t0, 3), "features": feat}
    return None


def run(rows: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    cps, trades, settle = BASE.states(rows)
    ser = reference_series(rows)
    strikes: Dict[str, Optional[D]] = {}
    for r in rows:
        if r.get("kind") == "window" and r.get("duration") == "BTC05M":
            k = BASE._d(r.get("strike"))
            if k is not None and r.get("ticker"):
                strikes[r["ticker"]] = k

    recs: List[Dict[str, Any]] = []
    for market, rws in sorted(cps.items()):
        rws = sorted(rws, key=lambda r: r.get("checkpoint_s") or 0)
        tk = rws[0].get("ticker")
        res = settle.get(tk)
        if res not in ("yes", "no"):
            continue                 # no outcome: not an unfilled order
        for i, cp in enumerate(rws):
            bk = BASE.book(cp)
            if bk is None:
                continue
            side = BASE.side_choice(bk, mode)
            if side is None:
                continue
            t0 = BASE._ns(cp)
            if t0 is None:
                continue
            later = rws[i + 1:]
            taker_px = bk["ask"] if side == "UP" else D("1") - bk["bid"]
            rec = {"market": market, "ticker": tk, "side": side,
                   "checkpoint_s": cp.get("checkpoint_s"),
                   "settlement_up_side": res,
                   "taker_price": taker_px,
                   "taker_fee": BASE.fee(BASE.TAKER_RATE, taker_px),
                   "placements": {}}
            for name, frac in BASE.PLACEMENTS:
                px = BASE.placement(bk, side, frac)
                if px is None:
                    continue
                pay = px if side == "UP" else D("1") - px
                ahead = BASE.ahead_size(bk, side, px)
                ev = BASE.fill_evidence(trades.get(market) or [], later,
                                        side, px, t0, ahead)
                entry: Dict[str, Any] = {
                    "resting_yes_price": px, "acquisition_price": pay,
                    "static_fill": bool(ev.get("filled")),
                    "fill_t": ev.get("fill_t"),
                    "maker_fee": BASE.fee(BASE.MAKER_RATE, pay),
                    "variants": {}}
                for v in VARIANTS:
                    c = cancel_time(v, later, side=side, px=px, t0=t0,
                                    bk0=bk, ahead0=ahead,
                                    strike=strikes.get(tk), ser=ser)
                    # THE RACE GOES AGAINST THE CANCEL: a tie is a fill.
                    if not ev.get("filled"):
                        state = ("CANCELLED_BEFORE_ANY_FILL" if c
                                 else "NO_FILL_NO_CANCEL")
                        filled = False
                    elif c is None or c["t"] > ev["fill_t"]:
                        state, filled = "FILLED", True
                    elif c["t"] == ev["fill_t"]:
                        state, filled = "FILLED_ON_A_TIE", True
                    else:
                        state, filled = "CANCELLED_BEFORE_FILL", False
                    entry["variants"][v] = {
                        "state": state, "filled": filled,
                        "cancel_rule": (c or {}).get("rule"),
                        "cancel_age_s": (c or {}).get("age_s")}
                rec["placements"][name] = entry
            recs.append(rec)
    return summarise(recs)


def _pnl(side: str, res: str, pay: D, f: D) -> D:
    won = (side == "UP" and res == "yes") or (side == "DOWN" and res == "no")
    return (D("1") if won else D("0")) * UNIT - pay * UNIT - f


def summarise(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(recs)
    out: Dict[str, Any] = {"opportunities_settled": n, "policies": {}}
    # TAKER: direction alone
    t_rows = [_pnl(r["side"], r["settlement_up_side"], r["taker_price"],
                   r["taker_fee"]) for r in recs]
    out["policies"]["TAKER"] = _m(t_rows, n, n, extra={
        "isolates": "the directional decision alone"})
    for name, _ in BASE.PLACEMENTS:
        placed = [r for r in recs if name in r["placements"]]
        # STATIC: direction + passive pricing
        s_rows, s_f = [], 0
        for r in placed:
            e = r["placements"][name]
            if e["static_fill"]:
                s_f += 1
                s_rows.append(_pnl(r["side"], r["settlement_up_side"],
                                   e["acquisition_price"], e["maker_fee"]))
        out["policies"]["STATIC_MAKER_%s" % name.upper()] = _m(
            s_rows, len(placed), s_f,
            extra={"isolates": "direction + passive pricing",
                   "baseline_fingerprint": STATIC_BASELINE_FINGERPRINT})
        # STATE_AWARE: direction + passive pricing + stale-fill avoidance
        for v in VARIANTS:
            rows_, f_, canc, ties = [], 0, 0, 0
            for r in placed:
                e = r["placements"][name]
                st = e["variants"][v]
                if st["state"] == "CANCELLED_BEFORE_FILL":
                    canc += 1
                if st["state"] == "FILLED_ON_A_TIE":
                    ties += 1
                if st["filled"]:
                    f_ += 1
                    rows_.append(_pnl(r["side"], r["settlement_up_side"],
                                      e["acquisition_price"],
                                      e["maker_fee"]))
            out["policies"]["STATE_AWARE_%s_%s" % (name.upper(), v)] = _m(
                rows_, len(placed), f_, extra={
                    "isolates": ("direction + passive pricing + stale-fill "
                                 "avoidance"),
                    "fills_avoided_by_cancelling": canc,
                    "fills_kept_on_a_tie": ties,
                    "tie_convention": ("a tie is a FILL -- the policy is "
                                       "denied the benefit of the doubt")})
    # the attribution the three-policy split exists to answer
    att = {}
    tk_ev = out["policies"]["TAKER"]["ev_per_opportunity_usd"]
    for name, _ in BASE.PLACEMENTS:
        st = out["policies"]["STATIC_MAKER_%s" % name.upper()]
        row = {"taker_ev_per_opportunity": tk_ev,
               "static_ev_per_opportunity": st["ev_per_opportunity_usd"],
               "passive_pricing_delta": _sub(st["ev_per_opportunity_usd"],
                                             tk_ev)}
        for v in VARIANTS:
            sa = out["policies"]["STATE_AWARE_%s_%s" % (name.upper(), v)]
            row[v] = {
                "ev_per_opportunity": sa["ev_per_opportunity_usd"],
                "stale_fill_avoidance_delta": _sub(
                    sa["ev_per_opportunity_usd"],
                    st["ev_per_opportunity_usd"]),
                "fills": sa["fills"],
                "fills_avoided_by_cancelling": sa[
                    "fills_avoided_by_cancelling"]}
        att[name] = row
    out["attribution"] = {
        "reading": ("passive_pricing_delta = STATIC - TAKER isolates resting "
                    "instead of crossing. stale_fill_avoidance_delta = "
                    "STATE_AWARE - STATIC isolates cancelling. A gain that "
                    "appears in TAKER too is directional and is not about "
                    "making at all."),
        "by_placement": att}
    return out


def _sub(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if a is None or b is None:
        return None
    return str(D(a) - D(b))


def _m(rows: List[D], n_opps: int, n_fills: int,
       extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    tot = sum(rows, D("0"))
    m = {
        "opportunities": n_opps,
        "fills": n_fills,
        "fill_rate_pct": (round(100.0 * n_fills / n_opps, 2)
                          if n_opps else None),
        "net_pnl_usd": str(tot.quantize(D("0.0001"))),
        "ev_per_fill_usd": (str((tot / len(rows)).quantize(D("0.0001")))
                            if rows else None),
        "ev_per_opportunity_usd": (str((tot / n_opps).quantize(D("0.0001")))
                                   if n_opps else None),
        "no_fill_opportunities": n_opps - n_fills,
        "rebates_usd": "0 -- NONE assumed, modelled or credited",
    }
    if extra:
        m.update(extra)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_snap/btc5m_rec_*.jsonl.gz")
    ap.add_argument("--side", default="underdog",
                    choices=("underdog", "up", "down"))
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "STATE_AWARE_MAKER.json"))
    a = ap.parse_args(argv)
    rows = BASE.tape(a.tapes)
    # THE BASELINE MUST BE THE FROZEN ONE, asserted rather than assumed
    live_fp = BASE.self_fingerprint()
    rep = {
        "label": LABEL,
        "fingerprint_sha256": self_fingerprint(),
        "static_baseline_fingerprint_expected": STATIC_BASELINE_FINGERPRINT,
        "static_baseline_fingerprint_live": live_fp,
        "static_baseline_unchanged": live_fp == STATIC_BASELINE_FINGERPRINT,
        "status": ("RESEARCH ONLY / PREREGISTERED. No live maker "
                   "authorization."),
        "purpose": ("does causal cancellation before fill reduce the adverse "
                    "selection static inside-spread maker orders suffer"),
        "not_fitted": ("every cancellation constant was written before this "
                       "file was first executed; variants are reported "
                       "independently with no sweep, no argmax and no "
                       "best-variant output. ALL is the predeclared "
                       "conjunction, not a selection."),
        "variants": {
            "C1_PASSIVE_RECEDED": "passive touch moved >= %d tick against us"
                                  % PASSIVE_RECEDE_TICKS,
            "C2_SPREAD_LEFT": "our price is no longer strictly inside",
            "C3_QUEUE_GREW": "size ahead of us >= %sx placement"
                             % QUEUE_GROWTH_MULTIPLE,
            "C4_REFERENCE_MOVED": "published reference adverse by >= %s bps"
                                  % REFERENCE_ADVERSE_BPS,
            "C5_AGE": "unfilled after %ss" % MAX_RESTING_AGE_S,
            "ALL": "whichever of C1..C5 fires first",
        },
        "causality": ("cancel decisions read only observations strictly "
                      "after placement and strictly before the hypothetical "
                      "execution; assert_causal refuses a feature naming an "
                      "outcome; the settlement is not a parameter of the "
                      "cancel function"),
        "tie_convention": ("a cancel and a fill on the same observation is "
                           "treated as a FILL -- a backtest that wins ties "
                           "is measuring its own optimism"),
        "no_touch_equals_fill": "inherited unchanged from the frozen baseline",
        "tapes_glob": a.tapes, "tape_rows": len(rows),
        "side_selection_mode": a.side,
    }
    if not rep["static_baseline_unchanged"]:
        rep["refused"] = ("the STATIC_MAKER baseline has changed; the "
                          "comparison is void until it is restored")
        print(json.dumps({"label": LABEL, "refused": rep["refused"]}))
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True, default=str)
        return 1
    rep["result"] = run(rows, a.side)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({"label": LABEL,
                      "fingerprint": rep["fingerprint_sha256"],
                      "baseline_unchanged": True,
                      "opportunities": rep["result"]["opportunities_settled"],
                      "attribution": rep["result"]["attribution"][
                          "by_placement"].get("p50")},
                     sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
