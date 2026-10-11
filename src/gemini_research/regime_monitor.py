"""GEMINI BTC5M/BTC15M REGIME MONITOR. Read-only, offline over tapes.

WHAT IT IS FOR. Between 2026-08-30 and 2026-10-05 the Gemini BTC5m book
degraded by roughly 7x on the only quantity that matters for a maker policy:
the single-leg top-of-book spread. This monitor measures that quantity, per
window, on whatever tapes it is pointed at, and appends a small durable JSON
record per window so the series can be watched over time.

A RETURN TOWARD THE HISTORICAL ~2c REGIME IS A RESEARCH TRIGGER, NOT TRADING
AUTHORIZATION. Nothing this file emits authorizes an order, sizes a position,
or unlocks any venue behaviour. It emits distributions. A tightening book means
"re-open the BTC5m/BTC15M habitat question with fresh evidence", and the live
progression (historical replay -> out-of-sample replay -> realtime shadow ->
action-by-action comparison -> bounded live probe) still applies in full from
its first step. Nothing here is realized profit for our account.

PER WINDOW IT RECORDS
  * single_leg_spread  -- ask - bid, TOP OF BOOK, on the listed UP instrument.
    This is a ONE-LEG quantity in dollars.
  * depth              -- size at the top level and summed over the top 3
    levels, per side.
  * print_rate         -- venue trade prints per minute of observed window
    time, and total printed quantity.
  * complement_reach   -- the price at which the DERIVED complement leg can be
    bought, 1 - up_bid, and the resulting two-leg pair cost up_ask + (1 -
    up_bid). Its unit is "$ over 1.00" once 1.00 is subtracted.

THE UNIT TRAP THIS FILE REFUSES TO FALL INTO. analysis/gemini/updown/
STRUCTURAL_EXPANDED.json carries `spread_proxy_q1`, whose own declared unit is
"$ over 1.00" -- a TWO-LEG PAIR-COST EXCESS. It is NOT a single-leg spread and
the two must never be compared to each other. Every number this monitor emits
carries its unit in the record.

THRESHOLD-FREE BY DESIGN. It reports distributions (n, median, p10, p25, p75,
p90, fraction >= $0.20) and never declares a regime change. One window is one
window; a regime statement needs a series, and the series is what the appended
JSONL is for.

UNAVAILABLE is written wherever a tape cannot answer a question, rather than a
zero or an omission.
"""
from __future__ import annotations

import argparse
import glob as globmod
import gzip
import json
import math
import statistics as st
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LABEL = "GEMINI_REGIME_MONITOR_V1"
UNAVAILABLE = "UNAVAILABLE"
HISTORICAL_REGIME_SPREAD = 0.02
RESEARCH_TRIGGER_NOTE = (
    "A return toward the historical ~$0.02 single-leg regime is a RESEARCH "
    "TRIGGER, NOT TRADING AUTHORIZATION.")


def _open(p: str):
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p, encoding="utf-8")


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def dist(xs: List[float], unit: str) -> Dict[str, Any]:
    """A threshold-free distribution. No verdict, no comparison."""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0, "unit": unit, "median": UNAVAILABLE}
    def q(p: float) -> float:
        return xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"n": len(xs), "unit": unit,
            "min": round(xs[0], 4), "p10": round(q(0.10), 4),
            "p25": round(q(0.25), 4), "median": round(st.median(xs), 4),
            "p75": round(q(0.75), 4), "p90": round(q(0.90), 4),
            "max": round(xs[-1], 4),
            "frac_ge_0_20": round(sum(1 for x in xs if x >= 0.20) / len(xs), 4),
            "frac_le_0_02": round(sum(1 for x in xs if x <= 0.02) / len(xs), 4)}


