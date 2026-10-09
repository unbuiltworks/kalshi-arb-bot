"""PMUS vs Polymarket BTC15m: venue, or our instrument?

WHY THIS SUPERSEDES THE EARLIER COMPARISON. The first reconciliation set a
27-second PMUS poll beside a 1-second Polymarket archive and read the
difference as flow. That confounds the two things we need to separate: the
venue and the sampling rate. This arm holds the sampling rate roughly fixed
-- PMUS at ~1.5 s, Polymarket at 1 s -- so a remaining difference is the
venue and not the instrument.

THE VENUES ARE NOT THE SAME MARKET. L5 trades Polymarket International; we
test PMUS. Separate order books, separate counterparties, separate matching.
Nothing here treats one as a proxy for the other.

NO CONTEMPORANEOUS OVERLAP EXISTS IN LOCAL DATA. The Polymarket archive runs
2026-08-21..24; the PMUS BBO tape runs 2026-08-30..09-02. They are seven days
apart, so this is a same-instrument, same-cadence, DIFFERENT-WEEK comparison
and is reported as such. A truly contemporaneous comparison needs a capture
we do not hold.

PMUS SINGLE-BOOK SEMANTICS. The tape carries UP symbols only, which is not a
missing side: PMUS serves one book per contract whose quoted price is the
long/UP side, so the DOWN side is the same book read as 1 - price. A DOWN
buyer's own-side bid is 1 - ask(UP), and that is how the complement is read
here rather than from a second book that does not exist.

GROUND TRUTH FOR DIRECTION. The prints file carries real PMUS trades with
tid, price, amount and an exchange-assigned buy/sell `type`. That lets the
volume-delta direction inference our replay relies on be scored against
truth instead of assumed correct.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

SYM = re.compile(r"GEMI-BTC(05|15)M(\d{10})-(UP|DOWN)")


def _pct(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 2)


def _f(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:                                          # noqa: BLE001
        return None


# --------------------------------------------------------------- loaders ---
def load_pmus_bbo(path: str) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = defaultdict(list)
    with gzip.open(path, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            m = SYM.match(str(r.get("symbol") or ""))
            if not m or m.group(1) != "15":
                continue
            b, a = _f(r.get("bid")), _f(r.get("ask"))
            if b is None or a is None or a <= b:
                continue
            out[r["symbol"]].append({
                "t": float(r["t"]), "bid": b, "ask": a,
                "bid_sz": _f(r.get("bid_sz")), "ask_sz": _f(r.get("ask_sz"))})
    for v in out.values():
        v.sort(key=lambda r: r["t"])
    return out


def load_pmus_prints(path: str) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = defaultdict(list)
    with gzip.open(path, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            sym = str(r.get("symbol") or "")
            for x in (r.get("rows") or []):
                out[sym].append({
                    "t": float(x["timestampms"]) / 1000.0,
                    "px": _f(x.get("price")), "qty": _f(x.get("amount")),
                    "type": str(x.get("type") or "")})
    for v in out.values():
        v.sort(key=lambda r: r["t"])
    return out


# ------------------------------------------------- venue characterisation ---
def characterise(series: Dict[str, List[dict]], *, venue: str,
                 min_rows: int) -> dict:
    """Queue at the own-side touch, spread, and how often the touch moves.

    Both sides are measured: the UP buyer's queue is bid_sz, and the DOWN
    buyer's own-side queue is ask_sz, because a DOWN bid at 1-ask stands in
    the same book as the UP offers.
    """
    q_up: List[float] = []
    q_dn: List[float] = []
    spreads: List[float] = []
    moves: List[float] = []
    kept = 0
    for sym, rows in series.items():
        if len(rows) < min_rows:
            continue
        kept += 1
        ch = 0
        prev = None
        for r in rows:
            if r["bid_sz"] is not None:
                q_up.append(r["bid_sz"])
            if r["ask_sz"] is not None:
                q_dn.append(r["ask_sz"])
            spreads.append(round(r["ask"] - r["bid"], 4))
            if prev is not None and r["bid"] != prev:
                ch += 1
            prev = r["bid"]
        span = rows[-1]["t"] - rows[0]["t"]
        if span > 0:
            moves.append(ch / (span / 900.0))      # per 15-minute window
    pooled = q_up + q_dn
    return {
        "venue": venue,
        "windows_kept": kept,
        "observations": len(spreads),
        "mean_interval_s": (round(sum(
            (rows[-1]["t"] - rows[0]["t"]) / max(1, len(rows) - 1)
            for rows in series.values() if len(rows) >= min_rows)
            / max(1, kept), 2)),
        "queue_at_own_touch": {
            "n": len(pooled), "p10": _pct(pooled, 0.10),
            "p50": _pct(pooled, 0.50), "p90": _pct(pooled, 0.90),
            "share_under_100": (round(sum(1 for q in pooled if q < 100)
                                      / len(pooled), 4) if pooled else None),
            "share_under_1000": (round(sum(1 for q in pooled if q < 1000)
                                       / len(pooled), 4) if pooled else None),
        },
        "queue_up_side_p50": _pct(q_up, 0.5),
        "queue_down_side_p50": _pct(q_dn, 0.5),
        "spread": {"p50": _pct(spreads, 0.5), "p90": _pct(spreads, 0.9)},
        "best_bid_changes_per_window": {
            "p10": _pct(moves, 0.10), "p50": _pct(moves, 0.50),
            "p90": _pct(moves, 0.90)},
    }


# --------------------------- is our direction inference actually correct? ---
def score_direction(bbo: Dict[str, List[dict]],
                    prints: Dict[str, List[dict]]) -> dict:
    """Score the volume-delta inference against the exchange's own buy/sell.

    For each real print, find the last BBO at or before it and apply exactly
    the rule `pmus_status_report.infer_trades` uses: at/above ask -> buy,
    at/below bid -> sell, otherwise unknown. The exchange's `type` is truth.
    """
    tot = ok = unknown = 0
    conf: Dict[str, int] = defaultdict(int)
    for sym, rows in prints.items():
        m = SYM.match(sym)
        if not m or m.group(1) != "15":
            continue
        quotes = bbo.get(sym) or []
        if not quotes:
            continue
        for p in rows:
            q = None
            for r in quotes:
                if r["t"] <= p["t"]:
                    q = r
                else:
                    break
            if q is None or p["px"] is None:
                continue
            tot += 1
            if p["px"] >= q["ask"]:
                guess = "buy"
            elif p["px"] <= q["bid"]:
                guess = "sell"
            else:
                guess = "unknown"
            conf["%s_as_%s" % (p["type"], guess)] += 1
            if guess == "unknown":
                unknown += 1
            elif guess == p["type"]:
                ok += 1
    return {
        "prints_scored": tot,
        "correctly_directed": ok,
        "unattributable_inside_spread": unknown,
        "accuracy_where_directed": (round(ok / (tot - unknown), 4)
                                    if tot - unknown else None),
        "unknown_share": (round(unknown / tot, 4) if tot else None),
        "confusion": dict(conf),
        "TRUTH": "exchange-assigned trade type from the prints feed",
    }


# ------------------------------------- what our poll cadence would destroy ---
def sampling_loss(series: Dict[str, List[dict]], every_s: float,
                  min_rows: int) -> dict:
    """Count touch moves and own-level size decreases at native cadence and
    at our shadow's, over the SAME PMUS rows."""
    def count(rows: Sequence[dict]) -> Dict[str, int]:
        ch = dec = 0
        pb = ps = None
        for r in rows:
            if pb is not None and r["bid"] != pb:
                ch += 1
            if (pb is not None and r["bid"] == pb and ps is not None
                    and r["bid_sz"] is not None and r["bid_sz"] < ps):
                dec += 1
            pb, ps = r["bid"], r["bid_sz"]
        return {"moves": ch, "decreases": dec, "snapshots": len(rows)}

    fine = {"moves": 0, "decreases": 0, "snapshots": 0}
    crs = {"moves": 0, "decreases": 0, "snapshots": 0}
    for rows in series.values():
        if len(rows) < min_rows:
            continue
        a = count(rows)
        coarse, last = [], None
        for r in rows:
            if last is None or r["t"] - last >= every_s:
                coarse.append(r)
                last = r["t"]
        b = count(coarse)
        for k in fine:
            fine[k] += a[k]
            crs[k] += b[k]
    return {"at_native": fine, "at_%ds" % every_s: crs,
            "visible_share": {k: (round(crs[k] / fine[k], 4) if fine[k]
                                  else None) for k in fine},
            "VENUE": "PMUS, measured on PMUS rows"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pmus-bbo",
                    default="data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz")
    ap.add_argument("--pmus-prints",
                    default="data/transfer_backtest/"
                            "gemini_prints_15m_tail.jsonl.gz")
    ap.add_argument("--zenodo",
                    default="data/zenodo_22084977/updown_15m.parquet")
    ap.add_argument("--min-rows", type=int, default=120)
    ap.add_argument("--resample-s", type=float, default=27.0)
    ap.add_argument("--out",
                    default="analysis/pmus/reconcile/cross_venue.json")
    a = ap.parse_args(argv)

    pm = load_pmus_bbo(a.pmus_bbo)
    pr = load_pmus_prints(a.pmus_prints)

    # Polymarket, same shape: touch size from the top ladder level
    import pyarrow.parquet as pq
    d = pq.read_table(a.zenodo).to_pydict()
    poly: Dict[str, List[dict]] = defaultdict(list)
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        bb, ba = d["best_bid"][i], d["best_ask"][i]
        if bb is None or ba is None or ba <= bb:
            continue
        bs, asz = (d["bid_sizes"][i] or []), (d["ask_sizes"][i] or [])
        poly["%s|%s" % (d["market_slug"][i], d["token_id"][i])].append({
            "t": d["captured_at"][i].timestamp(),
            "bid": float(bb), "ask": float(ba),
            "bid_sz": (float(bs[0]) if bs else None),
            "ask_sz": (float(asz[0]) if asz else None)})
    for v in poly.values():
        v.sort(key=lambda r: r["t"])

    def span(series):
        ts = [r["t"] for v in series.values() for r in v]
        f = lambda x: dt.datetime.fromtimestamp(
            x, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        return [f(min(ts)), f(max(ts))] if ts else None

    rep: Dict[str, Any] = {
        "VENUES_ARE_DISTINCT": ("L5 trades Polymarket International; we test "
                                "PMUS. Separate books, counterparties and "
                                "matching. Neither is a proxy for the other."),
        "coverage": {"pmus": span(pm), "polymarket": span(poly),
                     "contemporaneous_overlap": None},
        "pmus": characterise(pm, venue="PMUS", min_rows=a.min_rows),
        "polymarket": characterise(poly, venue="POLYMARKET_INTL",
                                   min_rows=a.min_rows),
        "direction_inference_vs_truth": score_direction(pm, pr),
        "pmus_sampling_loss": sampling_loss(pm, a.resample_s, a.min_rows),
        "LABELS": ("Queue sizes are public touch sizes, attributed to no "
                   "trader. No fill is claimed anywhere. The two venues are "
                   "seven days apart, so venue differences here are not "
                   "contemporaneous."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
