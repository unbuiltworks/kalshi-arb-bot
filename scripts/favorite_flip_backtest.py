"""FAVORITE ACCUMULATION / FLIP: a hold-to-settlement backtest.

A SEPARATE LANE. This strategy has nothing to do with the frozen .96
Gemini control and nothing to do with the L5 inference model. It does not
import `ci15m_policy`, it reads no frozen threshold, and its results are
written to their own artifact. Do not pool them.

THE RULE, exactly as specified and with no additions:
  * BTC15m, observed continuously.
  * At a fixed cadence, identify the currently favored outcome.
  * If an outcome is above 0.50, allocate a FIXED DOLLAR amount to it.
  * Same favorite next interval -> allocate again.
  * Leadership flips -> the next allocation goes to the new favorite.
  * Inventory is permitted on both outcomes.
  * No martingale, no size increase because earlier inventory is losing:
    the allocation is a constant, and nothing in the loop reads P&L.
  * Inventory is reconstructed after every fill.
  * HOLD TO SETTLEMENT. There is no exit rule, so exit discretion cannot
    manufacture hindsight.

WHY THIS DATASET. Zenodo 22084977 `updown_15m.parquet`: 1-second full bid
and ask ladders for Polymarket BTC/ETH 15-minute up/down markets,
2026-08-21..24, CC-BY-4.0, sha256
d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959. It
carries `winner` and `is_winning_outcome`, so settlement is READ rather
than inferred, and it carries ask ladders, so fills are walked against
real depth.

EXECUTION RULES, AND THEY ARE DELIBERATELY UNFLATTERING:
  * fills WALK THE ASK LADDER upward, paying each level's price;
  * there is NO midpoint fill and NO touch-equals-fill;
  * a dollar allocation the displayed depth cannot fill is FILLED ONLY AS
    FAR AS THE DEPTH GOES, and the shortfall is recorded, never assumed;
  * a one-sided book (no ask on the favored side) simply cannot be bought
    into, which the README warns is common late in a window.

IDENTIFICATION vs EXECUTION are different prices and are kept apart. The
favorite is identified by MID ((best_bid+best_ask)/2), because that is
what "the market favors this outcome" means; the purchase is executed on
the ASK LADDER. A bid-based identification is reported as a sensitivity
so the choice is visible rather than hidden.

FEES. This repository holds NO authenticated Polymarket fee measurement.
The base case therefore charges ZERO and says so; a stated per-dollar
sensitivity is reported alongside it and never merged into the base case.

EVERY WINDOW IS INCLUDED, flip-heavy ones above all, and the report
conditions P&L on flip count because funding the flips is the hypothesis.

Accounting label: historical replay on third-party public data. No order
was sent.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "FAVORITE_FLIP_BACKTEST_V1"
PARQUET_SHA256 = ("d86191506e416026f460338b1b238c628b3fcee37f468e646b3a"
                  "2256bd0be959")
SOURCE = ("Zenodo 22084977 updown_15m.parquet (CC-BY-4.0), 1-second "
          "Polymarket L2, 2026-08-21..24")
CADENCES = (15.0, 30.0, 60.0, 120.0)
INCREMENTS = (0.50, 1.00)
#: reported as a SENSITIVITY only. No authenticated Polymarket fee exists
#: in this repository, so the base case is zero and labelled.
FEE_PER_DOLLAR_SENSITIVITY = 0.02


def load(path: str, coin: str = "btc") -> Dict[str, Dict[str, Any]]:
    """One entry per window: both outcomes' second-by-second ladders."""
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=[
        "market_slug", "coin", "contract_length", "token_id", "outcome",
        "winner", "is_winning_outcome", "market_end_at", "captured_at",
        "seconds_to_close", "ask_prices", "ask_sizes", "best_bid",
        "best_ask"])
    d = t.to_pydict()
    wins: Dict[str, Dict[str, Any]] = {}
    n = len(d["market_slug"])
    for i in range(n):
        if d["coin"][i] != coin:
            continue
        slug = d["market_slug"][i]
        w = wins.setdefault(slug, {"slug": slug,
                                   "winner_raw": d["winner"][i],
                                   "winning_outcome": None,
                                   "ticks": collections.defaultdict(dict)})
        out = str(d["outcome"][i])
        stc = d["seconds_to_close"][i]
        if stc is None:
            continue
        # THE WINNING OUTCOME COMES FROM THE PRODUCER'S OWN PER-ROW FLAG,
        # never from mapping its `winner` string myself. `winner` is
        # "yes"/"no" while `outcome` is "Up"/"Down", so `qty[winner]` was
        # always zero and EVERY window scored a 100% loss -- a 0% win rate
        # across 33 windows and 8 configurations, which is what finally
        # gave it away. `is_winning_outcome` is True on exactly the rows
        # of the outcome that paid, so it needs no translation.
        if bool(d["is_winning_outcome"][i]):
            w["winning_outcome"] = out
        w["ticks"][round(float(stc), 0)][out] = {
            "ask_prices": list(d["ask_prices"][i] or []),
            "ask_sizes": list(d["ask_sizes"][i] or []),
            "best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
            "is_winner": bool(d["is_winning_outcome"][i]),
        }
    for w in wins.values():
        w["ticks"] = dict(sorted(w["ticks"].items(), key=lambda kv: -kv[0]))
    return wins


