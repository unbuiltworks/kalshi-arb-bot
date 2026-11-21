"""OWNER 5570456323 -- capital recycling: time from first-side fill to
complement fill to capital economically released, for L5's history and for
our own at-size markets.

On this venue a matched pair is netted to cash the moment the complement
prints (the balance jumps by the matched quantity), so the capital in a
lot is RELEASED at the complement for the matched part and at settlement
for the residual part. FIFO lots (src/l5_extend_productivity.window_lots)
give both times per lot. Per market/window:

  first_fill -> first_complement           seconds (None if never completed)
  dollars released at complement           sum(lot usd x matched share)
  dollars released at settlement           the residual
  capital-hours                            sum(lot usd x hours to its release)
  recycling time                           capital-hours / deployed dollars (hours per dollar)
  turnover per capital-hour                deployed / capital-hours

Ours: R4 fills from analysis/l5/r4_orders.jsonl (side, price, qty,
log_ts); R3L fills from src.l5_r4_counterfactual.R3L_FILLS (clock times on
2026-09-07); settlement times from the scoreboard's resolution_utc. Read-only.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_cap_tradeoff_curve as cv
from src import l5_extend_productivity as ep

A = os.path.join("analysis", "l5")
OUT_PATH = os.path.join(A, "l5_capital_recycling.json")
R4_ORDERS_PATH = os.path.join(A, "r4_orders.jsonl")
SCOREBOARD_PATH = os.path.join(A, "l5_economic_scoreboard.json")
R3L_DAY = "2026-09-07"


def _pct(xs: Sequence[float]) -> dict:
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if not n:
        return {"n": 0}
    q = lambda p: round(xs[min(n - 1, int(p * n))], 3)
    return {"n": n, "p10": q(0.10), "p25": q(0.25), "p50": q(0.50), "p75": q(0.75), "p90": q(0.90), "mean": round(sum(xs) / n, 3)}


def market_recycling(lots: Sequence[dict], t_settle: Optional[float], t_first_hint: Optional[float] = None) -> dict:
    """Recycling metrics for one market/window from its FIFO lots (with
    'first_match_t' kept, see lots_with_match_times)."""
    if not lots:
        return {"lots": 0}
    t_first = min(l["t"] for l in lots)
    first_comp = min((l["first_match_t"] for l in lots if l.get("first_match_t") is not None), default=None)
    deployed = sum(D(str(l["usd"])) for l in lots)
    released_at_complement = D(0)
    cap_hours = D(0)
    for l in lots:
        usd = D(str(l["usd"]))
        m = D(str(l["matched_share"]))
        released_at_complement += usd * m
        if l.get("first_match_t") is not None:
            cap_hours += usd * m * D(str((l["first_match_t"] - l["t"]) / 3600.0))
        if t_settle is not None:
            cap_hours += usd * (1 - m) * D(str(max(0.0, (t_settle - l["t"]) / 3600.0)))
    residual = deployed - released_at_complement
    return {"lots": len(lots), "deployed_usd": float(deployed),
            "first_fill_to_first_complement_s": (first_comp - t_first) if first_comp is not None else None,
            "released_at_complement_usd": float(released_at_complement),
            "released_at_complement_share": float(released_at_complement / deployed) if deployed else None,
            "released_at_settlement_usd": float(residual),
            "first_fill_to_settlement_s": (t_settle - t_first) if t_settle is not None else None,
            "capital_hours": float(cap_hours) if t_settle is not None or residual == 0 else None,
            "hours_per_deployed_dollar": float(cap_hours / deployed) if deployed and (t_settle is not None or residual == 0) else None,
            "turnover_per_capital_hour": float(deployed / cap_hours) if cap_hours > 0 else None}


def lots_with_match_times(ps: Sequence[dict], resolution: Optional[str], unit: D) -> List[dict]:
    """window_lots() pops first_match_t; recompute it here from a second
    FIFO pass so recycling can use it."""
    lots = ep.window_lots(ps, resolution, unit)
    # second pass: FIFO again but only to capture first match times
    open_lots: Dict[str, List[dict]] = {"YES": [], "NO": []}
    i = 0
    for p in ps:
        side, other = p["side"], ("NO" if p["side"] == "YES" else "YES")
        lot = {"open": p["qty"], "ref": lots[i], "t": p["t"]}
        i += 1
        remaining = p["qty"]
        for ol in open_lots[other]:
            if remaining <= 0:
                break
            if ol["open"] <= 0:
                continue
            m = min(remaining, ol["open"])
            for l in (lot, ol):
                if l["ref"].get("first_match_t") is None:
                    l["ref"]["first_match_t"] = p["t"]
            ol["open"] -= m
            remaining -= m
        lot["open"] = remaining
        open_lots[side].append(lot)
        open_lots[other] = [l for l in open_lots[other] if l["open"] > 0]
    for l in lots:
        l.setdefault("first_match_t", None)
    return lots


def l5_recycling(rows: Optional[Sequence[dict]] = None, path: str = cv.CANONICAL_PATH) -> dict:
    rows = list(rows) if rows is not None else cv.load_rows(path)
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    per: List[dict] = []
    lanes: Dict[str, List[dict]] = defaultdict(list)
    for wid, rs in by_win.items():
        ps = ep.prints(rs)
        if not ps:
            continue
        _f, resolution, unit = cv.window_fills(rs)
        last = max(ps, key=lambda p: p["t"])
        t_settle = last["t"] + max(0, last["tts"])
        lots = lots_with_match_times(ps, resolution, unit)
        m = market_recycling(lots, t_settle)
        m["lane"] = cv._lane({"asset": rs[-1].get("asset", ""), "duration_minutes": int(cv._f(rs[-1].get("duration_minutes")))})
        per.append(m)
        lanes[m["lane"]].append(m)

    def summ(ms: Sequence[dict]) -> dict:
        dep = sum(m["deployed_usd"] for m in ms)
        rel = sum(m["released_at_complement_usd"] for m in ms)
        ch = sum(m["capital_hours"] or 0 for m in ms)
        return {"windows": len(ms), "deployed_usd": round(dep, 2),
                "released_at_complement_share": round(rel / dep, 4) if dep else None,
                "first_fill_to_first_complement_s": _pct([m["first_fill_to_first_complement_s"] for m in ms]),
                "windows_never_completed_share": round(sum(1 for m in ms if m["first_fill_to_first_complement_s"] is None) / len(ms), 4) if ms else None,
                "first_fill_to_settlement_s": _pct([m["first_fill_to_settlement_s"] for m in ms]),
                "hours_per_deployed_dollar": _pct([m["hours_per_deployed_dollar"] for m in ms]),
                "capital_hours_total": round(ch, 2),
                "turnover_per_capital_hour_aggregate": round(dep / ch, 3) if ch else None}
    return {"overall": summ(per), "by_lane": {k: summ(v) for k, v in sorted(lanes.items())}}


def our_fills(r4_path: str = R4_ORDERS_PATH) -> Dict[str, dict]:
    """Our at-size markets as prints (YES-terms side/price, epoch seconds)."""
    from src.l5_r4_counterfactual import R3L_FILLS, R3L_RESOLUTION
    out: Dict[str, dict] = {}
    for slug, fills in R3L_FILLS.items():
        ps = []
        for t, s, p, q in fills:
            ts = datetime.fromisoformat("%sT%s+00:00" % (R3L_DAY, t)).timestamp()
            yes_px = D(str(p))                       # R3L_FILLS carries YES-terms prices
            px = yes_px if s == "YES" else D(1) - yes_px
            ps.append({"t": ts, "side": s, "px": px, "qty": D(str(q)), "usd": D(str(q)) * px, "tts": 0})
        out[slug] = {"prints": sorted(ps, key=lambda x: x["t"]), "resolution": R3L_RESOLUTION.get(slug), "cohort": "R3L"}
    for slug, fills in cv.r4_fills(r4_path).items():
        ps = []
        for t, s, yes_px, q in fills:
            ts = datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
            px = yes_px if s == "YES" else D(1) - yes_px      # own-side price
            ps.append({"t": ts, "side": s, "px": px, "qty": q, "usd": q * px, "tts": 0})
        out[slug] = {"prints": sorted(ps, key=lambda x: x["t"]), "resolution": cv.R4_RESOLUTION.get(slug), "cohort": "R4"}
    return out


def our_recycling(sb_path: str = SCOREBOARD_PATH, r4_path: str = R4_ORDERS_PATH) -> dict:
    settle: Dict[str, Optional[float]] = {}
    if os.path.exists(sb_path):
        for m in json.load(open(sb_path))["markets"]:
            settle[m["slug"]] = datetime.fromisoformat(m["resolution_utc"].replace("Z", "+00:00")).timestamp() if m.get("resolution_utc") else None
    per = {}
    for slug, v in our_fills(r4_path).items():
        ps = v["prints"]                          # own-side prices; window_lots pairs YES px + NO px
        lots = lots_with_match_times(ps, v["resolution"], D("2.50"))
        m = market_recycling(lots, settle.get(slug))
        m["cohort"] = v["cohort"]
        m["settlement_utc"] = datetime.fromtimestamp(settle[slug], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if settle.get(slug) else None
        per[slug] = m
    dep = sum(m["deployed_usd"] for m in per.values())
    rel = sum(m["released_at_complement_usd"] for m in per.values())
    ch = sum(m["capital_hours"] or 0 for m in per.values())
    return {"per_market": per,
            "overall": {"markets": len(per), "deployed_usd": round(dep, 2),
                        "released_at_complement_share": round(rel / dep, 4) if dep else None,
                        "first_fill_to_first_complement_s": _pct([m["first_fill_to_first_complement_s"] for m in per.values()]),
                        "first_fill_to_settlement_s": _pct([m["first_fill_to_settlement_s"] for m in per.values()]),
                        "hours_per_deployed_dollar": _pct([m["hours_per_deployed_dollar"] for m in per.values()]),
                        "capital_hours_total": round(ch, 2),
                        "turnover_per_capital_hour_aggregate": round(dep / ch, 3) if ch else None}}


def study() -> dict:
    l5 = l5_recycling()
    ours = our_recycling()
    o, u = l5["overall"], ours["overall"]
    reading = ("L5 releases %.0f%% of deployed dollars at the complement, first complement p50 %s s after the first fill "
               "(p90 %s s), and its capital sits %s h per deployed dollar (p50) because its windows settle in minutes "
               "(first fill to settlement p50 %s s). Ours: %.0f%% released at the complement, first complement p50 %s s "
               "(p90 %s s) -- the complement step itself is L5-like -- but %s h per deployed dollar (p50) because sports "
               "markets settle hours later (first fill to settlement p50 %s s) and the residual, plus the flat-account wait, "
               "sits until then. Recycling is bounded by market duration and the settlement wait, not by the pairing step."
               % (100 * (o["released_at_complement_share"] or 0), o["first_fill_to_first_complement_s"].get("p50"), o["first_fill_to_first_complement_s"].get("p90"),
                  o["hours_per_deployed_dollar"].get("p50"), o["first_fill_to_settlement_s"].get("p50"),
                  100 * (u["released_at_complement_share"] or 0), u["first_fill_to_first_complement_s"].get("p50"), u["first_fill_to_first_complement_s"].get("p90"),
                  u["hours_per_deployed_dollar"].get("p50"), u["first_fill_to_settlement_s"].get("p50")))
    return {"owner_direction": "#167 5570456323 (capital recycling)", "l5": l5, "ours": ours, "reading": reading,
            "labels": ("capital released at the complement for the matched part (venue nets pairs to cash) and at settlement for the "
                       "residual; L5 settlement = last print time + its seconds_to_settlement; ours from the scoreboard's resolution time; "
                       "our R4 fill times are the session row times (a first-fill-to-complement of 0 s means both filled inside one "
                       "~30 s session); read-only")}


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = study()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    print(out["reading"])
    print(json.dumps(out["l5"]["overall"], indent=1, default=str))
    print(json.dumps(out["ours"], indent=1, default=str)[:3500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
