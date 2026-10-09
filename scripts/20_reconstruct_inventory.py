"""Stage B -- reconstruct L5's inventory state after EVERY fill.

Input : export/work/fills_raw.jsonl.gz, settlement.csv (stage A)
Output: monthly shards of the enriched per-fill table, plus a per-window
        summary table and a reconciliation report.

WHAT IS RECONSTRUCTED, AND HOW

A window (one `event_slug`, e.g. `btc-updown-15m-1787903100`) is a binary
pair: the two outcomes are complements, so one contract of each is a
matched pair that settles for exactly $1.00 whichever way the window
resolves. Everything below follows from that.

For each fill, in timestamp order inside its window, we carry:

  * per-outcome cumulative quantity, spend and VWAP;
  * FIFO lot queues per outcome, so matched pairs are costed against the
    ACTUAL lots that paired, not against a blended average. A blended
    average is wrong whenever the two legs are bought at different times
    at different prices, which is the normal case here;
  * matched_qty = min(cum_up, cum_down) and the exact FIFO spend that
    bought those pairs, hence effective_pair_cost = that spend / matched_qty;
  * the residual: which outcome is held unpaired, how much, at what FIFO
    cost. The residual is the only directional risk in the window.

SIDE CONVENTION. The outcome LABEL ("Up"/"Down") is authoritative, not
`outcomeIndex`: 3,196 fills in this corpus carry an index whose polarity
is reversed relative to the majority, so indexing on it would silently
swap legs in those windows. Fills with no usable label are counted and
routed to an `UNLABELLED` bucket rather than guessed at.

SELLs. L5 is buy-only on this corpus except for 2 fills. A SELL is still
handled correctly -- it retires FIFO lots on its own leg -- so no fill is
dropped or special-cased away.

No network. Read-only on stage A output.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import json
import os
import sys
from collections import deque

EPS = 1e-9

FILL_COLS = [
    # identity / provenance
    "event_slug", "asset", "duration_minutes", "trade_seq",
    "timestamp", "datetime_utc", "condition_id", "tx_hash", "source_file",
    # clock
    "window_start_epoch", "window_end_epoch",
    "seconds_from_window_start", "seconds_to_settlement",
    # the fill itself
    "leg", "outcome_raw", "outcome_index", "side", "price", "size",
    "usdc_size",
    # inventory after this fill
    "cum_up_qty", "cum_up_spend", "up_vwap",
    "cum_down_qty", "cum_down_spend", "down_vwap",
    "matched_qty", "matched_spend_fifo", "effective_pair_cost",
    "locked_pair_profit",
    "residual_side", "residual_qty", "residual_spend", "residual_vwap",
    "total_spend", "open_directional_usd",
    # context
    "fills_so_far", "seconds_since_prev_fill", "leg_switched_from_prev",
    "settled_outcome", "settlement_evidence",
]

WINDOW_COLS = [
    "event_slug", "asset", "duration_minutes",
    "window_start_epoch", "window_end_epoch", "window_start_utc",
    "n_fills", "first_fill_epoch", "last_fill_epoch",
    "first_fill_sec_from_start", "last_fill_sec_from_start",
    "n_leg_switches", "n_up_fills", "n_down_fills",
    "final_up_qty", "final_down_qty", "final_up_vwap", "final_down_vwap",
    "final_matched_qty", "final_matched_spend", "final_pair_cost",
    "final_residual_side", "final_residual_qty", "final_residual_vwap",
    "total_spend", "min_price", "max_price", "mean_price",
    "settled_outcome", "settlement_evidence",
    "settlement_proceeds", "window_pnl", "window_pnl_per_matched_pair",
    "redeem_usdc", "n_redeems",
]


def fnum(x, nd=6):
    return "" if x is None else f"{x:.{nd}f}"


def leg_of(outcome_raw):
    o = (outcome_raw or "").strip().lower()
    if o == "up":
        return "UP"
    if o == "down":
        return "DOWN"
    return "UNLABELLED"


def fifo_add(q, qty, price):
    q.append([qty, price])


def fifo_take(q, qty):
    """Retire `qty` from the front. Returns (spend_retired, qty_retired)."""
    spend = 0.0
    taken = 0.0
    while qty > EPS and q:
        lot = q[0]
        use = min(lot[0], qty)
        spend += use * lot[1]
        taken += use
        lot[0] -= use
        qty -= use
        if lot[0] <= EPS:
            q.popleft()
    return spend, taken


def fifo_state(q):
    qty = sum(l[0] for l in q)
    spend = sum(l[0] * l[1] for l in q)
    return qty, spend


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    settle = {}
    with open(os.path.join(a.work, "settlement.csv"), newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            settle[r["event_slug"]] = (r["settled_outcome"], r["evidence"])

    redeem = collections.defaultdict(lambda: [0.0, 0])
    with open(os.path.join(a.work, "cashflow.csv"), newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["type"] == "REDEEM" and r["event_slug"]:
                redeem[r["event_slug"]][0] += float(r["usdc_size"] or 0)
                redeem[r["event_slug"]][1] += 1

    # Group fills by window. Fills arrive globally chronological; windows
    # interleave, so buffer per window and flush when the file is done.
    by_window = collections.defaultdict(list)
    n_in = 0
    with gzip.open(os.path.join(a.work, "fills_raw.jsonl.gz"), "rt",
                   encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            by_window[r["event_slug"]].append(r)
            n_in += 1

    rep = {
        "fills_in": n_in,
        "fills_out": 0,
        "windows": 0,
        "windows_without_slug": 0,
        "legs": collections.Counter(),
        "unlabelled_fills": 0,
        "sell_fills": 0,
        "windows_with_settlement": 0,
        "windows_without_settlement": 0,
        "windows_fully_matched_at_end": 0,
        "windows_with_residual_at_end": 0,
        "windows_with_leg_switch": 0,
        "shards": {},
        "spend_total": 0.0,
        "matched_pairs_total": 0.0,
    }

    shard_writers = {}
    shard_files = {}

    def shard_for(ts):
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        return f"{d.year:04d}-{d.month:02d}"

    def get_writer(tag):
        if tag not in shard_writers:
            p = os.path.join(a.out, f"l5_fills_enriched_{tag}.csv.gz")
            fh = gzip.open(p, "wt", newline="", encoding="utf-8")
            w = csv.writer(fh)
            w.writerow(FILL_COLS)
            shard_writers[tag] = (fh, w)
            shard_files[tag] = p
        return shard_writers[tag][1]

    wsum = []

    for slug, rows in by_window.items():
        if not slug:
            rep["windows_without_slug"] += 1
        rep["windows"] += 1
        rows.sort(key=lambda r: (r["timestamp"], r["tx_hash"]))
        st_out, st_ev = settle.get(slug, ("", "none"))
        if st_out:
            rep["windows_with_settlement"] += 1
        else:
            rep["windows_without_settlement"] += 1

        wstart = rows[0]["window_start_epoch"]
        dur = rows[0]["duration_minutes"]
        wend = (wstart + dur * 60) if (wstart and dur) else None

        q = {"UP": deque(), "DOWN": deque(), "UNLABELLED": deque()}
        cum = {"UP": [0.0, 0.0], "DOWN": [0.0, 0.0], "UNLABELLED": [0.0, 0.0]}
        matched_qty = 0.0
        matched_spend = 0.0
        total_spend = 0.0
        prev_ts = None
        prev_leg = None
        switches = 0
        legcount = collections.Counter()
        prices = []
        out_rows = []

        for i, r in enumerate(rows, start=1):
            leg = leg_of(r["outcome"])
            rep["legs"][leg] += 1
            if leg == "UNLABELLED":
                rep["unlabelled_fills"] += 1
            legcount[leg] += 1
            px = r["price"]
            sz = r["size"]
            sell = (r["side"] or "").upper() == "SELL"
            if sell:
                rep["sell_fills"] += 1
            if px is not None and sz is not None:
                prices.append(px)
                if sell:
                    sp, tk = fifo_take(q[leg], sz)
                    cum[leg][0] -= tk
                    cum[leg][1] -= sp
                    total_spend -= sp
                else:
                    fifo_add(q[leg], sz, px)
                    cum[leg][0] += sz
                    cum[leg][1] += sz * px
                    total_spend += sz * px

            # pair off whatever can be paired, at ACTUAL FIFO lot cost
            upq, _ = fifo_state(q["UP"])
            dnq, _ = fifo_state(q["DOWN"])
            pairable = min(upq, dnq)
            if pairable > EPS:
                su, _ = fifo_take(q["UP"], pairable)
                sd, _ = fifo_take(q["DOWN"], pairable)
                matched_qty += pairable
                matched_spend += su + sd

            rupq, rupsp = fifo_state(q["UP"])
            rdnq, rdnsp = fifo_state(q["DOWN"])
            runq, runsp = fifo_state(q["UNLABELLED"])
            if rupq > EPS:
                rside, rqty, rspend = "Up", rupq, rupsp
            elif rdnq > EPS:
                rside, rqty, rspend = "Down", rdnq, rdnsp
            elif runq > EPS:
                rside, rqty, rspend = "UNLABELLED", runq, runsp
            else:
                rside, rqty, rspend = "", 0.0, 0.0

            dsec = None if prev_ts is None else r["timestamp"] - prev_ts
            switched = int(prev_leg is not None and leg != prev_leg)
            if switched:
                switches += 1
            prev_ts, prev_leg = r["timestamp"], leg

            out_rows.append([
                slug, r["asset"], dur, i,
                r["timestamp"],
                dt.datetime.fromtimestamp(
                    r["timestamp"], dt.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                r["condition_id"], r["tx_hash"], r["source_file"],
                wstart if wstart else "", wend if wend else "",
                (r["timestamp"] - wstart) if wstart else "",
                (wend - r["timestamp"]) if wend else "",
                leg, r["outcome"], r["outcome_index"], r["side"],
                fnum(px, 6), fnum(sz, 6), fnum(r["usdc_size"], 6),
                fnum(cum["UP"][0]), fnum(cum["UP"][1]),
                fnum(cum["UP"][1] / cum["UP"][0]) if cum["UP"][0] > EPS else "",
                fnum(cum["DOWN"][0]), fnum(cum["DOWN"][1]),
                fnum(cum["DOWN"][1] / cum["DOWN"][0])
                if cum["DOWN"][0] > EPS else "",
                fnum(matched_qty), fnum(matched_spend),
                fnum(matched_spend / matched_qty) if matched_qty > EPS else "",
                fnum(matched_qty - matched_spend) if matched_qty > EPS else "",
                rside, fnum(rqty), fnum(rspend),
                fnum(rspend / rqty) if rqty > EPS else "",
                fnum(total_spend), fnum(rspend),
                i - 1, "" if dsec is None else dsec, switched,
                st_out, st_ev,
            ])

        tag = shard_for(rows[0]["timestamp"])
        w = get_writer(tag)
        w.writerows(out_rows)
        rep["shards"][tag] = rep["shards"].get(tag, 0) + len(out_rows)
        rep["fills_out"] += len(out_rows)
        rep["spend_total"] += total_spend
        rep["matched_pairs_total"] += matched_qty

        if rqty > EPS:
            rep["windows_with_residual_at_end"] += 1
        else:
            rep["windows_fully_matched_at_end"] += 1
        if switches:
            rep["windows_with_leg_switch"] += 1

        proceeds = matched_qty
        if st_out and rside and rside.lower() == st_out.strip().lower():
            proceeds += rqty
        pnl = proceeds - total_spend
        rdm = redeem.get(slug, [0.0, 0])

        wsum.append([
            slug, rows[0]["asset"], dur, wstart if wstart else "",
            wend if wend else "",
            dt.datetime.fromtimestamp(
                wstart, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if wstart else "",
            len(rows), rows[0]["timestamp"], rows[-1]["timestamp"],
            (rows[0]["timestamp"] - wstart) if wstart else "",
            (rows[-1]["timestamp"] - wstart) if wstart else "",
            switches, legcount["UP"], legcount["DOWN"],
            fnum(cum["UP"][0]), fnum(cum["DOWN"][0]),
            fnum(cum["UP"][1] / cum["UP"][0]) if cum["UP"][0] > EPS else "",
            fnum(cum["DOWN"][1] / cum["DOWN"][0])
            if cum["DOWN"][0] > EPS else "",
            fnum(matched_qty), fnum(matched_spend),
            fnum(matched_spend / matched_qty) if matched_qty > EPS else "",
            rside, fnum(rqty),
            fnum(rspend / rqty) if rqty > EPS else "",
            fnum(total_spend),
            fnum(min(prices)) if prices else "",
            fnum(max(prices)) if prices else "",
            fnum(sum(prices) / len(prices)) if prices else "",
            st_out, st_ev,
            fnum(proceeds), fnum(pnl),
            fnum(pnl / matched_qty) if matched_qty > EPS else "",
            fnum(rdm[0]), rdm[1],
        ])

    for fh, _ in shard_writers.values():
        fh.close()

    wsum.sort(key=lambda r: (r[3] if r[3] != "" else 0, r[0]))
    wp = os.path.join(a.out, "l5_window_summary.csv.gz")
    with gzip.open(wp, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(WINDOW_COLS)
        w.writerows(wsum)

    rep["legs"] = dict(rep["legs"])
    rep["shard_files"] = {k: os.path.basename(v)
                          for k, v in sorted(shard_files.items())}
    rep["reconciles"] = rep["fills_in"] == rep["fills_out"]
    with open(os.path.join(a.out, "INVENTORY_REPORT.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    print(json.dumps(rep, indent=1, sort_keys=True))
    return 0 if rep["reconciles"] else 1


if __name__ == "__main__":
    sys.exit(main())