def _mid(side: Dict[str, Any]) -> Optional[float]:
    b, a = side.get("best_bid"), side.get("best_ask")
    if b is None or a is None:
        return None
    return (b + a) / 2.0


def favorite(tick: Dict[str, Any], *, by: str = "mid"
             ) -> Tuple[Optional[str], Dict[str, Optional[float]]]:
    """The favored outcome at this instant, or None if undecidable."""
    px: Dict[str, Optional[float]] = {}
    for out, side in tick.items():
        px[out] = _mid(side) if by == "mid" else side.get("best_bid")
    live = {o: p for o, p in px.items() if p is not None}
    if not live:
        return None, px
    best = max(live, key=lambda o: live[o])
    return (best if live[best] > 0.50 else None), px


def buy_dollars(side: Dict[str, Any], dollars: float
                ) -> Dict[str, Any]:
    """Walk the ASK ladder spending up to `dollars`. No midpoint, no
    touch-equals-fill, and never more than the depth shown."""
    prices = side.get("ask_prices") or []
    sizes = side.get("ask_sizes") or []
    remaining = dollars
    shares = 0.0
    spent = 0.0
    used: List[Dict[str, float]] = []
    for p, s in sorted(zip(prices, sizes), key=lambda t: t[0]):
        if remaining <= 1e-12 or p is None or s is None or p <= 0:
            break
        want = remaining / p
        take = min(float(s), want)
        if take <= 0:
            continue
        shares += take
        spent += take * p
        remaining -= take * p
        used.append({"price": float(p), "shares": take})
    return {"shares": shares, "spent": spent,
            "shortfall_dollars": max(0.0, dollars - spent),
            "filled": spent > 0, "levels": used,
            "vwap": (spent / shares if shares else None)}


def run_window(w: Dict[str, Any], *, cadence: float, dollars: float,
               ident: str = "mid") -> Dict[str, Any]:
    qty: Dict[str, float] = collections.defaultdict(float)
    cost: Dict[str, float] = collections.defaultdict(float)
    fills: List[Dict[str, Any]] = []
    flips = 0
    prev_fav: Optional[str] = None
    allocations = 0
    skipped_no_fav = 0
    skipped_no_depth = 0
    shortfall = 0.0
    peak_unmatched = 0.0

    stcs = sorted(w["ticks"], reverse=True)
    if not stcs:
        return {"slug": w["slug"], "n_ticks": 0, "skipped": True}
    start = stcs[0]
    # cadence grid from the window's first observation toward expiry
    targets = []
    t = start
    while t > 0:
        targets.append(t)
        t -= cadence

    # true 1-second leadership flips, independent of the cadence grid
    lead_flips = 0
    last_lead: Optional[str] = None
    for stc in stcs:
        f, _ = favorite(w["ticks"][stc], by=ident)
        if f is not None:
            if last_lead is not None and f != last_lead:
                lead_flips += 1
            last_lead = f

    for want in targets:
        # the nearest observation at or before this grid point
        avail = [s for s in stcs if s <= want]
        if not avail:
            continue
        stc = max(avail)
        tick = w["ticks"][stc]
        fav, _px = favorite(tick, by=ident)
        if fav is None:
            skipped_no_fav += 1
            continue
        if prev_fav is not None and fav != prev_fav:
            flips += 1
        prev_fav = fav
        got = buy_dollars(tick.get(fav) or {}, dollars)
        if not got["filled"]:
            skipped_no_depth += 1
            continue
        allocations += 1
        shortfall += got["shortfall_dollars"]
        qty[fav] += got["shares"]
        cost[fav] += got["spent"]
        outs = list(w["ticks"][stc])
        m = min((qty[o] for o in outs), default=0.0) if len(outs) > 1 else 0.0
        tot = sum(qty.values())
        unmatched = tot - 2 * m
        peak_unmatched = max(peak_unmatched, unmatched)
        fills.append({"seconds_to_close": stc, "outcome": fav,
                      "shares": got["shares"], "spent": got["spent"],
                      "vwap": got["vwap"]})

    outs = sorted(set(list(qty) + [o for s in w["ticks"].values()
                                   for o in s]))
    matched = (min(qty.get(o, 0.0) for o in outs) if len(outs) > 1 else 0.0)
    deployed = sum(cost.values())
    # SETTLEMENT IS READ, NOT INFERRED: the dataset names the winner.
    winner = w["winning_outcome"]
    if winner is None:
        return {"slug": w["slug"], "n_ticks": len(stcs), "skipped": True,
                "why": "no row carried is_winning_outcome=True"}
    payout = qty.get(winner, 0.0) * 1.0
    matched_cost = 0.0
    for o in outs:
        if qty.get(o, 0.0) > 0 and matched > 0:
            matched_cost += matched * (cost[o] / qty[o])
    matched_pnl = (matched * len(outs) * 0.0) + (matched * 1.0 - matched_cost
                                                 if matched > 0 else 0.0)
    resid_out = max(outs, key=lambda o: qty.get(o, 0.0)) if outs else None
    resid_qty = (qty.get(resid_out, 0.0) - matched) if resid_out else 0.0
    resid_cost = ((cost[resid_out] / qty[resid_out]) * resid_qty
                  if resid_out and qty.get(resid_out) else 0.0)
    resid_pnl = ((resid_qty if resid_out == winner else 0.0) - resid_cost)
    net = payout - deployed
    return {
        "slug": w["slug"], "winner": winner,
        "winner_raw": w["winner_raw"],
        "n_ticks": len(stcs), "window_span_s": start,
        "allocations": allocations,
        "cadence_flips": flips, "leadership_flips_1s": lead_flips,
        "skipped_no_favorite": skipped_no_fav,
        "skipped_no_depth": skipped_no_depth,
        "shortfall_dollars": round(shortfall, 4),
        "qty": {o: round(qty.get(o, 0.0), 4) for o in outs},
        "cost": {o: round(cost.get(o, 0.0), 4) for o in outs},
        "capital_deployed": round(deployed, 4),
        "matched_qty": round(matched, 4),
        "matched_pnl": round(matched_pnl, 4),
        "residual_outcome": resid_out,
        "residual_qty": round(resid_qty, 4),
        "residual_pnl": round(resid_pnl, 4),
        "peak_unmatched_shares": round(peak_unmatched, 4),
        "payout": round(payout, 4),
        "net_zero_fees": round(net, 4),
        "fee_sensitivity": round(deployed * FEE_PER_DOLLAR_SENSITIVITY, 4),
        "net_with_fee_sensitivity": round(
            net - deployed * FEE_PER_DOLLAR_SENSITIVITY, 4),
        "fills": fills,
    }