class WindowAcc:
    def __init__(self, market: str) -> None:
        self.market = market
        self.spreads: List[float] = []
        self.d1_bid: List[float] = []
        self.d1_ask: List[float] = []
        self.d3_bid: List[float] = []
        self.d3_ask: List[float] = []
        self.pair_cost: List[float] = []
        self.complement_px: List[float] = []
        self.quotes = 0
        self.two_sided = 0
        self.one_sided = 0
        self.empty = 0
        self.prints = 0
        self.print_qty = 0.0
        self.t_min: Optional[float] = None
        self.t_max: Optional[float] = None

    def t(self, ts: Optional[float]) -> None:
        if ts is None:
            return
        self.t_min = ts if self.t_min is None else min(self.t_min, ts)
        self.t_max = ts if self.t_max is None else max(self.t_max, ts)

    def quote(self, bids: List[List[str]], asks: List[List[str]]) -> None:
        self.quotes += 1
        if bids and asks:
            self.two_sided += 1
        elif bids or asks:
            self.one_sided += 1
            return
        else:
            self.empty += 1
            return
        bp, ap = _f(bids[0][0]), _f(asks[0][0])
        if bp is None or ap is None:
            return
        self.spreads.append(ap - bp)
        self.complement_px.append(1.0 - bp)
        self.pair_cost.append(ap + (1.0 - bp))
        b1 = _f(bids[0][1]) if len(bids[0]) > 1 else None
        a1 = _f(asks[0][1]) if len(asks[0]) > 1 else None
        if b1 is not None:
            self.d1_bid.append(b1)
        if a1 is not None:
            self.d1_ask.append(a1)
        for rows, out in ((bids[:3], self.d3_bid), (asks[:3], self.d3_ask)):
            s = sum(x for x in (_f(r[1]) if len(r) > 1 else None
                                for r in rows) if x is not None)
            out.append(s)

    def record(self, source: str) -> Dict[str, Any]:
        span = ((self.t_max - self.t_min)
                if (self.t_min is not None and self.t_max is not None
                    and self.t_max > self.t_min) else None)
        return {
            "label": LABEL,
            "kind": "regime_window",
            "market": self.market,
            "duration": ("BTC15M" if "BTC15M" in self.market
                         else ("BTC05M" if "BTC05M" in self.market
                               else UNAVAILABLE)),
            "source": source,
            "observed_from_utc": (datetime.fromtimestamp(
                self.t_min, timezone.utc).isoformat() if self.t_min else
                UNAVAILABLE),
            "observed_to_utc": (datetime.fromtimestamp(
                self.t_max, timezone.utc).isoformat() if self.t_max else
                UNAVAILABLE),
            "observed_span_s": (round(span, 1) if span else UNAVAILABLE),
            "quote_observations": self.quotes,
            "book_state_counts": {"TWO_SIDED": self.two_sided,
                                  "ONE_SIDED": self.one_sided,
                                  "EMPTY": self.empty},
            "single_leg_spread": dist(self.spreads,
                                      "$ ask-bid, one leg, top of book"),
            "depth_top1_bid": dist(self.d1_bid, "shares at best bid"),
            "depth_top1_ask": dist(self.d1_ask, "shares at best ask"),
            "depth_top3_bid": dist(self.d3_bid, "shares, top 3 bid levels"),
            "depth_top3_ask": dist(self.d3_ask, "shares, top 3 ask levels"),
            "complement_reach_price": dist(
                self.complement_px,
                "$ to buy the DERIVED complement leg, 1 - up_bid"),
            "pair_cost_two_leg": dist(self.pair_cost,
                                      "$ total, two legs (1.00 = flat)"),
            "pair_cost_excess_over_1": dist(
                [x - 1.0 for x in self.pair_cost], "$ over 1.00, two legs"),
            "prints": self.prints,
            "printed_qty": round(self.print_qty, 2),
            "print_rate_per_min": (round(self.prints / (span / 60.0), 3)
                                   if span else UNAVAILABLE),
            "complement_is_derived": True,
            "derivation": ("the venue lists one instrument per window (-UP); "
                           "DOWN ask = 1 - UP bid, so the complement leg is "
                           "DERIVED, never a second feed"),
            "research_trigger_note": RESEARCH_TRIGGER_NOTE,
            "accounting": "public market data; no order; not realized profit",
        }


