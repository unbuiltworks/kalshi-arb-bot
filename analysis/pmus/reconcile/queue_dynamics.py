"""At 1-second resolution, how often does the queue in front of us clear?

WHY THIS ARM EXISTS. The PMUS captures we hold at fine resolution cover two
partial windows. This arm uses the Zenodo 1-second archive, which covers many
BTC15m windows with full 25-level ladders, to ask the question the PMUS tape
is too small to answer: is the queue standing at the touch small enough, often
enough, for a repeatedly re-quoted 1-share order to be reached?

VENUE CAVEAT, STATED ONCE AND MEANT. This archive is POLYMARKET, not PMUS. It
is the same instrument class and the same 15-minute up/down structure, and it
is the only many-window 1-second prediction-market book we hold. Conclusions
about queue DYNAMICS carry across as evidence about this kind of market;
conclusions about PMUS specifically do not, and are not drawn here.

WHAT A SIZE DECREASE MEANS, AND DOES NOT. This archive has no trade feed. A
level whose resting size falls may have been traded or cancelled, and the two
are indistinguishable here. Every decrease is therefore labelled
UNKNOWN_DECREASE and is NOT counted as flow or as a fill. What IS unambiguous
is the queue standing in front of a price, which is read directly off the
ladder, and that is what the headline numbers are built from.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

WINDOW_S = 900.0


def _pct(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--min-snapshots", type=int, default=300)
    ap.add_argument("--top-windows", type=int, default=12)
    ap.add_argument("--out",
                    default="analysis/pmus/reconcile/queue_dynamics.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    d = pq.read_table(a.parquet).to_pydict()

    # one series per (market, token); the own side of a BUY is the bid ladder
    series: Dict[tuple, List[dict]] = defaultdict(list)
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        series[(d["market_slug"][i], str(d["token_id"][i]))].append({
            "t": d["captured_at"][i].timestamp(),
            "stc": d["seconds_to_close"][i],
            "bb": d["best_bid"][i], "ba": d["best_ask"][i],
            "bp": d["bid_prices"][i], "bs": d["bid_sizes"][i]})
    for v in series.values():
        v.sort(key=lambda r: r["t"])

    picked = sorted(((k, v) for k, v in series.items()
                     if len(v) >= a.min_snapshots),
                    key=lambda kv: -len(kv[1]))[:a.top_windows]

    per_window: Dict[str, Any] = {}
    by_minute: Dict[int, Dict[str, list]] = defaultdict(
        lambda: {"queue": [], "spread": [], "dec": []})
    all_q: List[float] = []
    for (slug, tok), rows in picked:
        qs, decs, bb_changes = [], 0, 0
        prev_touch_sz = prev_bb = None
        spreads = []
        for r in rows:
            bp, bs = (r["bp"] or []), (r["bs"] or [])
            if not bp or not bs or r["bb"] is None:
                continue
            q = float(bs[0])
            qs.append(q)
            all_q.append(q)
            if r["ba"] is not None:
                spreads.append(round(float(r["ba"]) - float(r["bb"]), 4))
            if prev_bb is not None and float(r["bb"]) != prev_bb:
                bb_changes += 1
            if (prev_bb is not None and float(r["bb"]) == prev_bb
                    and prev_touch_sz is not None and q < prev_touch_sz):
                decs += 1
            prev_bb, prev_touch_sz = float(r["bb"]), q
            stc = r["stc"]
            if stc is not None:
                m = int(max(0.0, WINDOW_S - float(stc)) // 60)
                if 0 <= m <= 14:
                    by_minute[m]["queue"].append(q)
                    if spreads:
                        by_minute[m]["spread"].append(spreads[-1])
        per_window["%s|%s" % (slug, tok[:10])] = {
            "snapshots": len(rows),
            "queue_at_touch_p10": _pct(qs, 0.10),
            "queue_at_touch_p50": _pct(qs, 0.50),
            "queue_at_touch_p90": _pct(qs, 0.90),
            "share_of_seconds_queue_under_100": (
                round(sum(1 for q in qs if q < 100) / len(qs), 4)
                if qs else None),
            "share_of_seconds_queue_under_1000": (
                round(sum(1 for q in qs if q < 1000) / len(qs), 4)
                if qs else None),
            "median_spread": _pct(spreads, 0.5),
            "best_bid_changes": bb_changes,
            "unknown_decreases_at_touch": decs,
            "DECREASE_LABEL": "UNKNOWN_DECREASE: trade or cancel, "
                              "indistinguishable without a trade feed",
        }

    rep: Dict[str, Any] = {
        "source": a.parquet,
        "VENUE": "POLYMARKET 1-second archive, NOT PMUS",
        "windows_measured": len(per_window),
        "per_window": per_window,
        "pooled_queue_at_touch": {
            "n_seconds": len(all_q),
            "p10": _pct(all_q, 0.10), "p50": _pct(all_q, 0.50),
            "p90": _pct(all_q, 0.90),
            "share_under_100": (round(sum(1 for q in all_q if q < 100)
                                      / len(all_q), 4) if all_q else None),
            "share_under_1000": (round(sum(1 for q in all_q if q < 1000)
                                       / len(all_q), 4) if all_q else None),
        },
        "by_minute_of_window": {
            str(m): {"n": len(v["queue"]),
                     "queue_p50": _pct(v["queue"], 0.5),
                     "queue_p90": _pct(v["queue"], 0.9),
                     "spread_p50": _pct(v["spread"], 0.5)}
            for m, v in sorted(by_minute.items())},
        "LABELS": ("Queue size is read off the public ladder and is not "
                   "attributed to any trader. No fill is claimed. A size "
                   "decrease is UNKNOWN_DECREASE, never counted as flow."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: rep[k] for k in
                      ("VENUE", "windows_measured", "pooled_queue_at_touch",
                       "by_minute_of_window")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --------------------------------------------- what the sampling destroys ---
def sampling_loss(rows, every_s: float) -> dict:
    """Count the SAME events at 1 s and at a coarser cadence.

    The market is identical in both counts; only how often we look changes.
    So the ratio is the share of the book's activity our poll interval cannot
    see, and it bounds how many re-quote opportunities any replay built on
    that tape can possibly find.
    """
    def count(seq) -> dict:
        bb_ch = dec = 0
        prev_bb = prev_sz = None
        for r in seq:
            bp, bs = (r["bp"] or []), (r["bs"] or [])
            if not bp or not bs or r["bb"] is None:
                continue
            bb, sz = float(r["bb"]), float(bs[0])
            if prev_bb is not None and bb != prev_bb:
                bb_ch += 1
            if (prev_bb is not None and bb == prev_bb
                    and prev_sz is not None and sz < prev_sz):
                dec += 1
            prev_bb, prev_sz = bb, sz
        return {"snapshots": len(list(seq)), "best_bid_changes": bb_ch,
                "unknown_decreases_at_touch": dec}

    coarse, last = [], None
    for r in rows:
        if last is None or r["t"] - last >= every_s:
            coarse.append(r)
            last = r["t"]
    fine = count(rows)
    cz = count(coarse)
    def ratio(a, b):
        return round(b / a, 4) if a else None
    return {
        "at_1s": fine,
        "at_%ds" % every_s: cz,
        "visible_share": {
            "best_bid_changes": ratio(fine["best_bid_changes"],
                                      cz["best_bid_changes"]),
            "unknown_decreases": ratio(fine["unknown_decreases_at_touch"],
                                       cz["unknown_decreases_at_touch"]),
            "snapshots": ratio(fine["snapshots"], cz["snapshots"]),
        },
    }
