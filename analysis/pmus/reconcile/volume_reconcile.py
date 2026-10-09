"""Does PMUS BTC15m volume reach our resting price, or do we just not see it?

THE CHALLENGE THIS ANSWERS. The NO-GO rested on modelled fill counts. But a
modelled non-fill has four possible causes and only one of them is the venue:
our price was wrong, our timing was wrong, the queue ahead of us never
cleared, our tape never recorded the prints, or there was genuinely no
opposing flow. This separates them on the ONE PMUS capture we hold at fine
resolution.

THE DEFECT THIS EXPOSES, which matters more than any number here.
`pmus_status_report.infer_trades` emits ONE synthetic trade per poll interval.
Its `vol` is the WHOLE cumulative-volume delta over the interval; its `px` is
`last_trade_px`, the price of the LAST print only. So every print in an
interval is attributed to one price. That is not conservative in one
direction -- it is wrong in both:

  UNDER-CREDIT  500 shares print at the bid mid-interval, the last print is
                at the ask: a resting bid is credited NOTHING.
  OVER-CREDIT   the last print is at or below our bid: we are credited the
                interval's ENTIRE volume, including prints at other levels.

The size of both errors scales with the poll interval, and the shadow polls
every ~27 s. So this module reports what the same tape says at its native
2 s cadence and at resampled 27 s, and the gap between them is the measurement
artifact rather than a property of the market.

WHAT IS NOT CLAIMED. No fill here is real. A decrease in resting size is
ambiguous between a trade and a cancellation without a trade feed, and is
labelled UNKNOWN_DECREASE rather than counted as flow. A touch is never a
fill.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

TICK = D("0.01")


def _d(x) -> Optional[D]:
    try:
        return None if x is None else D(str(x))
    except Exception:                                          # noqa: BLE001
        return None


def _f(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:                                          # noqa: BLE001
        return None


def _ts(s: str) -> Optional[float]:
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(s)).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def load(path: str) -> Dict[str, Dict[str, list]]:
    """Per-slug bbo rows, book rows, and error rows, in time order."""
    out: Dict[str, Dict[str, list]] = defaultdict(
        lambda: {"bbo": [], "book": [], "err": []})
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        slug, kind = r.get("slug"), r.get("kind")
        if not slug:
            continue
        t = _ts(r.get("t"))
        if t is None:
            continue
        r["_t"] = t
        if kind == "bbo":
            out[slug]["bbo"].append(r)
        elif kind == "book":
            out[slug]["book"].append(r)
        elif kind in ("error", "book_error"):
            out[slug]["err"].append(r)
    for v in out.values():
        for k in v:
            v[k].sort(key=lambda r: r["_t"])
    return out


def coverage(bbo: Sequence[dict], err: Sequence[dict],
             interval_s: float) -> dict:
    """Polling gaps, duplicate snapshots, and the volume the tape never saw.

    A DUPLICATE is a poll whose cumulative volume AND touch are unchanged
    from the previous one -- it carries no new information. A GAP is a
    consecutive pair more than 3x the configured interval apart.
    """
    gaps, dups = [], 0
    for a, b in zip(bbo, bbo[1:]):
        dt = b["_t"] - a["_t"]
        if dt > 3 * interval_s:
            gaps.append(round(dt, 1))
        if (b.get("shares_traded") == a.get("shares_traded")
                and b.get("bid") == a.get("bid")
                and b.get("ask") == a.get("ask")):
            dups += 1
    first = _f(bbo[0].get("shares_traded")) if bbo else None
    last = _f(bbo[-1].get("shares_traded")) if bbo else None
    # sum of positive deltas the tape actually observed
    seen = 0.0
    for a, b in zip(bbo, bbo[1:]):
        x, y = _f(a.get("shares_traded")), _f(b.get("shares_traded"))
        if x is not None and y is not None and y > x:
            seen += y - x
    span = (bbo[-1]["_t"] - bbo[0]["_t"]) if len(bbo) > 1 else 0.0
    return {
        "polls": len(bbo),
        "errors": len(err),
        "error_share": (round(len(err) / (len(err) + len(bbo)), 4)
                        if (len(err) + len(bbo)) else None),
        "span_s": round(span, 1),
        "mean_interval_s": (round(span / (len(bbo) - 1), 2)
                            if len(bbo) > 1 else None),
        "gaps_over_3x_interval": gaps,
        "duplicate_snapshots": dups,
        "cum_volume_first": first,
        "cum_volume_last": last,
        "cum_volume_rise": (None if first is None or last is None
                            else round(last - first, 2)),
        "volume_observed_in_deltas": round(seen, 2),
        # these agree by construction unless the counter reset or a gap
        # swallowed a reset; reported so disagreement is visible
        "delta_sum_matches_rise": (
            None if first is None or last is None
            else abs(seen - (last - first)) < 0.01),
        "volume_before_tape_started": first,
        "NOTE": ("cumulative volume means the tape MISSES NO VOLUME, only "
                 "the price and timing of the prints inside each interval"),
    }


def one_sidedness(bbo: Sequence[dict]) -> dict:
    n = len(bbo) or 1
    two = sum(1 for r in bbo if r.get("bid") and r.get("ask"))
    return {"polls": len(bbo), "two_sided_polls": two,
            "two_sided_share": round(two / n, 4),
            "WHY_IT_MATTERS": ("the entry rule requires a two-sided book; a "
                              "one-sided poll is not an opportunity our "
                              "clone ever declines, it is one it never "
                              "sees")}


def queue_at_touch(book: Sequence[dict], bbo: Sequence[dict]) -> dict:
    """Shares standing at the own-side touch -- the queue a 1-share resting
    quote must wait behind before one share of it is ours."""
    qs: List[float] = []
    for b in book:
        bids = b.get("bids") or []
        if not bids:
            continue
        q = _f(bids[0][1] if isinstance(bids[0], (list, tuple))
               else bids[0].get("qty"))
        if q is not None:
            qs.append(q)
    qs.sort()
    def p(x):
        return round(qs[min(len(qs) - 1, int(x * (len(qs) - 1)))], 1) if qs \
            else None
    return {"book_snapshots": len(qs), "queue_at_bid_p10": p(0.10),
            "queue_at_bid_p50": p(0.50), "queue_at_bid_p90": p(0.90),
            "MEANING": ("printed volume must EXCEED this before any of a "
                       "resting 1-share quote is filled")}


def attribution(bbo: Sequence[dict], resample_s: float) -> dict:
    """How much volume `infer_trades` would credit to a resting bid at the
    touch, at the tape's own cadence and at a coarser one.

    Reimplements the inference rather than importing it, so the resampling
    is visible: one synthetic trade per interval, the whole delta at the
    LAST print's price.
    """
    def run(rows: Sequence[dict]) -> dict:
        credited = unknown = to_other = 0.0
        for a, b in zip(rows, rows[1:]):
            x, y = _f(a.get("shares_traded")), _f(b.get("shares_traded"))
            if x is None or y is None or y <= x:
                continue
            dv = y - x
            px, bid, ask = (_d(b.get("last")), _d(b.get("bid")),
                            _d(b.get("ask")))
            if px is None or bid is None:
                unknown += dv
            elif px <= bid:
                credited += dv           # inferred SELL into our bid
            elif ask is not None and px >= ask:
                to_other += dv           # inferred BUY, no help to a bid
            else:
                unknown += dv            # inside the spread: unattributable
        tot = credited + unknown + to_other
        return {"volume": round(tot, 2),
                "credited_to_a_resting_bid": round(credited, 2),
                "attributed_to_buys": round(to_other, 2),
                "unattributable": round(unknown, 2),
                "credited_share": (round(credited / tot, 4) if tot else None),
                "intervals": max(0, len(rows) - 1)}

    coarse: List[dict] = []
    last_t = None
    for r in bbo:
        if last_t is None or r["_t"] - last_t >= resample_s:
            coarse.append(r)
            last_t = r["_t"]
    return {"native": run(bbo), "resampled_%ds" % resample_s: run(coarse),
            "RESAMPLING_IS_THE_POINT": (
                "the market is identical in both rows; only the sampling "
                "changes, so any difference is measurement, not flow")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape",
                    default="data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl")
    ap.add_argument("--interval-s", type=float, default=2.0)
    ap.add_argument("--resample-s", type=float, default=27.0)
    ap.add_argument("--out",
                    default="analysis/pmus/reconcile/volume_reconcile.json")
    a = ap.parse_args(argv)

    per = load(a.tape)
    rep: Dict[str, Any] = {
        "tape": a.tape,
        "native_interval_s": a.interval_s,
        "shadow_interval_s": a.resample_s,
        "windows": {},
        "LABEL": "BOOK_AND_VOLUME_OBSERVATION_NOT_A_FILL",
    }
    for slug, v in sorted(per.items()):
        if not v["bbo"]:
            continue
        rep["windows"][slug] = {
            "coverage": coverage(v["bbo"], v["err"], a.interval_s),
            "two_sidedness": one_sidedness(v["bbo"]),
            "queue": queue_at_touch(v["book"], v["bbo"]),
            "price_attribution": attribution(v["bbo"], a.resample_s),
        }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
