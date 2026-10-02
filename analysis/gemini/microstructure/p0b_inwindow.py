"""P0b: in-window flow and the "static book despite executions" test.

The prior canonical study established that most observations in these tapes
sit OUTSIDE the window they belong to (median in-window coverage 0 s, median
max gap 875.8 s) because the old collector subscribed on the events feed's
"active" flag and held subscriptions across bounded sessions. Any statement
about displayed depth or book activity is therefore only meaningful on
observations STRICTLY INSIDE [window_start, expiry].

Window bounds come from the `vol` rows' own `expiry` field (venue metadata),
never from parsing a symbol.

Reports, per in-window-covered BTC5m window:
  - executions (count, contracts, maker direction) from the WS trade stream
  - venue cumulative volume over the same interval
  - displayed BBO: distinct states, change rate, spread, min-side depth
  - the STATIC-BOOK test: windows where the displayed top-of-book barely
    changes while executions are landing.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MARKET_RE = re.compile(r"^GEMI-((?:BTC|ETH)(?:05|15)M\d{10})-(UP|DOWN)$")
BARE_RE = re.compile(r"^((?:BTC|ETH)(?:05|15)M\d{10})$")


def key_of(row):
    m = row.get("market") or ""
    h = MARKET_RE.match(m)
    if h:
        return h.group(1)
    h = BARE_RE.match(m)
    return h.group(1) if h else None


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def main(tape_dir, asset="BTC", dur="05M", min_cov_s=240.0):
    dur_s = 300.0 if dur == "05M" else 900.0
    tapes = sorted(Path(tape_dir).glob("tape_*.jsonl"))

    expiry = {}
    vol = defaultdict(list)
    prints = defaultdict(list)
    bbo = defaultdict(list)

    for p in tapes:
        for line in open(p):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("venue") != "gemini":
                continue
            k = key_of(r)
            if not k or not k.startswith(asset) or k[3:6] != dur:
                continue
            kind, t = r.get("kind"), r.get("t")
            if kind == "vol":
                e = r.get("expiry")
                if e and k not in expiry:
                    expiry[k] = datetime.fromisoformat(
                        e.replace("Z", "+00:00")).replace(
                        tzinfo=timezone.utc).timestamp()
                try:
                    vol[k].append((t, float(r["volume"])))
                except Exception:
                    pass
            elif kind == "print":
                try:
                    prints[k].append((t, float(r["qty"]), float(r["price"]),
                                      r.get("buyer_is_maker"), r.get("trade_id")))
                except Exception:
                    pass
            elif kind == "bbo":
                b, a = r.get("bids") or [], r.get("asks") or []
                bbo[k].append((
                    t,
                    float(b[0][0]) if b else None, float(b[0][1]) if b else None,
                    float(a[0][0]) if a else None, float(a[0][1]) if a else None,
                ))

    rows = []
    for k, exp in expiry.items():
        start = exp - dur_s
        inw = lambda t: t is not None and start <= t <= exp

        ob = sorted([o for o in bbo.get(k, []) if inw(o[0])])
        cov = (ob[-1][0] - ob[0][0]) if len(ob) > 1 else 0.0

        seen, pr = set(), []
        for t, qty, price, bim, tid in prints.get(k, []):
            if not inw(t):
                continue
            if tid is not None and tid in seen:
                continue
            if tid is not None:
                seen.add(tid)
            pr.append((t, qty, price, bim))

        vs = sorted([v for v in vol.get(k, []) if inw(v[0])])
        vol_in = (vs[-1][1] - vs[0][1]) if len(vs) > 1 else None
        vol_final = max((v for _, v in sorted(vol.get(k, []))), default=None)

        states = {(b, bs, a, asz) for _, b, bs, a, asz in ob}
        changes = sum(1 for x, y in zip(ob, ob[1:]) if x[1:] != y[1:])
        two = [o for o in ob if o[1] is not None and o[3] is not None]
        spreads = [o[3] - o[1] for o in two]
        depths = [min(o[2], o[4]) for o in two if o[2] and o[4]]

        contracts = sum(q for _, q, _, _ in pr)
        rows.append({
            "window": k,
            "expiry_utc": datetime.fromtimestamp(exp, timezone.utc).isoformat(),
            "inwindow_cov_s": round(cov, 1),
            "bbo_obs_in": len(ob),
            "bbo_two_sided_in": len(two),
            "bbo_distinct_states_in": len(states),
            "bbo_changes_in": changes,
            "bbo_change_per_min": round(changes / (cov / 60.0), 2) if cov > 0 else None,
            "spread_p50": round(pct(spreads, 0.5), 4) if spreads else None,
            "depth_min_side_p50": round(pct(depths, 0.5), 2) if depths else None,
            "ws_prints_in": len(pr),
            "ws_contracts_in": round(contracts, 2),
            "ws_trades_per_min": round(len(pr) / (dur_s / 60.0), 2),
            "ws_contracts_per_min": round(contracts / (dur_s / 60.0), 2),
            "buyer_is_maker_true": sum(1 for _, _, _, b in pr if b is True),
            "buyer_is_maker_false": sum(1 for _, _, _, b in pr if b is False),
            "venue_vol_delta_in": round(vol_in, 2) if vol_in is not None else None,
            "venue_vol_final": vol_final,
        })

    rows.sort(key=lambda r: r["expiry_utc"])
    out = Path(__file__).with_name(f"p0b_inwindow_{asset}{dur}.json")
    out.write_text(json.dumps(rows, indent=1))

    covered = [r for r in rows if r["inwindow_cov_s"] >= min_cov_s]
    traded = [r for r in covered if r["ws_prints_in"] > 0]
    print(f"{asset}{dur}: windows with venue expiry metadata : {len(rows)}")
    print(f"  in-window coverage >= {int(min_cov_s)} s            : {len(covered)}")
    print(f"  of those, with >=1 captured execution  : {len(traded)}")
    print()
    if covered:
        print("ON WELL-COVERED WINDOWS (the only defensible denominator):")
        for f in ("ws_prints_in", "ws_contracts_in", "ws_trades_per_min",
                  "ws_contracts_per_min", "venue_vol_delta_in",
                  "bbo_change_per_min", "spread_p50", "depth_min_side_p50",
                  "bbo_distinct_states_in", "bbo_obs_in"):
            v = [r[f] for r in covered if r[f] is not None]
            print(f"  {f:24s} p10={pct(v,0.1)} p50={pct(v,0.5)} "
                  f"p90={pct(v,0.9)} max={max(v) if v else None}")
        print()
        tc = sum(r["ws_contracts_in"] for r in covered)
        tp = sum(r["ws_prints_in"] for r in covered)
        print(f"  TOTAL in-window executions : {tp} prints / {round(tc,2)} contracts")
        bm_t = sum(r["buyer_is_maker_true"] for r in covered)
        bm_f = sum(r["buyer_is_maker_false"] for r in covered)
        print(f"  buyer_is_maker true/false  : {bm_t} / {bm_f}")
        print()
        print("STATIC-BOOK TEST (executions land while displayed BBO barely moves):")
        for r in sorted(traded, key=lambda r: -r["ws_contracts_in"])[:12]:
            print(f"  {r['window']} cov={r['inwindow_cov_s']:5.0f}s "
                  f"obs={r['bbo_obs_in']:3d} states={r['bbo_distinct_states_in']:3d} "
                  f"chg/min={r['bbo_change_per_min']} | "
                  f"prints={r['ws_prints_in']:3d} qty={r['ws_contracts_in']:9.2f} "
                  f"venue_delta={r['venue_vol_delta_in']} "
                  f"depth={r['depth_min_side_p50']} spread={r['spread_p50']}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/policy_d_port",
                  sys.argv[2] if len(sys.argv) > 2 else "BTC",
                  sys.argv[3] if len(sys.argv) > 3 else "05M"))
