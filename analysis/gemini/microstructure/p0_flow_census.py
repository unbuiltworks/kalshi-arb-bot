"""P0: Is Gemini BTC5m actually busy?

Measures native/authenticated market data only. Two INDEPENDENT instruments
per window, deliberately never merged:

  (A) VENUE-REPORTED volume -- `kind:"vol"` rows, the cumulative traded
      volume the Gemini events REST endpoint publishes per window, polled
      every ~20 s. Independent of our websocket coverage.

  (B) WS-CAPTURED prints -- `kind:"print"` rows from the public
      "<symbol>@trade" stream: individual executions with trade_id, qty and
      buyer_is_maker. Complete ONLY while we were actually subscribed.

The ratio B/A is our CAPTURE COMPLETENESS. It is the quantity that decides
whether "Gemini looks quiet" is a statement about the venue or about us.

Nothing here is forward-filled. A gap stays a gap.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

WINDOW_RE = re.compile(r"^(BTC|ETH)(05|15)M(\d{10})$")
# bbo/print markets arrive as GEMI-BTC05M2608310240-UP; vol rows as BTC05M2608310240
MARKET_RE = re.compile(r"^GEMI-((?:BTC|ETH)(?:05|15)M\d{10})-(UP|DOWN)$")


def canon(row):
    """Return the bare window key (e.g. BTC05M2608310240) or None."""
    m = row.get("market") or ""
    hit = MARKET_RE.match(m)
    if hit:
        return hit.group(1)
    if WINDOW_RE.match(m):
        return m
    return None


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def load(tapes):
    """Per-window accumulation. asset/duration kept so BTC5m can be isolated."""
    w = defaultdict(lambda: {
        "vol_series": [],      # (t, cumulative_volume)
        "prints": [],          # (t, qty, price, buyer_is_maker, trade_id)
        "bbo_t": [],           # observation timestamps
        "bbo": [],             # (t, bid, ask, bid_sz, ask_sz)
        "expiry_t": None,
        "src_files": set(),
    })
    for path in tapes:
        name = Path(path).name
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("venue") != "gemini":
                    continue
                kind = r.get("kind")
                if kind not in ("vol", "print", "bbo"):
                    continue
                key = canon(r)
                if not key:
                    continue
                rec = w[key]
                rec["src_files"].add(name)
                t = r.get("t")
                if kind == "vol":
                    try:
                        rec["vol_series"].append((t, float(r["volume"])))
                    except Exception:
                        pass
                elif kind == "print":
                    try:
                        rec["prints"].append((
                            t, float(r["qty"]), float(r["price"]),
                            r.get("buyer_is_maker"), r.get("trade_id"),
                        ))
                    except Exception:
                        pass
                else:
                    bids, asks = r.get("bids") or [], r.get("asks") or []
                    bid = float(bids[0][0]) if bids else None
                    bsz = float(bids[0][1]) if bids else None
                    ask = float(asks[0][0]) if asks else None
                    asz = float(asks[0][1]) if asks else None
                    rec["bbo_t"].append(t)
                    rec["bbo"].append((t, bid, ask, bsz, asz))
    return w


def window_start(key, expiry_t):
    dur = 300.0 if key[3:6] == "05M" else 900.0
    return (expiry_t - dur) if expiry_t else None


def main(tape_dir, asset="BTC", duration="05M"):
    tapes = sorted(Path(tape_dir).glob("tape_*.jsonl"))
    if not tapes:
        print("no tapes found", file=sys.stderr)
        return 1
    w = load([str(p) for p in tapes])

    rows = []
    for key, rec in w.items():
        if not key.startswith(asset) or key[3:6] != duration:
            continue
        # expiry from the window key: YYMMDDHHMM -> use vol row expiry when present
        vol = sorted(rec["vol_series"])
        # venue-reported traded volume for the window = max cumulative seen
        venue_vol = max((v for _, v in vol), default=None)
        venue_first = vol[0][1] if vol else None
        # WS capture
        seen = set()
        contracts = 0.0
        maker_buy = 0
        n_print = 0
        for t, qty, price, bim, tid in rec["prints"]:
            if tid is not None:
                if tid in seen:
                    continue
                seen.add(tid)
            n_print += 1
            contracts += qty
            if bim is True:
                maker_buy += 1
        # BBO cadence
        ts = sorted(rec["bbo_t"])
        gaps = [b - a for a, b in zip(ts, ts[1:])] if len(ts) > 1 else []
        span = (ts[-1] - ts[0]) if len(ts) > 1 else 0.0
        two_sided = sum(1 for _, b, a, _, _ in rec["bbo"] if b is not None and a is not None)
        spreads = [a - b for _, b, a, _, _ in rec["bbo"] if b is not None and a is not None]
        depths = [min(bs, az) for _, b, a, bs, az in rec["bbo"]
                  if b is not None and a is not None and bs and az]
        # distinct BBO states -- how much the 1 Hz sample actually changes
        states = {(b, a, bs, az) for _, b, a, bs, az in rec["bbo"]}

        rows.append({
            "window": key,
            "vol_polls": len(vol),
            "venue_volume": venue_vol,
            "venue_volume_first_poll": venue_first,
            "ws_prints": n_print,
            "ws_contracts": round(contracts, 2),
            "ws_buyer_is_maker": maker_buy,
            "capture_ratio": (round(contracts / venue_vol, 4)
                              if venue_vol and venue_vol > 0 else None),
            "bbo_obs": len(ts),
            "bbo_two_sided": two_sided,
            "bbo_distinct_states": len(states),
            "bbo_span_s": round(span, 1),
            "bbo_obs_per_min": round(len(ts) / (span / 60.0), 2) if span > 0 else None,
            "bbo_gap_p50": round(pct(gaps, 0.5), 2) if gaps else None,
            "bbo_gap_max": round(max(gaps), 2) if gaps else None,
            "spread_p50": round(pct(spreads, 0.5), 4) if spreads else None,
            "depth_min_side_p50": round(pct(depths, 0.5), 2) if depths else None,
            "src_files": len(rec["src_files"]),
        })

    rows.sort(key=lambda r: r["window"])
    out = Path(__file__).with_name(f"p0_flow_census_{asset}{duration}.json")
    out.write_text(json.dumps(rows, indent=1))

    # ---- summary ----
    traded = [r for r in rows if (r["venue_volume"] or 0) > 0]
    covered = [r for r in traded if r["capture_ratio"] is not None]
    print(f"tapes                       : {len(tapes)}")
    print(f"{asset}{duration} windows seen        : {len(rows)}")
    print(f"  with a vol poll           : {sum(1 for r in rows if r['vol_polls'])}")
    print(f"  venue_volume > 0          : {len(traded)}")
    print(f"  venue_volume == 0         : {sum(1 for r in rows if r['venue_volume'] == 0)}")
    print()
    print("VENUE-REPORTED volume per traded window (contracts):")
    for q in (0.10, 0.25, 0.50, 0.75, 0.90, 1.0):
        v = pct([r["venue_volume"] for r in traded], q)
        print(f"  p{int(q*100):3d} : {v}")
    tot_venue = sum(r["venue_volume"] or 0 for r in traded)
    tot_ws = sum(r["ws_contracts"] for r in traded)
    print(f"  total venue volume        : {round(tot_venue,2)}")
    print(f"  total WS-captured qty     : {round(tot_ws,2)}")
    print(f"  AGGREGATE CAPTURE RATIO   : "
          f"{round(tot_ws/tot_venue,4) if tot_venue else None}")
    print()
    print("CAPTURE RATIO per traded window:")
    for q in (0.10, 0.25, 0.50, 0.75, 0.90):
        print(f"  p{int(q*100):3d} : {pct([r['capture_ratio'] for r in covered], q)}")
    print()
    print("BBO sampling (1 Hz collector):")
    for f in ("bbo_obs_per_min", "bbo_gap_p50", "bbo_gap_max",
              "spread_p50", "depth_min_side_p50"):
        vals = [r[f] for r in rows if r[f] is not None]
        print(f"  {f:22s} p50={pct(vals,0.5)}  p90={pct(vals,0.9)}")
    ch = [(r["bbo_distinct_states"], r["bbo_obs"]) for r in rows if r["bbo_obs"] > 1]
    if ch:
        print(f"  distinct BBO states / obs p50 : "
              f"{pct([a/b for a,b in ch],0.5):.3f}")
    print()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "data/policy_d_port"
    a = sys.argv[2] if len(sys.argv) > 2 else "BTC"
    du = sys.argv[3] if len(sys.argv) > 3 else "05M"
    sys.exit(main(d, a, du))