def scan(paths: List[str]) -> List[Dict[str, Any]]:
    """Accumulate per window over any mix of tape shapes we have taken."""
    acc: Dict[str, WindowAcc] = {}
    src: Dict[str, str] = {}
    for p in paths:
        shape = "unknown"
        with _open(p) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                k = r.get("kind")
                # shape A: flat bbo rows (data/transfer_backtest/*bbo*)
                if k is None and "bid" in r and "ask" in r:
                    shape = "flat_bbo"
                    m = str(r.get("symbol") or UNAVAILABLE)
                    a = acc.setdefault(m, WindowAcc(m))
                    a.t(_f(r.get("t")))
                    b = [[r.get("bid"), r.get("bid_sz")]] if r.get("bid") else []
                    s = [[r.get("ask"), r.get("ask_sz")]] if r.get("ask") else []
                    a.quote(b, s)
                    src.setdefault(m, "%s:%s" % (Path(p).name, shape))
                    continue
                # shape C: nested print tapes {symbol, rows:[{...}]}
                if k is None and isinstance(r.get("rows"), list):
                    m = str(r.get("symbol") or UNAVAILABLE)
                    a = acc.setdefault(m, WindowAcc(m))
                    for tr in r["rows"]:
                        if not isinstance(tr, dict):
                            continue
                        a.prints += 1
                        q = _f(tr.get("amount") or tr.get("qty"))
                        if q:
                            a.print_qty += q
                        tms = _f(tr.get("timestampms"))
                        a.t(tms / 1000.0 if tms else _f(tr.get("timestamp")))
                    src.setdefault(m, "%s:nested_prints" % Path(p).name)
                    continue
                # shape B: btc5m recorder tape
                if k == "p_snapshot":
                    shape = "recorder_p_snapshot"
                    m = str(r.get("market") or UNAVAILABLE)
                    a = acc.setdefault(m, WindowAcc(m))
                    tns = r.get("t_ns")
                    a.t(tns / 1e9 if tns else None)
                    a.quote(r.get("bids") or [], r.get("asks") or [])
                    src.setdefault(m, "%s:%s" % (Path(p).name, shape))
                elif k == "pair_snapshot":
                    shape = "btc15m_pair_snapshot"
                    m = str(r.get("market") or UNAVAILABLE)
                    a = acc.setdefault(m, WindowAcc(m))
                    tns = r.get("t_ns")
                    a.t(tns / 1e9 if tns else None)
                    yl = r.get("up_ladder_raw") or {}
                    a.quote(yl.get("bids") or [], yl.get("asks") or [])
                    src.setdefault(m, "%s:%s" % (Path(p).name, shape))
                elif k == "p_trade":
                    m = str(r.get("market") or UNAVAILABLE)
                    a = acc.setdefault(m, WindowAcc(m))
                    a.prints += 1
                    q = _f(r.get("qty"))
                    if q:
                        a.print_qty += q
                    tns = r.get("t_ns")
                    a.t(tns / 1e9 if tns else None)
    return [a.record(src.get(m, UNAVAILABLE)) for m, a in sorted(acc.items())]


