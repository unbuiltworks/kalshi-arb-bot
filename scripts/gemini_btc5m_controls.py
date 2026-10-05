"""TWO DUMB BTC5m CONTROLS. One trade, held to settlement, nothing else.

INTENTIONALLY STUPID, AND THAT IS THE POINT. No exit, no scaling, no
Martingale, no flip, no filter, no Z, no settlement-state conditioning, no
threshold. One contract, once, at a frozen causal checkpoint, held to the
venue's own resolution. Existing strategies and conclusions are untouched.

CONTROL A -- DIRECTIONAL BASE RATE
  A1  buy UP   once at the checkpoint, hold
  A2  buy DOWN once at the checkpoint, hold
  Run SEPARATELY and unconditionally -- not "whichever is cheaper". This asks
  whether UP itself carries a base-rate advantage and whether Gemini prices it.

CONTROL B -- INITIAL UNDERDOG, and its exact inverse
  B1  buy the CHEAPER executable outcome once, hold
  B2  buy the DEARER executable outcome once, hold
  TWO-SIDED BOOKS ONLY. A surviving one-sided quote does not identify a
  favorite or an underdog: on this venue a bid-only book still offers a
  purchase (of DOWN at 1 - the bid), and classifying from it would label
  whichever side happens to still be quoted. The earlier underdog matrix
  deliberately pooled one-sided books; this control deliberately does not,
  which is why the two are different measurements of different populations.

THE CHECKPOINT, and why it is not T+0.
  BTC5m has a 300s tradeable life (the strike is null until availableAt =
  expiry - 300s). The recorded grid is 1/2/5/10/15/20/30/45/60/90/120s
  remaining, so the EARLIEST consistently available causal checkpoint is
  tte=120s, i.e. T+180s -- already 60% through the window. The requested
  opening ladder T+5/15/30/60 (tte 295/285/270/240) is ENTIRELY ABSENT from
  this corpus and is NOT substituted with a later observation. 120s is the
  frozen primary; 90/60/45s are reported as secondary rows because they exist,
  and TWO_SIDED coverage collapses below 90s (56 -> 30 -> 13 -> 14 windows),
  so Control B is only meaningfully testable at the first two.

EXECUTION. Executable asks only: UP pays the YES ask, DOWN pays 1 - the YES
bid and consumes the bid ladder. A side with no recorded ask is NOT TRADEABLE
and the window simply has no trade -- never a fill at some other price.
Fee: the published taker rate, 7% of q*p*(1-p). Breakeven win rate for a
hold-to-settlement buy at p is p + 0.07*p*(1-p); the zero-fee breakeven p is
reported beside it.

THE STATISTICAL UNIT IS THE WINDOW -- one trade per window, so this is
automatic here, and intervals come from a window bootstrap.

Accounting label: historical replay on Gemini's own recorded books and
settlements. No order was sent. Nothing here is realized profit.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
from typing import Any, Dict, List, Optional, Sequence

LABEL = "GEMINI_BTC5M_CONTROLS_V1"
TAKER_RATE = 0.07
PRIMARY_CHECKPOINT = 120.0
SECONDARY_CHECKPOINTS = (90.0, 60.0, 45.0)
REQUESTED_OPENING_LADDER_TTE = (295.0, 285.0, 270.0, 240.0)
TRADEABLE_LIFE_S = 300.0
WINDOWS_PER_DAY = 288

BUCKETS = (("<10c", 0.00, 0.10), ("10-20c", 0.10, 0.20),
           ("20-30c", 0.20, 0.30), ("30-40c", 0.30, 0.40),
           ("40-50c", 0.40, 0.50))


def breakeven(p: float) -> float:
    return p + TAKER_RATE * p * (1.0 - p)


def fee(p: float, q: float = 1.0) -> float:
    return TAKER_RATE * q * p * (1.0 - p)


def sides(r: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Executable asks and their depth. DOWN is bought at 1 - the YES bid."""
    out: Dict[str, Dict[str, Any]] = {}
    if r.get("up_ask") is not None:
        out["UP"] = {"ask": float(r["up_ask"]),
                     "depth": (None if r.get("up_ask_sz") is None
                               else float(r["up_ask_sz"]))}
    if r.get("bid") is not None:
        out["DOWN"] = {"ask": round(1.0 - float(r["bid"]), 6),
                       "depth": (None if r.get("down_sz") is None
                                 else float(r["down_sz"]))}
    return out


