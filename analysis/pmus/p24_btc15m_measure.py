"""P24 -- measure PMUS BTC15m from the public tape, and only from it.

PMUS launched `btc-updown-15m` on 2026-09-22. It is the same instrument our
strongest L5 behavioural corpus is built on, so the question is not whether it
LOOKS like L5's habitat but whether its flow, spread, depth and two-sidedness
can actually carry an L5-style inventory recycler.

WHAT THE PUBLIC GATEWAY GIVES, AND WHAT IT WITHHOLDS. There is no public trade
tape: `/v1/markets/{slug}/trades` is a 404. What exists is `sharesTraded`, a
monotone per-market cumulative counter, and the book. So:

  * CONTRACTS traded per window are OBSERVED exactly, from the counter's value
    when the window closes -- each market lives exactly one window, so the
    final value IS the window's volume.
  * TRADE COUNT is NOT observable. A poll interval sees a burst, not a print.
    What this module reports is `print_bursts` -- polls at which the counter
    moved -- which is a LOWER BOUND on trades and is labelled as one. It is
    never called a trade count.
  * NOTIONAL is DERIVED: each burst's contracts are valued at the mid in force
    at that poll. That is a VWAP approximation, not the venue's own number.

The authenticated WS carries the real trade tape (`src/pmus_ws_evidence_
recorder.py`), and this container holds no PMUS credentials, so the bound
stands rather than being wished away.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import statistics as st
import sys
from typing import Any, Dict, List, Optional

#: the sports entry band the PMUS executor enforces today
SPORTS_BAND = (0.20, 0.80)


def load(pattern: str) -> List[dict]:
    rows: List[dict] = []
    for f in sorted(glob.glob(pattern)):
        with open(f) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def f(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pct(a: List[float], q: float) -> Optional[float]:
    if not a:
        return None
    b = sorted(a)
    return b[min(len(b) - 1, int(q * len(b)))]


def per_window(rows: List[dict]) -> Dict[str, dict]:
    """Everything the BBO stream says about each window it observed."""
    by = collections.defaultdict(list)
    for r in rows:
        if r.get("kind") == "bbo" and r.get("slug"):
            by[r["slug"]].append(r)
    out: Dict[str, dict] = {}
    for slug, rs in by.items():
        rs.sort(key=lambda r: r["t"])
        bursts: List[dict] = []
        prev_sh = None
        prev_t = None
        gaps: List[float] = []
        for r in rs:
            sh = f(r.get("shares_traded"))
            bid, ask = f(r.get("bid")), f(r.get("ask"))
            mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) \
                else (f(r.get("last")) if r.get("last") else None)
            if prev_sh is not None and sh is not None and sh > prev_sh:
                bursts.append({"t": r["t"], "contracts": sh - prev_sh,
                               "mid": mid, "bid": bid, "ask": ask})
                if prev_t is not None:
                    gaps.append(_dt(prev_t, r["t"]))
                prev_t = r["t"]
            if sh is not None:
                prev_sh = sh
        two = [r for r in rs if r.get("two_sided")]
        spreads = [round(f(r["ask"]) - f(r["bid"]), 4)
                   for r in rs if r.get("bid") and r.get("ask")]
        mids = [(f(r["ask"]) + f(r["bid"])) / 2.0
                for r in rs if r.get("bid") and r.get("ask")]
        bidq = [f(r.get("bid_shares")) for r in rs
                if f(r.get("bid_shares")) is not None]
        askq = [f(r.get("ask_shares")) for r in rs
                if f(r.get("ask_shares")) is not None]
        contracts = sum(b["contracts"] for b in bursts)
        notional = sum(b["contracts"] * (b["mid"] or 0.0) for b in bursts)
        out[slug] = {
            "samples": len(rs),
            "first_t": rs[0]["t"], "last_t": rs[-1]["t"],
            "observed_contracts": round(contracts, 2),
            "derived_notional_usd": round(notional, 2),
            "print_bursts_LOWER_BOUND": len(bursts),
            "two_sided_frac": round(len(two) / len(rs), 4) if rs else None,
            "spread_median": pct(spreads, 0.5),
            "spread_p90": pct(spreads, 0.9),
            "mid_median": pct(mids, 0.5),
            "mid_p10": pct(mids, 0.1), "mid_p90": pct(mids, 0.9),
            "in_sports_band_frac": round(
                sum(1 for m in mids if SPORTS_BAND[0] <= m <= SPORTS_BAND[1])
                / len(mids), 4) if mids else None,
            "bid_shares_median": pct(bidq, 0.5),
            "ask_shares_median": pct(askq, 0.5),
            "burst_gap_median_s": round(st.median(gaps), 2) if gaps else None,
            "burst_gap_max_s": round(max(gaps), 2) if gaps else None,
            "thirds_contracts": _thirds(bursts),
        }
    return out


def _dt(a: str, b: str) -> float:
    import datetime as dt
    pa = dt.datetime.fromisoformat(a.replace("Z", "+00:00"))
    pb = dt.datetime.fromisoformat(b.replace("Z", "+00:00"))
    return (pb - pa).total_seconds()


def _thirds(bursts: List[dict]) -> List[float]:
    """Where in the observed span the volume actually arrives. An L5-style
    recycler needs the opposite side to keep interacting late enough to repair
    inventory, so a window whose flow all lands in the first third is a
    different animal from one that trades throughout."""
    if not bursts:
        return [0.0, 0.0, 0.0]
    t0, t1 = bursts[0]["t"], bursts[-1]["t"]
    span = max(_dt(t0, t1), 1e-9)
    out = [0.0, 0.0, 0.0]
    for b in bursts:
        k = min(2, int(3.0 * _dt(t0, b["t"]) / span))
        out[k] += b["contracts"]
    return [round(x, 1) for x in out]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", default="data/pmus_btc15m/*.jsonl")
    a = ap.parse_args(argv)
    rows = load(a.tape)
    w = per_window(rows)
    print("PMUS BTC15m -- from the public book tape, %d rows, %d windows\n"
          % (len(rows), len(w)))
    for slug in sorted(w):
        d = w[slug]
        print(slug)
        print("  observed contracts   %-10s   derived notional  $%s"
              % (d["observed_contracts"], d["derived_notional_usd"]))
        print("  print bursts (LOWER BOUND on trades) %-5s  median gap %ss  max %ss"
              % (d["print_bursts_LOWER_BOUND"], d["burst_gap_median_s"],
                 d["burst_gap_max_s"]))
        print("  two-sided %-7s spread med %-6s p90 %-6s  mid med %-6s "
              "[p10 %s p90 %s]"
              % (d["two_sided_frac"], d["spread_median"], d["spread_p90"],
                 d["mid_median"], d["mid_p10"], d["mid_p90"]))
        print("  depth med  bid %-9s ask %-9s   in sports band 0.20-0.80: %s"
              % (d["bid_shares_median"], d["ask_shares_median"],
                 d["in_sports_band_frac"]))
        print("  volume by third of observed span: %s" % (d["thirds_contracts"],))
        print("  samples %d  %s -> %s" % (d["samples"], d["first_t"][11:19],
                                          d["last_t"][11:19]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