def summarise(records: List[Dict[str, Any]], tag: str) -> Dict[str, Any]:
    """Pool the per-window records. Distributions only; no verdict."""
    out: Dict[str, Any] = {
        "label": LABEL, "kind": "regime_summary", "tag": tag,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "windows": len(records),
        "research_trigger_note": RESEARCH_TRIGGER_NOTE,
        "no_verdict": ("distributions only -- a regime statement needs a "
                       "series of windows, not one window, and this file "
                       "deliberately applies no threshold"),
        "unit_warning": ("single_leg_spread is '$ ask-bid, one leg'. "
                         "STRUCTURAL_EXPANDED.json's spread_proxy_q1 is "
                         "'$ over 1.00', a TWO-LEG pair-cost excess. Never "
                         "compare them."),
        "accounting": "public market data; no order; not realized profit",
    }
    for dur in ("BTC05M", "BTC15M", UNAVAILABLE):
        rs = [r for r in records if r.get("duration") == dur]
        if not rs:
            continue
        def pool(field: str) -> Dict[str, Any]:
            n = sum(r[field].get("n", 0) for r in rs)
            med = [r[field]["median"] for r in rs
                   if isinstance(r[field].get("median"), (int, float))]
            return {"observations": n, "windows_with_data": len(med),
                    "unit": rs[0][field].get("unit"),
                    "median_of_window_medians": (round(st.median(med), 4)
                                                 if med else UNAVAILABLE),
                    "window_median_spread_of_medians": (
                        dist(med, rs[0][field].get("unit", "")) if med
                        else UNAVAILABLE)}
        tmins = [r["observed_from_utc"] for r in rs
                 if r["observed_from_utc"] != UNAVAILABLE]
        tmaxs = [r["observed_to_utc"] for r in rs
                 if r["observed_to_utc"] != UNAVAILABLE]
        prints = sum(r.get("prints", 0) for r in rs)
        rates = [r["print_rate_per_min"] for r in rs
                 if isinstance(r.get("print_rate_per_min"), (int, float))]
        out[dur] = {
            "windows": len(rs),
            "date_range_utc": ([min(tmins), max(tmaxs)]
                               if tmins and tmaxs else UNAVAILABLE),
            "single_leg_spread": pool("single_leg_spread"),
            "depth_top1_bid": pool("depth_top1_bid"),
            "depth_top1_ask": pool("depth_top1_ask"),
            "depth_top3_bid": pool("depth_top3_bid"),
            "complement_reach_price": pool("complement_reach_price"),
            "pair_cost_excess_over_1": pool("pair_cost_excess_over_1"),
            "prints_total": prints,
            "print_rate_per_min": (dist(rates, "prints/min") if rates
                                   else UNAVAILABLE),
            "complement_leg": ("DERIVED from the UP book; no separate DOWN "
                               "instrument has ever appeared on any tape "
                               "this programme has taken"),
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", nargs="+", required=True,
                    help="globs over tapes (flat bbo, btc5m recorder, or "
                         "btc15m pair_snapshot)")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--append",
                    default="analysis/gemini/regime_monitor/windows.jsonl")
    ap.add_argument("--summary-out",
                    default="analysis/gemini/regime_monitor/REGIME_MONITOR.json")
    a = ap.parse_args(argv)
    paths: List[str] = []
    for g in a.tapes:
        paths += sorted(globmod.glob(g))
    if not paths:
        print(json.dumps({"error": "no tapes matched", "globs": a.tapes}))
        return 1
    records = scan(paths)
    for r in records:
        r["tag"] = a.tag
    ap_path = Path(a.append)
    ap_path.parent.mkdir(parents=True, exist_ok=True)
    with ap_path.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, separators=(",", ":"), default=str) + "\n")
    s = summarise(records, a.tag)
    s["tapes"] = [str(p) for p in paths]
    so = Path(a.summary_out)
    so.parent.mkdir(parents=True, exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if so.exists():
        try:
            prev = json.loads(so.read_text(encoding="utf-8"))
            existing = prev if isinstance(prev, list) else [prev]
        except Exception:                                      # noqa: BLE001
            existing = []
    existing = [e for e in existing if e.get("tag") != a.tag] + [s]
    so.write_text(json.dumps(existing, indent=2, sort_keys=True, default=str)
                  + "\n", encoding="utf-8")
    print(json.dumps({"tag": a.tag, "tapes": len(paths),
                      "windows": len(records),
                      "appended": str(ap_path),
                      "summary": str(so)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