def _boot(wins: Sequence[bool], n: int = 4000,
          seed: int = 5) -> List[Optional[float]]:
    if len(wins) < 2:
        return [None, None]
    rng = random.Random(seed)
    rates = []
    for _ in range(n):
        s = [wins[rng.randrange(len(wins))] for _ in wins]
        rates.append(100.0 * sum(s) / len(s))
    rates.sort()
    return [round(rates[int(0.025 * len(rates))], 2),
            round(rates[min(len(rates) - 1, int(0.975 * len(rates)))], 2)]


def arm(trades: Sequence[Dict[str, Any]], *, windows_attempted: int,
        name: str) -> Dict[str, Any]:
    """One trade per window, q=1 contract, held to settlement."""
    if not trades:
        return {"arm": name, "trades": 0, "windows_attempted":
                windows_attempted, "why": "no window had an executable ask"}
    wins = [bool(t["won"]) for t in trades]
    nets = [t["net"] for t in trades]
    asks = [t["ask"] for t in trades]
    avg = statistics.fmean(asks)
    # chronological order for streaks/drawdown: the window ticker sorts by time
    chron = sorted(trades, key=lambda t: t["window"])
    cum = peak = dd = 0.0
    streak = worst = 0
    for t in chron:
        cum += t["net"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
        streak = 0 if t["won"] else streak + 1
        worst = max(worst, streak)
    depths = [t["depth"] for t in trades if t["depth"] is not None]
    wp = 100.0 * sum(wins) / len(wins)
    be = 100.0 * breakeven(avg)
    ci = _boot(wins)
    per_trade = statistics.fmean(nets)
    return {
        "arm": name,
        "trades": len(trades),
        "windows_attempted": windows_attempted,
        "windows_with_no_executable_ask": windows_attempted - len(trades),
        "tradeable_share_pct": round(100.0 * len(trades)
                                     / max(1, windows_attempted), 1),
        "avg_executable_ask": round(avg, 4),
        "settlement_win_pct": round(wp, 2),
        "win_pct_ci95_window_bootstrap": ci,
        "breakeven_pct_zero_fees": round(100.0 * avg, 2),
        "breakeven_pct_taker_fees": round(be, 2),
        "win_minus_breakeven_pp": round(wp - be, 2),
        "ci_low_minus_breakeven_pp": (None if ci[0] is None
                                      else round(ci[0] - be, 2)),
        "established_positive": bool(ci[0] is not None and ci[0] > be),
        "fees_usd_total": round(sum(t["fee"] for t in trades), 4),
        "fees_usd_per_trade": round(statistics.fmean(
            [t["fee"] for t in trades]), 5),
        "net_pnl_usd_total": round(sum(nets), 4),
        "net_pnl_per_trade_usd": round(per_trade, 4),
        "net_pnl_per_window_attempted_usd": round(
            sum(nets) / max(1, windows_attempted), 4),
        "net_pnl_per_day_usd": round(
            sum(nets) / max(1, windows_attempted) * WINDOWS_PER_DAY, 2),
        "max_drawdown_usd": round(dd, 4),
        "longest_loss_streak": worst,
        "capital_at_risk_per_trade_usd": {
            "median": round(statistics.median(
                [t["cost"] for t in trades]), 4),
            "max": round(max(t["cost"] for t in trades), 4)},
        "depth_contracts": (
            {"recorded": False} if not depths else
            {"recorded": True,
             "median": round(statistics.median(depths), 1),
             "p10": round(sorted(depths)[len(depths) // 10], 1),
             "max": round(max(depths), 1),
             "capacity_usd_at_median_depth": round(
                 statistics.median(depths) * avg, 2)}),
        "sizing": "q = 1 contract, once per window. No scaling, no Martingale.",
    }


def build(rows: Dict[str, Dict[float, Dict[str, Any]]], cp: float,
          picker, *, two_sided_only: bool) -> List[Dict[str, Any]]:
    out = []
    for tk, byt in rows.items():
        r = byt.get(cp)
        if r is None or r.get("up_won") is None:
            continue
        sd = sides(r)
        if two_sided_only and len(sd) < 2:
            continue
        pick = picker(sd)
        if pick is None:
            continue
        side, info = pick
        p = info["ask"]
        if not (0.0 < p < 1.0):
            continue
        winner = "UP" if r["up_won"] else "DOWN"
        f = fee(p)
        cost = p + f
        out.append({"window": tk, "side": side, "ask": p, "fee": f,
                    "cost": cost, "depth": info["depth"],
                    "won": side == winner,
                    "net": (1.0 - cost) if side == winner else -cost})
    return out


def buckets(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, lo, hi in BUCKETS:
        sub = [t for t in trades if lo <= t["ask"] < hi]
        if not sub:
            out[name] = {"trades": 0}
            continue
        wins = [bool(t["won"]) for t in sub]
        avg = statistics.fmean([t["ask"] for t in sub])
        wp = 100.0 * sum(wins) / len(wins)
        be = 100.0 * breakeven(avg)
        ci = _boot(wins)
        out[name] = {
            "trades": len(sub),
            "avg_executable_ask": round(avg, 4),
            "settlement_win_pct": round(wp, 2),
            "breakeven_pct_zero_fees": round(100.0 * avg, 2),
            "breakeven_pct_taker_fees": round(be, 2),
            "win_minus_breakeven_pp": round(wp - be, 2),
            "win_pct_ci95_window_bootstrap": ci,
            "ci_low_minus_breakeven_pp": (None if ci[0] is None
                                          else round(ci[0] - be, 2)),
            "established_positive": bool(ci[0] is not None and ci[0] > be),
            "net_pnl_per_trade_usd": round(statistics.fmean(
                [t["net"] for t in sub]), 4),
            "side_mix": dict(collections.Counter(t["side"] for t in sub)),
        }
    return out


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    raw = [r for r in json.load(open(a.ladder, encoding="utf-8"))
           if str(r.get("ticker", "")).startswith("BTC05M")]
    rows: Dict[str, Dict[float, Dict[str, Any]]] = collections.defaultdict(
        dict)
    for r in raw:
        if r.get("tte") is not None:
            rows[r["ticker"]][float(r["tte"])] = r
    grid = sorted({float(r["tte"]) for r in raw}, reverse=True)
    settled = {tk for tk, b in rows.items()
               if any(x.get("up_won") is not None for x in b.values())}
    n_w = len(settled)

    def at(cp: float) -> Dict[str, Any]:
        up = build(rows, cp, lambda s: (("UP", s["UP"]) if "UP" in s
                                        else None), two_sided_only=False)
        dn = build(rows, cp, lambda s: (("DOWN", s["DOWN"]) if "DOWN" in s
                                        else None), two_sided_only=False)
        cheap = build(rows, cp, lambda s: min(s.items(),
                                              key=lambda kv: kv[1]["ask"]),
                      two_sided_only=True)
        dear = build(rows, cp, lambda s: max(s.items(),
                                             key=lambda kv: kv[1]["ask"]),
                     two_sided_only=True)
        two = len([1 for tk, b in rows.items()
                   if cp in b and b[cp].get("up_won") is not None
                   and len(sides(b[cp])) == 2])
        return {
            "checkpoint_tte_s": cp,
            "equivalent_time_since_open_s": round(TRADEABLE_LIFE_S - cp, 1),
            "windows_settled": n_w,
            "two_sided_windows": two,
            "CONTROL_A": {
                "A1_buy_UP_always": arm(up, windows_attempted=n_w,
                                        name="A1 buy UP once, hold"),
                "A2_buy_DOWN_always": arm(dn, windows_attempted=n_w,
                                          name="A2 buy DOWN once, hold"),
                "unconditional_UP_settlement_rate_pct": round(
                    100.0 * sum(1 for tk in settled
                                if any(x.get("up_won") for x in
                                       rows[tk].values())) / n_w, 2),
            },
            "CONTROL_B": {
                "two_sided_only": True,
                "B1_buy_initial_UNDERDOG": arm(
                    cheap, windows_attempted=two,
                    name="B1 buy cheaper executable outcome once, hold"),
                "B2_buy_initial_FAVORITE": arm(
                    dear, windows_attempted=two,
                    name="B2 buy dearer executable outcome once, hold"),
                "B1_by_price_bucket": buckets(cheap),
                "B1_side_mix": dict(collections.Counter(
                    t["side"] for t in cheap)),
                "B2_side_mix": dict(collections.Counter(
                    t["side"] for t in dear)),
            },
        }

    rep: Dict[str, Any] = {
        "label": LABEL, "duration": "BTC5M",
        "new_or_previously_run": {
            "CONTROL_A": ("NEW. No prior study bought UP or DOWN "
                          "unconditionally; every earlier arm selected a side "
                          "by price (cheaper/dearer) or by a state proxy."),
            "CONTROL_B1_underdog_two_sided_only": (
                "NEW. The earlier underdog matrix pooled ONE-SIDED books, "
                "where the 'cheaper executable side' is whichever side "
                "happens to still be quoted. Restricting to two-sided books "
                "is a different population and was never measured."),
            "CONTROL_B2_favorite_two_sided_only": (
                "OVERLAPS an earlier measurement: the favorite test already "
                "required both sides executable, so its 120s row is the same "
                "population. It is re-reported here for symmetry and the "
                "numbers should agree."),
        },
        "checkpoint_choice": {
            "recorded_grid_tte_s": grid,
            "tradeable_life_s": TRADEABLE_LIFE_S,
            "earliest_consistently_available_tte_s": PRIMARY_CHECKPOINT,
            "equivalent_time_since_open_s": TRADEABLE_LIFE_S
            - PRIMARY_CHECKPOINT,
            "requested_opening_ladder_tte_s": list(
                REQUESTED_OPENING_LADDER_TTE),
            "requested_opening_ladder_status": (
                "ENTIRELY ABSENT from this corpus. NOT substituted with a "
                "later observation; it needs the P2 recorder."),
            "primary": PRIMARY_CHECKPOINT,
            "secondary": list(SECONDARY_CHECKPOINTS),
            "secondary_caveat": ("two-sided coverage collapses below 90s "
                                 "(56 -> 30 -> 13 -> 14 windows), so Control "
                                 "B is only meaningfully testable at 120s "
                                 "and 90s"),
        },
        "discipline": ("one trade per window, q=1 contract, held to "
                       "settlement. No exits, scaling, Martingale, flips, "
                       "filters, Z or state conditioning. Executable asks "
                       "only; no recorded ask means no trade."),
        "fee_model": {"taker_rate": TAKER_RATE,
                      "formula": "rate * q * p * (1 - p)",
                      "breakeven": "p + 0.07*p*(1-p)",
                      "note": ("published schedule, not reconciled against "
                               "our own fills; zero-fee breakeven reported "
                               "beside it")},
        "by_checkpoint": {"%ds" % int(cp): at(cp)
                          for cp in (PRIMARY_CHECKPOINT,)
                          + SECONDARY_CHECKPOINTS},
        "accounting_label": ("historical replay on Gemini's own recorded "
                             "books and settlements; no order was sent"),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    p = rep["by_checkpoint"]["120s"]
    print("%s  primary tte=120s (T+180s)  windows=%d two_sided=%d"
          % (LABEL, p["windows_settled"], p["two_sided_windows"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
