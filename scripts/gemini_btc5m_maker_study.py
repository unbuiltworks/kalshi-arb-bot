"""BTC5m INSIDE-SPREAD MAKER study. RESEARCH ONLY. No live maker orders.

THE QUESTION, and the reason side selection is kept separate from execution
method: GIVEN THE SAME DIRECTIONAL DECISION, does resting inside Gemini's
spread materially improve expectancy versus crossing it? So `--side` picks
the direction (underdog / up / down) and is held fixed while only the
EXECUTION METHOD varies: immediate taker at the executable price, against
predetermined resting placements at 25%, 50% and 75% through the observed
spread from the passive side.

NO TOUCH = FILL. A resting order is NEVER assumed filled because the price
was reached. A fill requires defensible SUBSEQUENT execution evidence from
the tape, in two explicitly separated tiers:

  TRADE_THROUGH  -- a later trade prints at or through our resting price,
                    AND the cumulative traded quantity at or through it
                    exceeds the size that would have been AHEAD of us plus
                    our own. This is the only tier counted as a fill in the
                    headline numbers.
  BOOK_EVOLUTION -- no qualifying print, but the book's own passive side
                    later moves strictly through our price, which implies
                    the level was consumed. Weaker evidence; reported
                    SEPARATELY and never pooled into the headline.

CONSERVATIVE QUEUE. We are assumed to arrive BEHIND every contract already
displayed at a better-or-equal price on our side, and our own size must also
trade before we are filled. No queue position is invented, no partial fill
is credited, and a trade whose side cannot be determined is not evidence.

FEES AND REBATES ARE SEPARATE. The venue's published maker rate is
0.0175*q*p*(1-p) and the taker rate 0.07*q*p*(1-p). Both are FEES. NO
REBATE OR LIQUIDITY REWARD IS ASSUMED, MODELLED OR CREDITED anywhere in
this file: the repository's standing rule is that a rebate counts only once
it is credited and source-accounted, and none has been.

It changes nothing: not the live UNDERDOG cohort, not the recorder, not
either frozen analyzer (f2800e3e...960e90b, b2f6d50a...142f9bc6), not any
authorization, and it opens no venue surface.
"""
from __future__ import annotations

import argparse
import collections
import glob
import gzip
import json
import os
import statistics
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_UP, Decimal as D
from typing import Any, Dict, List, Optional, Tuple

LABEL = "GEMINI_BTC5M_INSIDE_SPREAD_MAKER_STUDY_V1"
CHECKPOINTS = (15, 30, 45, 60, 90, 120, 150, 180)
#: structural placements, as a fraction of the spread from the PASSIVE side
PLACEMENTS: Tuple[Tuple[str, D], ...] = (("p25", D("0.25")),
                                         ("p50", D("0.50")),
                                         ("p75", D("0.75")))
UNIT = D("1")
TAKER_RATE = D("0.07")
MAKER_RATE = D("0.0175")
FEE_Q = D("0.01")
TICK = D("0.01")
#: a level at or above this price with a size of one contract is a no-offer
NO_OFFER_PRICE = D("0.98")


def _d(v: Any) -> Optional[D]:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def fee(rate: D, price: D, qty: D = UNIT) -> D:
    raw = rate * qty * price * (D("1") - price)
    if raw <= 0:
        return D("0")
    return raw.quantize(FEE_Q, rounding=ROUND_CEILING)


def tape(pattern: str) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(glob.glob(pattern)):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except Exception:                              # noqa: BLE001
                    continue
    return out


def _ns(r: Dict[str, Any]) -> Optional[float]:
    for k in ("E_ns", "t_ns"):
        v = r.get(k)
        if v is not None:
            return float(v) / 1e9
    return None


