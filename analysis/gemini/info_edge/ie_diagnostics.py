"""Permanent per-observation diagnostics (item 5).

Emits, for every in-window Gemini book observation:
  TTE, api_bid, api_ask, api_spread, api_bid_sz, api_ask_sz,
  coinbase, coinbase_distance_to_strike,
  index_distance_to_strike   (see the HONEST LIMIT below),
  coinbase_minus_index       (computable only at the index anchor points)

HONEST LIMIT ON THE INDEX -- do not paper over this.
The settlement index is Lukka LS-LRRR-XBTUSD ({"agency":"lukka"} in the event
payload). No public continuous feed for it was found: every probed Gemini
endpoint 404s and api.lukka.tech is not public. We therefore DO NOT have a
continuous index series, and this module does not pretend to one.

What we DO capture is TWO EXACT INDEX ANCHOR POINTS per window:
    strike            = the index value at window OPEN
    settlement_value  = the index value at window CLOSE
So `index_distance_to_strike` is exactly 0 at open and
(settlement_value - strike) at close, and is UNKNOWN in between. Any
in-between value would be interpolation, which is not recorded.

`coinbase_minus_index` is therefore reported at the two anchors only. That is
still the quantity of interest: it measures whether Coinbase sits above or
below the settlement index, and how that basis behaves across the window.

COINBASE REMAINS LABELLED AN EXTERNAL PREDICTIVE FEED. It is NOT the
settlement index and is never substituted for it.
"""
from __future__ import annotations

import bisect
import json
import re
from collections import defaultdict
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")


def build(outdir="data/gemini_infoedge"):
    settled, expiry, cb = {}, {}, []
    book = defaultdict(list)
    for f in sorted(Path(outdir).glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                if r.get("resolution_side"):
                    settled[r["ticker"]] = r
            elif k == "cb_match":
                cb.append((r["t_ns"] / 1e9, float(r["price"])))
            elif k in ("window", "window_poll"):
                m = SYM.match(r.get("market") or "")
                if m and r.get("expiry"):
                    from datetime import datetime, timezone
                    expiry[m.group(1)] = datetime.fromisoformat(
                        r["expiry"].replace("Z", "+00:00")).replace(
                        tzinfo=timezone.utc).timestamp()
            elif k == "p_snapshot":
                m = SYM.match(r.get("market") or "")
                if m:
                    book[m.group(1)].append(r)
    cb.sort()
    cbt = [x[0] for x in cb]

    def cb_at(t):
        i = bisect.bisect_right(cbt, t) - 1
        return cb[i][1] if i >= 0 else None

    out = []
    for tk, s in settled.items():
        exp = expiry.get(tk)
        if exp is None:
            continue
        try:
            strike = float(s["strike"])
            settle = float(s["settlement_value"])
        except Exception:
            continue
        start = exp - 300.0
        cb_open, cb_close = cb_at(start), cb_at(exp)
        anchors = {
            "index_at_open": strike,
            "index_at_close": settle,
            "index_distance_to_strike_at_close": round(settle - strike, 4),
            "coinbase_at_open": cb_open,
            "coinbase_at_close": cb_close,
            "coinbase_minus_index_at_open": (round(cb_open - strike, 4)
                                             if cb_open else None),
            "coinbase_minus_index_at_close": (round(cb_close - settle, 4)
                                              if cb_close else None),
        }
        for r in sorted(book.get(tk, []), key=lambda x: x["t_ns"]):
            t = r["t_ns"] / 1e9
            if not (start <= t <= exp):
                continue
            b = r.get("bids") or []
            a = r.get("asks") or []
            bid = float(b[0][0]) if b else None
            ask = float(a[0][0]) if a else None
            c = cb_at(t)
            out.append({
                "ticker": tk, "t": t, "TTE": round(exp - t, 3),
                "api_bid": bid, "api_bid_sz": float(b[0][1]) if b else None,
                "api_ask": ask, "api_ask_sz": float(a[0][1]) if a else None,
                "api_spread": (round(ask - bid, 4)
                               if (bid is not None and ask is not None) else None),
                "up_executable": ask is not None,
                "up_cost": ask,
                "down_cost": (round(1 - bid, 4) if bid is not None else None),
                "coinbase": c,
                "coinbase_distance_to_strike": (round(c - strike, 4) if c else None),
                "index_distance_to_strike": None,   # UNKNOWN mid-window, never interpolated
                "resolution_side": s.get("resolution_side"),
                "source": s.get("source"),
                **anchors,
            })
    return out


def main():
    rows = build()
    Path("analysis/gemini/info_edge/ie_diagnostics.json").write_text(
        json.dumps(rows[:50000], indent=1))
    print(f"diagnostic rows: {len(rows)} over "
          f"{len({r['ticker'] for r in rows})} settled windows")
    anc = {r["ticker"]: r for r in rows}
    bo = [r["coinbase_minus_index_at_open"] for r in anc.values()
          if r["coinbase_minus_index_at_open"] is not None]
    bc = [r["coinbase_minus_index_at_close"] for r in anc.values()
          if r["coinbase_minus_index_at_close"] is not None]
    if bo:
        bo.sort(); print(f"coinbase - index AT OPEN  : p10={bo[len(bo)//10]:+.2f} "
                         f"p50={bo[len(bo)//2]:+.2f} p90={bo[-max(1,len(bo)//10)]:+.2f} n={len(bo)}")
    if bc:
        bc.sort(); print(f"coinbase - index AT CLOSE : p10={bc[len(bc)//10]:+.2f} "
                         f"p50={bc[len(bc)//2]:+.2f} p90={bc[-max(1,len(bc)//10)]:+.2f} n={len(bc)}")
    print("\nNOTE: index_distance_to_strike is UNKNOWN mid-window by construction;")
    print("only the open and close anchors exist. No interpolation is recorded.")


if __name__ == "__main__":
    main()