def _st(xs: Sequence[float]) -> Dict[str, Any]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "worst": round(s[0], 4),
            "p10": round(s[max(0, len(s) // 10)], 4),
            "median": round(statistics.median(s), 4),
            "p90": round(s[min(len(s) - 1, 9 * len(s) // 10)], 4),
            "best": round(s[-1], 4),
            "mean": round(statistics.fmean(s), 4),
            "total": round(sum(s), 4)}


def _drawdown(nets: Sequence[float]) -> float:
    """Worst peak-to-trough on the cumulative curve, windows in order."""
    cum = 0.0
    peak = 0.0
    dd = 0.0
    for n in nets:
        cum += n
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 4)


def summarise(rows: List[Dict[str, Any]], *, cadence: float,
              dollars: float) -> Dict[str, Any]:
    rows = [r for r in rows if not r.get("skipped")]
    nets = [r["net_zero_fees"] for r in rows]
    by_flip: Dict[str, Any] = {}
    for key, pred in (("0_flips", lambda f: f == 0),
                      ("1_flip", lambda f: f == 1),
                      ("2_flips", lambda f: f == 2),
                      ("3+_flips", lambda f: f >= 3)):
        sub = [r for r in rows if pred(r["cadence_flips"])]
        by_flip[key] = {
            "windows": len(sub),
            "net_zero_fees": _st([r["net_zero_fees"] for r in sub]),
            "capital_deployed": _st([r["capital_deployed"] for r in sub]),
            "matched_pnl": _st([r["matched_pnl"] for r in sub]),
            "residual_pnl": _st([r["residual_pnl"] for r in sub]),
            "win_rate_pct": (None if not sub else round(
                100.0 * sum(1 for r in sub if r["net_zero_fees"] > 0)
                / len(sub), 1)),
        }
    n = len(rows)
    windows_per_day = 96
    return {
        "cadence_s": cadence, "dollars_per_allocation": dollars,
        "windows": n,
        "net_per_window_zero_fees": (None if not n
                                     else round(sum(nets) / n, 4)),
        "est_net_per_day_zero_fees": (None if not n else round(
            sum(nets) / n * windows_per_day, 2)),
        "windows_per_day_assumed": windows_per_day,
        "win_rate_pct": (None if not n else round(
            100.0 * sum(1 for x in nets if x > 0) / n, 1)),
        "net_distribution": _st(nets),
        "max_drawdown_cumulative": _drawdown(nets),
        "capital_deployed": _st([r["capital_deployed"] for r in rows]),
        "allocations": _st([float(r["allocations"]) for r in rows]),
        "peak_unmatched_shares": _st([r["peak_unmatched_shares"]
                                      for r in rows]),
        "matched_pnl": _st([r["matched_pnl"] for r in rows]),
        "residual_pnl": _st([r["residual_pnl"] for r in rows]),
        "cadence_flips": _st([float(r["cadence_flips"]) for r in rows]),
        "leadership_flips_1s": _st([float(r["leadership_flips_1s"])
                                    for r in rows]),
        "shortfall_dollars": _st([r["shortfall_dollars"] for r in rows]),
        "skipped_no_depth": _st([float(r["skipped_no_depth"])
                                 for r in rows]),
        "fees": {
            "base_case": 0.0,
            "why_zero": ("this repository holds NO authenticated "
                         "Polymarket fee measurement, so the base case "
                         "charges nothing and says so"),
            "sensitivity_per_dollar": FEE_PER_DOLLAR_SENSITIVITY,
            "net_per_window_with_sensitivity": (
                None if not n else round(
                    sum(r["net_with_fee_sensitivity"] for r in rows) / n, 4)),
        },
        "by_flip_count": by_flip,
        "per_window": [{k: r[k] for k in
                        ("slug", "winner", "allocations", "cadence_flips",
                         "leadership_flips_1s", "capital_deployed",
                         "matched_qty", "matched_pnl", "residual_pnl",
                         "peak_unmatched_shares", "payout",
                         "net_zero_fees", "skipped_no_depth",
                         "shortfall_dollars")} for r in rows],
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--ident", default="mid", choices=("mid", "bid"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    wins = load(a.parquet)
    grid: Dict[str, Any] = {}
    for dollars in INCREMENTS:
        for cad in CADENCES:
            rows = [run_window(w, cadence=cad, dollars=dollars,
                               ident=a.ident) for w in wins.values()]
            grid["$%.2f@%gs" % (dollars, cad)] = summarise(
                rows, cadence=cad, dollars=dollars)
    rep = {
        "label": LABEL, "source": SOURCE,
        "parquet_sha256": PARQUET_SHA256,
        "identification": ("MID for identifying the favorite; ASK LADDER "
                           "for execution" if a.ident == "mid"
                           else "BID for identifying the favorite"),
        "n_windows": len(wins),
        "execution_rules": ("walk the ask ladder upward; no midpoint fill; "
                            "no touch-equals-fill; a dollar allocation the "
                            "displayed depth cannot fill is filled only as "
                            "far as the depth goes and the shortfall is "
                            "recorded"),
        "exit_rule": "NONE -- hold to settlement, so exit discretion "
                     "cannot create hindsight bias",
        "separation": ("this is NOT the frozen .96 Gemini control and NOT "
                       "the L5 inference model; results are not pooled "
                       "with either"),
        "accounting_label": "historical replay on third-party public data",
        "grid": grid,
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s  windows=%d  %s" % (LABEL, len(wins), rep["identification"]))
    print("\n%-12s %7s %8s %9s %7s %9s %9s %9s %8s"
          % ("cfg", "wins", "net/win", "$/day", "win%", "median", "p10",
             "worst", "maxDD"))
    for k, v in grid.items():
        d = v["net_distribution"]
        print("%-12s %7d %8s %9s %7s %9s %9s %9s %8s"
              % (k, v["windows"], v["net_per_window_zero_fees"],
                 v["est_net_per_day_zero_fees"], v["win_rate_pct"],
                 d.get("median"), d.get("p10"), d.get("worst"),
                 v["max_drawdown_cumulative"]))
    print("\nFLIP CONDITIONING (the central hypothesis) -- $1.00 @ 60s")
    base = grid.get("$1.00@60s")
    if base:
        print("%-10s %7s %10s %10s %10s %9s"
              % ("flips", "windows", "net/win", "matched", "residual",
                 "win%"))
        for k, v in base["by_flip_count"].items():
            print("%-10s %7d %10s %10s %10s %9s"
                  % (k, v["windows"], v["net_zero_fees"].get("mean"),
                     v["matched_pnl"].get("mean"),
                     v["residual_pnl"].get("mean"), v["win_rate_pct"]))
        print("\ncapital/window %s" % base["capital_deployed"])
        print("peak unmatched shares %s" % base["peak_unmatched_shares"])
        print("cadence flips %s" % base["cadence_flips"])
        print("1s leadership flips %s" % base["leadership_flips_1s"])
        print("no-depth skips %s" % base["skipped_no_depth"])
        print("fees: base %s (%s); sensitivity net/win %s"
              % (base["fees"]["base_case"], "zero, unmeasured",
                 base["fees"]["net_per_window_with_sensitivity"]))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