# --------------------------------------------------------------- the states
def states(rows: List[Dict[str, Any]]
           ) -> Tuple[Dict[str, List[Dict[str, Any]]],
                      Dict[str, List[Dict[str, Any]]],
                      Dict[str, str]]:
    cps: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    trades: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    settle: Dict[str, str] = {}
    for r in rows:
        k = r.get("kind")
        if k == "checkpoint" and r.get("duration") == "BTC05M":
            if r.get("checkpoint_family") == "EARLY_FROM_T0" \
                    and r.get("checkpoint_s") in CHECKPOINTS:
                cps[r["market"]].append(r)
        elif k == "p_trade":
            trades[r.get("market", "")].append(r)
        elif k == "settlement":
            ev = r.get("raw_event") or {}
            for c in (ev.get("contracts") or []):
                if str(c.get("label", "")).lower() == "up":
                    side = c.get("resolutionSide")
                    if side in ("yes", "no") and r.get("ticker"):
                        settle[r["ticker"]] = side
    for m in trades:
        trades[m].sort(key=lambda r: _ns(r) or 0.0)
    return cps, trades, settle


def book(cp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Touch and depth of the ONE published ladder. YES terms throughout."""
    lad = cp.get("yes_ladder") or {}
    bids, asks = lad.get("bids") or [], lad.get("asks") or []
    if not bids or not asks:
        return None
    b_px, b_sz = _d(bids[0][0]), _d(bids[0][1])
    a_px, a_sz = _d(asks[0][0]), _d(asks[0][1])
    if None in (b_px, b_sz, a_px, a_sz) or b_px >= a_px:
        return None
    if a_px >= NO_OFFER_PRICE and a_sz <= UNIT:
        return None
    return {"bid": b_px, "ask": a_px, "bid_size": b_sz, "ask_size": a_sz,
            "spread": a_px - b_px, "bids": bids, "asks": asks}


def side_choice(bk: Dict[str, Any], mode: str) -> Optional[str]:
    """DIRECTION ONLY. Held fixed while execution method varies."""
    if mode == "up":
        return "UP"
    if mode == "down":
        return "DOWN"
    up_cost, dn_cost = bk["ask"], D("1") - bk["bid"]
    if up_cost < dn_cost:
        return "UP"
    if dn_cost < up_cost:
        return "DOWN"
    return None                                  # a tie has no underdog


def placement(bk: Dict[str, Any], side: str, frac: D) -> Optional[D]:
    """A resting price `frac` through the spread from OUR passive side.

    Buying UP rests on the BID side and improves upward; buying DOWN is a
    YES SELL, which rests on the ASK side and improves downward. Each is
    snapped AWAY from aggression -- the buy floors, the sell ceilings -- so
    the placement is never better than the fraction implies.
    """
    if side == "UP":
        px = bk["bid"] + frac * bk["spread"]
        px = px.quantize(TICK, rounding=ROUND_DOWN)
        return px if bk["bid"] < px < bk["ask"] else None
    px = bk["ask"] - frac * bk["spread"]
    px = px.quantize(TICK, rounding=ROUND_UP)
    return px if bk["bid"] < px < bk["ask"] else None


def ahead_size(bk: Dict[str, Any], side: str, px: D) -> D:
    """Contracts that would be AHEAD of us in queue at or better than px."""
    tot = D("0")
    rows = bk["bids"] if side == "UP" else bk["asks"]
    for lv in rows:
        p, q = _d(lv[0]), _d(lv[1])
        if p is None or q is None:
            continue
        if (side == "UP" and p >= px) or (side == "DOWN" and p <= px):
            tot += q
    return tot


def fill_evidence(trades: List[Dict[str, Any]], later_cps: List[Dict],
                  side: str, px: D, t0: float, ahead: D,
                  qty: D = UNIT) -> Dict[str, Any]:
    """Did a resting order at `px` demonstrably fill? NO TOUCH = FILL."""
    need = ahead + qty
    cum = D("0")
    for tr in trades:
        t = _ns(tr)
        if t is None or t <= t0:
            continue
        p, q = _d(tr.get("price")), _d(tr.get("qty"))
        if p is None or q is None:
            continue
        # buying UP rests on the bid: a SELL printing at or below our price
        # could have hit us. Buying DOWN is a YES sell resting on the ask: a
        # BUY printing at or above our price could have lifted us.
        through = (p <= px) if side == "UP" else (p >= px)
        if not through:
            continue
        # THE AGGRESSOR MUST BE ON THE RIGHT SIDE. `buyer_is_maker` tells us
        # which party was passive: True means the BUYER rested and a SELLER
        # crossed, so that print could hit a resting BID and not a resting
        # ask. Ignoring this field counted every print through our price as
        # evidence in BOTH directions, which is how a maker study credits
        # itself with fills it could not have had. A print whose side cannot
        # be determined is NOT evidence.
        bim = tr.get("buyer_is_maker")
        if bim is None:
            continue
        aggressor_sold = bool(bim)
        if side == "UP" and not aggressor_sold:
            continue
        if side == "DOWN" and aggressor_sold:
            continue
        cum += q
        if cum >= need:
            return {"filled": True, "tier": "TRADE_THROUGH",
                    "fill_t": t, "time_to_fill_s": round(t - t0, 3),
                    "traded_through_qty": str(cum),
                    "queue_ahead_qty": str(ahead)}
    # weaker tier, reported separately and never pooled
    for cp in later_cps:
        bk = book(cp)
        if bk is None:
            continue
        moved = (bk["bid"] > px) if side == "UP" else (bk["ask"] < px)
        if moved:
            t = _ns(cp)
            return {"filled": False, "tier": "BOOK_EVOLUTION",
                    "book_moved_through": True,
                    "at_checkpoint_s": cp.get("checkpoint_s"),
                    "time_to_signal_s": (None if t is None
                                         else round(t - t0, 3)),
                    "why": ("the passive side later moved through our price, "
                            "which implies the level was consumed -- but no "
                            "qualifying print was observed, so this is NOT "
                            "counted as a fill")}
    return {"filled": False, "tier": "NO_EVIDENCE",
            "traded_through_qty": str(cum), "queue_ahead_qty": str(ahead),
            "why": "no print reached our price with enough size behind it"}


def adverse_after(later_cps: List[Dict[str, Any]], side: str,
                  ref: D) -> Optional[float]:
    """Worst executable move against us AFTER the fill, in price points."""
    worst = None
    for cp in later_cps:
        bk = book(cp)
        if bk is None:
            continue
        # what we could liquidate for: UP at the bid, DOWN at 1 - the ask
        liq = bk["bid"] if side == "UP" else D("1") - bk["ask"]
        mv = float(liq - ref)
        worst = mv if worst is None else min(worst, mv)
    return None if worst is None else round(worst, 4)


# ------------------------------------------------------------------- study
def run(cps, trades, settle, mode: str) -> Dict[str, Any]:
    opps: List[Dict[str, Any]] = []
    for market, rows in sorted(cps.items()):
        rows = sorted(rows, key=lambda r: r.get("checkpoint_s") or 0)
        tk = rows[0].get("ticker")
        res = settle.get(tk)
        for i, cp in enumerate(rows):
            bk = book(cp)
            if bk is None:
                continue
            side = side_choice(bk, mode)
            if side is None:
                continue
            t0 = _ns(cp)
            if t0 is None:
                continue
            later = rows[i + 1:]
            taker_px = bk["ask"] if side == "UP" else D("1") - bk["bid"]
            o = {"market": market, "ticker": tk,
                 "checkpoint_s": cp.get("checkpoint_s"),
                 "side": side, "bid": str(bk["bid"]), "ask": str(bk["ask"]),
                 "spread": str(bk["spread"]),
                 "settlement_up_side": res,
                 "taker": {"price": str(taker_px),
                           "fee": str(fee(TAKER_RATE, taker_px))},
                 "maker": {}}
            for name, frac in PLACEMENTS:
                px = placement(bk, side, frac)
                if px is None:
                    o["maker"][name] = {"placed": False,
                                        "why": ("the spread is too narrow "
                                                "for a distinct inside tick")}
                    continue
                # the PRICE WE PAY. For DOWN, the resting YES sell at px
                # acquires NO at 1 - px.
                pay = px if side == "UP" else D("1") - px
                ahead = ahead_size(bk, side, px)
                ev = fill_evidence(trades.get(market) or [], later, side,
                                   px, t0, ahead)
                rec = {"placed": True, "resting_yes_price": str(px),
                       "acquisition_price": str(pay),
                       "queue_ahead_qty": ev.get("queue_ahead_qty"),
                       "maker_fee_if_filled": str(fee(MAKER_RATE, pay)),
                       "rebate_assumed": False, **ev}
                if ev.get("filled"):
                    rec["adverse_after_fill"] = adverse_after(
                        later, side, pay)
                o["maker"][name] = rec
            opps.append(o)
    return summarise(opps, mode)


def summarise(opps: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    def settled_pnl(side: str, res: Optional[str], pay: D, f: D
                    ) -> Optional[D]:
        if res not in ("yes", "no"):
            return None
        won = (side == "UP" and res == "yes") or (side == "DOWN"
                                                  and res == "no")
        return (D("1") if won else D("0")) * UNIT - pay * UNIT - f

    # AN UNSETTLED OPPORTUNITY IS NOT AN UNFILLED ONE. The first version
    # divided by every opportunity and counted windows with no settlement as
    # no-fills, which showed TAKER -- whose fill is immediate by
    # construction -- with an 82% "fill rate". Those windows are excluded
    # from BOTH denominators and reported separately instead.
    settled_opps = [o for o in opps
                    if o.get("settlement_up_side") in ("yes", "no")]
    n_set = len(settled_opps)
    out: Dict[str, Any] = {
        "side_selection_mode": mode,
        "opportunities": len(opps),
        "settled_opportunities_used_as_the_denominator": n_set,
        "unsettled_opportunities_excluded": len(opps) - n_set,
        "denominator_note": ("every rate and per-opportunity EV divides by "
                             "the SETTLED opportunities; an unsettled window "
                             "is missing an outcome, not an unfilled order"),
        "by_method": {}}
    opps = settled_opps
    # TAKER, the comparison baseline
    rows = []
    for o in opps:
        pay = D(o["taker"]["price"])
        f = D(o["taker"]["fee"])
        p = settled_pnl(o["side"], o["settlement_up_side"], pay, f)
        if p is not None:
            rows.append({"pnl": p, "pay": pay, "fee": f,
                         "won": p > 0, "cp": o["checkpoint_s"]})
    tk = _metrics(rows, n_set, 0, None)
    tk["fill_rate_pct"] = 100.0
    tk["fill_rate_note"] = ("a taker fill is immediate by construction; the "
                            "rate is 100% and is not a measured quantity")
    out["by_method"]["TAKER_IMMEDIATE"] = tk
    for name, _ in PLACEMENTS:
        filled, n_placed, n_book = [], 0, 0
        ttf, adv = [], []
        for o in opps:
            m = o["maker"].get(name) or {}
            if not m.get("placed"):
                continue
            n_placed += 1
            if m.get("tier") == "BOOK_EVOLUTION":
                n_book += 1
            if not m.get("filled"):
                continue
            pay = D(m["acquisition_price"])
            f = D(m["maker_fee_if_filled"])
            p = settled_pnl(o["side"], o["settlement_up_side"], pay, f)
            if p is None:
                continue
            filled.append({"pnl": p, "pay": pay, "fee": f, "won": p > 0,
                           "cp": o["checkpoint_s"]})
            if m.get("time_to_fill_s") is not None:
                ttf.append(m["time_to_fill_s"])
            if m.get("adverse_after_fill") is not None:
                adv.append(m["adverse_after_fill"])
        out["by_method"]["MAKER_%s" % name.upper()] = _metrics(
            filled, n_placed, n_book, {"time_to_fill_s": ttf,
                                       "adverse_after_fill": adv})
    t = out["by_method"]["TAKER_IMMEDIATE"]
    out["headline"] = {
        "question": ("given the SAME directional decision, does resting "
                     "inside the spread beat crossing it?"),
        "taker_ev_per_all_opportunities": t["ev_per_all_opportunities_usd"],
        "comparison": {k: {
            "ev_per_all_opportunities_usd": v["ev_per_all_opportunities_usd"],
            "minus_taker_usd": (
                str(D(v["ev_per_all_opportunities_usd"])
                    - D(t["ev_per_all_opportunities_usd"]))
                if v["ev_per_all_opportunities_usd"] not in (None, "0")
                or t["ev_per_all_opportunities_usd"] not in (None,) else None),
            "fill_rate_pct": v["fill_rate_pct"],
            "fills": v["fills"]}
            for k, v in out["by_method"].items() if k != "TAKER_IMMEDIATE"},
    }
    return out


def _metrics(rows: List[Dict[str, Any]], n_opps: int, n_book: int,
             paths: Optional[Dict[str, List[float]]]) -> Dict[str, Any]:
    n = len(rows)
    tot = sum((r["pnl"] for r in rows), D("0"))
    wins = sum(1 for r in rows if r["won"])
    fees = sum((r["fee"] for r in rows), D("0"))
    cap = sum((r["pay"] * UNIT + r["fee"] for r in rows), D("0"))
    m = {
        "opportunities": n_opps,
        "fills": n,
        "fill_rate_pct": (round(100.0 * n / n_opps, 2) if n_opps else None),
        "book_evolution_only_not_counted_as_fills": n_book,
        "settlement_win_rate_pct": (round(100.0 * wins / n, 2)
                                    if n else None),
        "net_pnl_usd": str(tot.quantize(D("0.0001"))),
        "ev_per_fill_usd": (str((tot / n).quantize(D("0.0001")))
                            if n else None),
        "ev_per_all_opportunities_usd": (
            str((tot / n_opps).quantize(D("0.0001"))) if n_opps else None),
        "no_fill_opportunities": n_opps - n,
        "fees_usd_separate": str(fees),
        "rebates_usd": "0 -- NONE assumed, modelled or credited",
        "capacity_capital_deployed_usd": str(cap),
        "capacity_note": ("q=1 per opportunity; capacity is bounded by the "
                          "queue ahead of us, which is reported per "
                          "placement rather than averaged away"),
    }
    if paths:
        for k, vals in paths.items():
            m[k] = ({"n": len(vals), "median": round(
                statistics.median(vals), 4),
                "mean": round(statistics.fmean(vals), 4),
                "worst": round(min(vals), 4), "best": round(max(vals), 4)}
                if vals else None)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_snap/btc5m_rec_*.jsonl.gz")
    ap.add_argument("--side", default="underdog",
                    choices=("underdog", "up", "down"))
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "MAKER_STUDY.json"))
    a = ap.parse_args(argv)
    rows = tape(a.tapes)
    cps, trades, settle = states(rows)
    n_trades = sum(len(v) for v in trades.values())
    rep = {
        "label": LABEL,
        "status": "RESEARCH ONLY / EXPLORATORY. No live maker orders.",
        "authorizes_nothing": ("changes no cohort, recorder, frozen analyzer "
                               "or authorization, and opens no venue "
                               "surface"),
        "tapes_glob": a.tapes,
        "tape_rows": len(rows),
        "btc5m_markets_with_early_checkpoints": len(cps),
        "trade_prints_observed": n_trades,
        "settlements": len(settle),
        "coverage_warning": (
            "FILL EVIDENCE IS BOUNDED BY TRADE PRINTS. If the tape carries "
            "few prints, a low measured fill rate is a statement about the "
            "EVIDENCE available, not a proven property of the venue -- and "
            "it is reported as such rather than as a maker fill rate."),
        "no_touch_equals_fill": (
            "a resting order fills only on a qualifying later print with "
            "enough size behind it; book-evolution-only cases are reported "
            "separately and never pooled"),
        "queue_assumption": ("we arrive BEHIND every contract displayed at "
                             "a better-or-equal price on our side, and our "
                             "own size must also trade"),
        "fees": {"maker_rate": str(MAKER_RATE), "taker_rate": str(TAKER_RATE),
                 "form": "rate*q*p*(1-p), cent-rounded UP",
                 "rebates": ("NONE assumed, modelled or credited anywhere "
                             "in this study")},
        "placements": [n for n, _ in PLACEMENTS],
        "placement_definition": ("a fraction through the observed spread "
                                 "from OUR passive side, snapped AWAY from "
                                 "aggression so it is never better than the "
                                 "fraction implies"),
        "side_selection_is_separate": (
            "`--side` fixes the DIRECTION; only the execution method varies, "
            "so the comparison answers the maker-vs-taker question at a "
            "constant directional decision"),
        "result": run(cps, trades, settle, a.side),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({"label": LABEL, "tape_rows": len(rows),
                      "markets": len(cps), "trade_prints": n_trades,
                      "opportunities": rep["result"]["opportunities"],
                      "headline": rep["result"]["headline"]["comparison"]},
                     sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
