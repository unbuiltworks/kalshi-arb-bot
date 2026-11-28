"""Gemini 5m / 15m through the IDENTICAL habitat score used for PMUS.

Nothing here is tuned for Gemini. The candidate observations are built into
the same {t, price, two_sided, spread_ticks, tte_s, depth_at_touch} shape that
`run.py` builds for PMUS, handed to the same
`l5_habitat_score.window_features`, and scored against the same L5 reference
written by `run.py`. A venue-specific feature, threshold or weighting would
make the cross-venue comparison meaningless, so there is none.

The price series is the BBO MID, exactly as the PMUS side. That is a
like-in-kind comparison to L5's trade-price series and not an identical one,
and the label travels with every row.
"""
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src import l5_habitat_score as hs                      # noqa: E402

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REF = os.path.join(HERE, "l5_reference.json")
TAPES = {"gemini_5m": "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz",
         "gemini_15m": "data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz"}

# Gemini quotes to 4 decimals but the traded grid is cents, same as PMUS.
# It matters only for the UNCALIBRATED spread diagnostic: no CALIBRATED
# feature consumes spread_ticks, so this choice cannot move a score.
TICK = 0.01
MIN_SPAN_S = 60.0   # a 5m window cannot supply 120 s of margin


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def expiry_utc(symbol: str):
    """GEMI-BTC05M2608302330-UP -> 2026-08-30T23:30Z.

    The window's own name carries its expiry, so TTE is measurable here
    rather than UNKNOWN as it is for the PMUS XCHECK rows.
    """
    try:
        core = symbol.split("-")[1]
        digits = "".join(ch for ch in core if ch.isdigit())
        stamp = digits[-10:]
        if len(stamp) != 10:
            return None
        yy, mm, dd, hh, mi = (int(stamp[0:2]), int(stamp[2:4]),
                              int(stamp[4:6]), int(stamp[6:8]), int(stamp[8:10]))
        return datetime(2000 + yy, mm, dd, hh, mi,
                        tzinfo=timezone.utc).timestamp()
    except Exception:                                        # noqa: BLE001
        return None


def duration_s(symbol: str):
    """A 5m window is 300 s of market and a 15m window 900 s. Anything the
    tape carries for that symbol OUTSIDE its own window is a pre-open or
    post-expiry placeholder quote, not habitat."""
    core = symbol.split("-")[1] if "-" in symbol else symbol
    if "05M" in core:
        return 300.0
    if "15M" in core:
        return 900.0
    return None


def windows(path):
    obs, no_expiry = defaultdict(list), 0
    out_of_window = {}
    with gzip.open(os.path.join(ROOT, path), "rt") as fh:
        for line in fh:
            try:
                j = json.loads(line)
            except Exception:                                # noqa: BLE001
                continue
            sym, t = j.get("symbol"), _f(j.get("t"))
            bid, ask = _f(j.get("bid")), _f(j.get("ask"))
            if not sym or t is None or bid is None or ask is None:
                continue
            exp = expiry_utc(sym)
            if exp is None:
                no_expiry += 1
            bsz, asz = _f(j.get("bid_sz")), _f(j.get("ask_sz"))
            depth = None if (bsz is None or asz is None) else min(bsz, asz)
            tte = None if exp is None else exp - t
            # ONLY the window's own active period. Without this the tape's
            # pre-open placeholder quote (a static 0.48/0.50 x 500 that can
            # run for the better part of an hour) dominates the series and
            # drives price_span to 0 and bands_visited to 1 -- which reads
            # as "this venue never moves" when it is really "we measured the
            # wrong minutes". Caught live: GEMI-BTC05M2608310400-UP showed
            # 597 observations over 3271 s with tte_p50 2026 s, for a market
            # that is 300 s long.
            dur = duration_s(sym)
            if tte is None or dur is None or not (0.0 <= tte <= dur):
                out_of_window[sym] = out_of_window.get(sym, 0) + 1
                continue
            obs[sym].append({"t": t, "price": (bid + ask) / 2.0,
                             "two_sided": bid > 0 and ask > 0,
                             "spread_ticks": round((ask - bid) / TICK, 2),
                             "tte_s": tte,
                             "depth_at_touch": depth})
    out, skipped = [], {"too_short": 0, "unusable": 0}
    for sym, v in obs.items():
        v.sort(key=lambda o: o["t"])
        feat = hs.window_features(v)
        if not feat.get("usable"):
            skipped["unusable"] += 1
            continue
        if feat["span_s"] < MIN_SPAN_S:
            skipped["too_short"] += 1
            continue
        feat["market"] = sym
        feat["price_series_is"] = "BBO mid (quotes), not trades"
        out.append(feat)
    return out, {"symbols_seen": len(obs), "scored": len(out),
                 "skipped": skipped, "rows_without_expiry": no_expiry,
                 "rows_dropped_outside_window": sum(out_of_window.values()),
                 "symbols_with_dropped_rows": len(out_of_window)}


def q(vals, p):
    v = sorted(x for x in vals if x is not None)
    return round(v[min(len(v) - 1, int(p * (len(v) - 1)))], 4) if v else None


def main():
    if not os.path.exists(REF):
        print("no l5_reference.json -- run run.py first"); return 1
    reference = json.load(open(REF))
    print("L5 reference: the SAME file run.py wrote for the PMUS baseline\n")
    summary = {}
    for venue, path in TAPES.items():
        ws, audit = windows(path)
        scored = []
        for w in ws:
            cand = {k: v for k, v in w.items() if not k.startswith("_")}
            cand["observations_per_min"] = (round(w["observations"]
                                                  / (w["span_s"] / 60.0), 4)
                                            if w["span_s"] else None)
            s = hs.score(cand, reference)
            scored.append({"market": w["market"], "span_s": w["span_s"],
                           "observations": w["observations"],
                           "observations_per_min": cand["observations_per_min"],
                           **s})
        vals = [s.get("habitat_score_on_observed") for s in scored]
        disc = [s.get("coverage_discounted_score") for s in scored]
        cov = [s.get("coverage") for s in scored]
        summary[venue] = {
            "audit": audit, "n": len(scored),
            "score_p25": q(vals, .25), "score_p50": q(vals, .50),
            "score_p75": q(vals, .75),
            "discounted_p50": q(disc, .50), "coverage_p50": q(cov, .50),
            "unknown_features": sorted({f for s in scored
                                        for f, v in (s.get("per_feature") or {}).items()
                                        if (v or {}).get("status") == hs.UNKNOWN}),
        }
        print("=" * 74)
        print(f"{venue.upper()}   {path}")
        print("=" * 74)
        print(f"  symbols seen {audit['symbols_seen']}   scored {audit['scored']}"
              f"   skipped {audit['skipped']}")
        print(f"  score  p25 {summary[venue]['score_p25']}"
              f"   p50 {summary[venue]['score_p50']}"
              f"   p75 {summary[venue]['score_p75']}")
        print(f"  coverage p50 {summary[venue]['coverage_p50']}"
              f"   discounted p50 {summary[venue]['discounted_p50']}")
        print(f"  UNKNOWN features: {summary[venue]['unknown_features'] or 'none'}")
        top = sorted(scored, key=lambda s: (s.get("habitat_score_on_observed") or 0), reverse=True)[:5]
        for s in top:
            print(f"    {s['market'][:38]:38s} obs {s['observations']:5d} "
                  f"span {s['span_s']:7.0f}s  score {s.get('score')}")
        print()
    out = os.path.join(HERE, "gemini_habitat.json")
    json.dump(summary, open(out, "w"), indent=1, sort_keys=True, default=str)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
