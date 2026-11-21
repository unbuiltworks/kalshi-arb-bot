"""L5's residual-risk envelope, in DOLLARS and in TIME.

OWNER DECISION #167 5566810119 #1: "Quantify L5 residual risk in time AND
dollars, not just units. From canonical history measure residual notional at
creation, p50/p75/p90/p95, residual holding duration, time-to-settlement at
creation, fraction complemented before settlement, and loss distribution.
Segment BTC/ETH 5m vs 15m and by L5 era/size."

WHY IT MATTERS. R3L lost $6.2855 on one unpaired football residual held for
hours, and made money on both markets that completed their pair. Our residual
cap is expressed in UNITS (6), which let 19.27 shares at $0.3262 ride. This
module measures what L5 itself tolerated, so R4's cap can be set from L5's
envelope rather than from the market that happened to lose.

DEFINITIONS (all from the canonical file's own columns, none inferred):
  residual_notional = |residual_qty_signed| x (cum_<side>_spend / cum_<side>_qty)
      -- the dollars that are NOT protected by a matched pair, i.e. exactly
      what is lost if that side expires worthless. On R3L's wisc-nd this is
      19.27 x 0.3262 = $6.2855, the realized loss to the cent.
  A matched pair pays $1 whatever happens, so it carries no settlement risk;
  only the residual does.

THE TWO TRANSFERABLE RATIOS. Absolute dollars do not transfer from a $93
median L5 window to our $7.50 market cap, and absolute seconds do not
transfer from a 15-minute crypto window to a 3.5-hour football game. What
transfers is:
  * residual notional as a FRACTION OF THE WINDOW'S OWN SPEND, and
  * residual open time as a FRACTION OF THE MARKET'S OWN LIFE.
Both are reported, and they are the numbers R4 should be built from.

PROVENANCE / LIMITS: analysis/l5/l5_canonical_sequence.csv.gz, executed
trades from the public activity feed. Quotes and cancels are invisible, so
"complemented before settlement" means a complementary FILL arrived, never
that L5 was trying and failed. Nothing here is a P&L claim about our account.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence

from src.l5_concurrency_study import CANONICAL_PATH, load_rows, provenance

# dollars; below this a residual is float dust, not exposure
NOTIONAL_TOL = 0.01


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _avg_price(row: dict, side: str) -> float:
    if side == "Up":
        qty, spend = _f(row.get("cum_up_qty")), _f(row.get("cum_up_spend"))
    elif side == "Down":
        qty, spend = _f(row.get("cum_down_qty")), _f(row.get("cum_down_spend"))
    else:
        return 0.0
    return (spend / qty) if qty else 0.0


def residual_at(row: dict) -> dict:
    """The unmatched exposure standing after this fill."""
    qty = abs(_f(row.get("residual_qty_signed")))
    side = str(row.get("residual_side") or "")
    avg = _avg_price(row, side)
    return {"qty": qty, "side": side, "avg_price": round(avg, 6),
            "notional": round(qty * avg, 6),
            "seconds_to_settlement": int(_f(row.get("seconds_to_settlement"))),
            "timestamp": int(_f(row.get("timestamp")))}


def window_residual(rows: Sequence[dict]) -> dict:
    """The whole residual path of one market episode."""
    rs = sorted(rows, key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))
    last = rs[-1]
    life_s = int(_f(last.get("duration_minutes")) * 60) or None
    t_settle = int(_f(last.get("timestamp"))) + int(_f(last.get("seconds_to_settlement")))
    path = [residual_at(r) for r in rs]
    spend = sum(_f(r.get("dollars")) for r in rs)

    peak = max(path, key=lambda p: p["notional"])
    final = path[-1]
    first_open = next((p for p in path if p["notional"] > NOTIONAL_TOL), None)
    # the last moment the exposure was still BELOW its final level: the final
    # residual has been standing, unreduced, ever since
    standing_from = None
    if final["notional"] > NOTIONAL_TOL:
        standing_from = path[0]
        for p in path:
            if p["notional"] < final["notional"] - NOTIONAL_TOL:
                standing_from = p
        standing_from = standing_from["timestamp"]

    # creation events: every fill that ADDS unmatched exposure
    creations = []
    prev = 0.0
    for p in path:
        if p["notional"] > prev + NOTIONAL_TOL:
            creations.append({"added_usd": round(p["notional"] - prev, 6),
                              "level_usd": p["notional"],
                              "seconds_to_settlement": p["seconds_to_settlement"]})
        prev = p["notional"]

    won = bool(final["side"]) and final["side"] == str(last.get("settlement_outcome") or "")
    pnl = (final["qty"] * (1.0 - final["avg_price"]) if won
           else -final["notional"]) if final["notional"] > NOTIONAL_TOL else 0.0
    open_s = (t_settle - first_open["timestamp"]) if first_open else 0

    return {
        "window_id": last["window_id"], "market_slug": last["market_slug"],
        "asset": last.get("asset", ""),
        "duration_minutes": int(_f(last.get("duration_minutes"))),
        "market_life_s": life_s, "settlement_ts": t_settle,
        "fills": len(rs), "window_spend_usd": round(spend, 6),
        "peak_residual_usd": peak["notional"],
        "peak_at_seconds_to_settlement": peak["seconds_to_settlement"],
        "final_residual_usd": final["notional"],
        "final_residual_qty": final["qty"], "final_residual_side": final["side"],
        "final_residual_avg_price": final["avg_price"],
        "creations": creations,
        "first_open_seconds_to_settlement": (
            first_open["seconds_to_settlement"] if first_open else None),
        "residual_open_s": open_s,
        "residual_open_share_of_life": (round(open_s / life_s, 4)
                                        if life_s and open_s else 0.0),
        "final_unattended_s": final["seconds_to_settlement"],
        "standing_since_ts": standing_from,
        "final_standing_s": (t_settle - standing_from) if standing_from else 0,
        "complemented_by_settlement": final["notional"] <= NOTIONAL_TOL,
        "peak_reduced_share": (round(1.0 - final["notional"] / peak["notional"], 4)
                               if peak["notional"] > NOTIONAL_TOL else None),
        "peak_share_of_window_spend": (round(peak["notional"] / spend, 4)
                                       if spend else None),
        "final_share_of_window_spend": (round(final["notional"] / spend, 4)
                                        if spend else None),
        "residual_won": won if final["notional"] > NOTIONAL_TOL else None,
        "residual_pnl_usd": round(pnl, 6),
    }


def windows(rows: Iterable[dict]) -> List[dict]:
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    out = [window_residual(rs) for rs in by_win.values()]
    out.sort(key=lambda w: w["settlement_ts"])
    return out


def dist(xs: Sequence[float], nd: int = 4) -> dict:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {"n": 0}
    def q(p):
        return round(xs[min(n - 1, int(p * n))], nd)
    return {"n": n, "mean": round(sum(xs) / n, nd), "p10": q(0.10), "p25": q(0.25),
            "p50": q(0.50), "p75": q(0.75), "p90": q(0.90), "p95": q(0.95),
            "p99": q(0.99), "max": round(xs[-1], nd)}


def envelope(ws: Sequence[dict]) -> dict:
    """The risk envelope: how much, for how long, how often reduced, at what cost."""
    exposed = [w for w in ws if w["peak_residual_usd"] > NOTIONAL_TOL]
    held = [w for w in ws if not w["complemented_by_settlement"]]
    won = [w for w in held if w["residual_won"]]
    pnl = [w["residual_pnl_usd"] for w in held]
    losses = [p for p in pnl if p < 0]
    return {
        "windows": len(ws),
        "windows_taking_unmatched_exposure": len(exposed),
        "windows_holding_residual_to_settlement": len(held),
        "share_complemented_before_settlement": (
            round(1.0 - len(held) / len(exposed), 4) if exposed else None),
        "dollars": {
            "peak_residual_usd": dist([w["peak_residual_usd"] for w in exposed]),
            "final_residual_usd": dist([w["final_residual_usd"] for w in held]),
            "window_spend_usd": dist([w["window_spend_usd"] for w in ws], 2),
            "peak_share_of_window_spend": dist(
                [w["peak_share_of_window_spend"] for w in exposed
                 if w["peak_share_of_window_spend"] is not None]),
            "final_share_of_window_spend": dist(
                [w["final_share_of_window_spend"] for w in held
                 if w["final_share_of_window_spend"] is not None]),
            "added_per_creation_usd": dist(
                [c["added_usd"] for w in ws for c in w["creations"]]),
        },
        "time": {
            "seconds_to_settlement_at_first_exposure": dist(
                [w["first_open_seconds_to_settlement"] for w in exposed
                 if w["first_open_seconds_to_settlement"] is not None], 0),
            "residual_open_s": dist([w["residual_open_s"] for w in exposed], 0),
            "residual_open_share_of_market_life": dist(
                [w["residual_open_share_of_life"] for w in exposed]),
            "final_standing_s": dist([w["final_standing_s"] for w in held], 0),
            "final_unattended_s": dist([w["final_unattended_s"] for w in held], 0),
            "market_life_s": dist([w["market_life_s"] for w in ws if w["market_life_s"]], 0),
        },
        "outcome": {
            "residual_won": len(won),
            "residual_lost": len(held) - len(won),
            "win_rate": round(len(won) / len(held), 4) if held else None,
            "residual_pnl_usd": dist(pnl, 2),
            "loss_only_usd": dist(losses, 2),
            "total_residual_pnl_usd": round(sum(pnl), 2),
            "reading": "a matched pair pays $1 whatever happens; only the "
                       "residual carries settlement risk, so this is the whole "
                       "of L5's outcome exposure",
        },
    }


def by(ws: Sequence[dict], key) -> dict:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for w in ws:
        groups[key(w)].append(w)
    return {k: envelope(v) for k, v in sorted(groups.items())}


def _lane(w: dict) -> str:
    return "%s-%dm" % (w["asset"], w["duration_minutes"])


def _era(w: dict) -> str:
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(w["settlement_ts"]).strftime("%Y-%m")


def _size_band(w: dict) -> str:
    s = w["window_spend_usd"]
    return ("spend<$25" if s < 25 else "spend$25-100" if s < 100
            else "spend$100-500" if s < 500 else "spend>=$500")


def study(rows: Optional[Sequence[dict]] = None,
          path: str = CANONICAL_PATH) -> dict:
    rows = list(rows) if rows is not None else load_rows(path)
    ws = windows(rows)
    prov = provenance(rows)
    prov["limits"] = list(prov["limits"]) + [
        "residual_notional = unmatched shares x their own average cost: exactly "
        "what is lost if that side expires worthless",
        "'complemented before settlement' means a complementary FILL arrived; "
        "quotes and cancels are invisible in this feed",
    ]
    return {
        "owner_decision": "#167 5566810119 item 1",
        "provenance": prov,
        "overall": envelope(ws),
        "by_lane": by(ws, _lane),
        "by_era": by(ws, _era),
        "by_size_band": by(ws, _size_band),
    }
