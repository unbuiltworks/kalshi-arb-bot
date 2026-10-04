"""Is there a REALISTICALLY EXECUTABLE BRTI -> PMUS latency window at all?

THE QUESTION. BRTI is the reference index the venue settles BTC15m against. If a
settlement-relevant BRTI move were observable before PMUS executable prices
adjusted, that would be a causal reason to choose the FIRST LEG of the sequential
inventory mechanism. This measures whether such a window exists. It does not
build a controller.

THE DISTINCTION THAT DECIDES IT, and the reason this study exists at all:

  INFORMATION LEAD -- the BRTI value was knowable to an actor, and PMUS had not
  yet moved. Actionable.

  TIMESTAMP ILLUSION -- the BRTI bucket is STAMPED earlier than the PMUS move, so
  a naive join shows BRTI "leading", but the bucket was not published when it is
  stamped and so could not have been acted on. Not actionable, and it looks
  identical to a lead unless publication time is handled explicitly.

A 1-second BRTI bucket covering [s, s+1) is not complete until the second ENDS.
Its close therefore cannot be known before s+1. That is rule 1 of
PREREGISTRATION_STATE_CONDITIONAL_V1 and it is enforced here: every measurement
is taken from t_avail = s + 1, never from the bucket's own stamp. Any PMUS move
between s and s+1 happened BEFORE the information existed, and is counted as
illusion rather than lead.

t_avail = s + 1 is a LOWER BOUND on availability. The venue's own publication and
transport delay beyond the bucket close is not measurable from this tape and is
certainly not negative, so every lead reported here is an OVERSTATEMENT of what
was actually reachable. The bias is deliberately in the optimistic direction.

THE SIGNAL IS NOT DEFINED BY SETTLEMENT. A move's size is the change in
distance-to-strike standardized by the realized volatility available at that
instant, and its direction is the sign of that change. The settlement outcome
enters only in the final economics column, which was asked for. No threshold is
selected by P&L: fixed magnitude bands are reported over the whole distribution,
noise bands included.

LATENCY IS MEASURED, NOT ASSUMED. 65 authenticated venue round-trips recorded in
analysis/l5/live_tape_2026-09-13/r5ft_live_tape.jsonl: median 273ms, p90 459ms,
p99 1531ms. Those are READ round-trips, and an order submission on this path does
a preview POST before the order POST, so real submit latency is worse -- using
the read figures is again optimistic.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import io
import json
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_fees as FEES                                 # noqa: E402

ONE, ZERO = D(1), D(0)
TICK = D("0.01")

#: measured venue round-trips (see module docstring). Reported at several points
#: of the measured distribution rather than reduced to one number.
LATENCY_MS = {"measured_median": 273.0, "measured_p90": 458.6,
              "measured_p99": 1531.3,
              "measured_median_x2_preview_plus_submit": 546.0}
HORIZONS_S = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
#: fixed, declared before looking at any result
MOVE_BANDS = ((0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0),
              (5.0, float("inf")))


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def band_of(v, bands):
    for lo, hi in bands:
        if lo <= v < hi:
            return "[%s,%s)" % (lo, hi)
    return "out"


def load_books(path):
    """slug -> (sorted times, rows). Sub-second PMUS book states."""
    by = collections.defaultdict(list)
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if r.get("kind") != "book" or not r.get("slug"):
                continue
            if r.get("bid") in (None, "") or r.get("ask") in (None, ""):
                continue
            by[r["slug"]].append(r)
    out = {}
    for k, v in by.items():
        v.sort(key=lambda r: float(r["t"]))
        out[k] = ([float(r["t"]) for r in v], v)
    return out


def load_brti(path):
    """slug -> per-second reference state, from the frozen state table."""
    by = collections.defaultdict(dict)
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if not r.get("slug") or r.get("raw_distance") is None:
                continue
            by[r["slug"]][int(r["t"])] = r
    return by


def book_at(times, rows, t):
    """Latest book state with timestamp <= t. None if the book predates t."""
    i = bisect.bisect_right(times, t) - 1
    return rows[i] if i >= 0 else None


def exe(row):
    """Executable taker prices: UP = ask, DOWN = 1 - bid, with touch depth."""
    try:
        bid, ask = D(str(row["bid"])), D(str(row["ask"]))
        bq = D(str(row.get("bid_qty") or 0))
        aq = D(str(row.get("ask_qty") or 0))
    except Exception:                                           # noqa: BLE001
        return None
    if ask <= 0 or bid <= 0 or ask > D("0.99") or bid > D("0.99"):
        return None
    return {"UP": (ask, aq), "DOWN": (ONE - bid, bq),
            "up_exit": bid, "down_exit": ONE - ask,
            "mid": (bid + ask) / D(2), "spread": ask - bid}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", required=True, help="derived book/trade jsonl")
    ap.add_argument("--state", required=True, help="frozen state table jsonl")
    ap.add_argument("--label", default="OOS")
    ap.add_argument("--out", default="analysis/l5/path_economics")
    a = ap.parse_args()

    books = load_books(a.books)
    brti = load_brti(a.state)
    slugs = sorted(set(books) & set(brti))

    events = []
    for slug in slugs:
        times, rows = books[slug]
        secs = sorted(brti[slug])
        for k in range(1, len(secs)):
            s0, s1 = secs[k - 1], secs[k]
            if s1 != s0 + 1:
                continue            # only adjacent buckets; no interpolation
            r0, r1 = brti[slug][s0], brti[slug][s1]
            try:
                d0, d1 = float(r0["raw_distance"]), float(r1["raw_distance"])
                sig = float(r1.get("sigma_1s") or 0)
            except Exception:                                   # noqa: BLE001
                continue
            if sig <= 0:
                continue            # no vol scale available yet; not an event
            dd = d1 - d0
            mag = abs(dd) / sig
            # DIRECTION FROM THE MOVE, NEVER FROM SETTLEMENT
            favored = "UP" if dd > 0 else "DOWN"
            if dd == 0:
                continue
            # the bucket closing at s1 is knowable only from s1 onward
            t_avail = float(s1)
            pre = book_at(times, rows, float(s0))      # before the info existed
            at_avail = book_at(times, rows, t_avail)
            if pre is None or at_avail is None:
                continue
            e_pre, e_av = exe(pre), exe(at_avail)
            if e_pre is None or e_av is None:
                continue
            px_pre = e_pre[favored][0]
            # THE ILLUSION TEST: did PMUS already move before t_avail?
            moved_before_avail = abs(e_av[favored][0] - px_pre) >= TICK
            # first MATERIAL response at or after t_avail
            i = bisect.bisect_left(times, t_avail)
            t_resp = None
            px_resp = None
            while i < len(times):
                ei = exe(rows[i])
                if ei is not None and abs(ei[favored][0] - px_pre) >= TICK:
                    t_resp = times[i] - t_avail
                    px_resp = ei[favored][0]
                    break
                i += 1
            rec = {
                "slug": slug, "bucket_close_s": s1, "t_avail": t_avail,
                "seconds_remaining": r1.get("seconds_remaining"),
                "brti": r1.get("brti_now"),
                "price_to_beat": r1.get("price_to_beat"),
                "d_distance": round(dd, 4), "sigma_1s": sig,
                "move_mag_sigmas": round(mag, 4),
                "band": band_of(mag, MOVE_BANDS),
                "favored": favored,
                "px_pre": str(px_pre),
                "depth_pre": str(e_pre[favored][1]),
                "px_at_avail": str(e_av[favored][0]),
                "depth_at_avail": str(e_av[favored][1]),
                "ILLUSION_pmus_moved_before_info_existed": moved_before_avail,
                "secs_to_material_response_after_avail": t_resp,
                "px_at_material_response": (str(px_resp) if px_resp is not None
                                            else None),
            }
            # could a taker submitted at t_avail have reached the stale price,
            # given MEASURED latency?
            for nm, ms in LATENCY_MS.items():
                b = book_at(times, rows, t_avail + ms / 1000.0)
                eb = exe(b) if b is not None else None
                if eb is None:
                    rec["capturable_" + nm] = None
                    continue
                px, dep = eb[favored]
                rec["capturable_" + nm] = bool(px <= px_pre and dep > 0)
                rec["px_after_" + nm] = str(px)
            # post-entry economics, entering at the price reachable after the
            # MEASURED MEDIAN latency, exiting at the executable exit price
            b = book_at(times, rows, t_avail + LATENCY_MS["measured_median"] / 1000.0)
            eb = exe(b) if b is not None else None
            if eb is not None and eb[favored][1] > 0:
                entry = eb[favored][0]
                fee_in = FEES.taker_fee(entry, ONE, cents=True)
                rec["entry_px"] = str(entry)
                rec["entry_all_in"] = str(entry + fee_in)
                for h in HORIZONS_S:
                    bh = book_at(times, rows, t_avail + h)
                    ehh = exe(bh) if bh is not None else None
                    if ehh is None:
                        rec["pnl_%ss" % h] = None
                        continue
                    ex = (ehh["up_exit"] if favored == "UP"
                          else ehh["down_exit"])
                    fee_out = FEES.taker_fee(ex, ONE, cents=True) if ex > 0 else ZERO
                    rec["pnl_%ss" % h] = str(ex - fee_out - entry - fee_in)
                # INFORMATION CONTENT, separated from execution cost: does
                # the mid actually drift the way the BRTI move pointed? A
                # latency edge is impossible, at ANY speed, if the favourable
                # drift is smaller than the round trip it must pay for.
                mid0 = eb["mid"]
                rec["spread_at_entry"] = str(eb["spread"])
                rec["round_trip_cost_floor"] = str(
                    eb["spread"] + fee_in
                    + FEES.taker_fee(eb["up_exit"] if favored == "UP"
                                     else eb["down_exit"], ONE, cents=True))
                for h in HORIZONS_S:
                    bh = book_at(times, rows, t_avail + h)
                    ehh = exe(bh) if bh is not None else None
                    if ehh is None:
                        rec["favmid_%ss" % h] = None
                        continue
                    dmid = ehh["mid"] - mid0
                    rec["favmid_%ss" % h] = str(dmid if favored == "UP"
                                                else -dmid)
                oc = r1.get("outcome_up")
                if oc is not None:
                    won = (oc == 1) if favored == "UP" else (oc == 0)
                    rec["pnl_settlement"] = str((ONE if won else ZERO)
                                                - entry - fee_in)
            events.append(rec)

    def agg(group):
        n = len(group)
        if not n:
            return None
        illus = sum(1 for e in group
                    if e["ILLUSION_pmus_moved_before_info_existed"])
        resp = [e["secs_to_material_response_after_avail"] for e in group
                if e["secs_to_material_response_after_avail"] is not None]
        out = {
            "n_events": n,
            "ILLUSION_pct_pmus_already_moved_before_info_existed":
                round(100.0 * illus / n, 2),
            "LEAD_pct_pmus_had_not_moved": round(100.0 * (n - illus) / n, 2),
            "secs_to_material_response_after_availability": {
                "n_responded": len(resp),
                "pct_never_responded": round(100.0 * (n - len(resp)) / n, 2),
                "median": round(statistics.median(resp), 3) if resp else None,
                "p10": (round(sorted(resp)[int(.1 * (len(resp) - 1))], 3)
                        if resp else None),
                "p90": (round(sorted(resp)[int(.9 * (len(resp) - 1))], 3)
                        if resp else None)},
            "depth_at_stale_price_median": (
                round(statistics.median([float(e["depth_pre"])
                                         for e in group]), 1)),
        }
        for nm in LATENCY_MS:
            v = [e["capturable_" + nm] for e in group
                 if e.get("capturable_" + nm) is not None]
            out["pct_capturable_at_" + nm] = (
                round(100.0 * sum(1 for x in v if x) / len(v), 2) if v else None)
        # economics, only where an entry was reachable
        for h in list(HORIZONS_S) + ["settlement"]:
            k = "pnl_%ss" % h if h != "settlement" else "pnl_settlement"
            v = [float(e[k]) for e in group if e.get(k) is not None]
            out["mean_" + k] = round(statistics.mean(v), 5) if v else None
            out["n_" + k] = len(v)
        for h in HORIZONS_S:
            k = "favmid_%ss" % h
            v = [float(e[k]) for e in group if e.get(k) is not None]
            out["mean_favourable_mid_drift_%ss" % h] = (
                round(statistics.mean(v), 5) if v else None)
        cf = [float(e["round_trip_cost_floor"]) for e in group
              if e.get("round_trip_cost_floor") is not None]
        sp = [float(e["spread_at_entry"]) for e in group
              if e.get("spread_at_entry") is not None]
        out["median_spread_at_entry"] = (round(statistics.median(sp), 4)
                                         if sp else None)
        out["mean_round_trip_cost_floor"] = (round(statistics.mean(cf), 5)
                                             if cf else None)
        out["VERDICT_drift_beats_cost_at_30s"] = (
            (out["mean_favourable_mid_drift_30.0s"] is not None
             and out["mean_round_trip_cost_floor"] is not None
             and out["mean_favourable_mid_drift_30.0s"]
             > out["mean_round_trip_cost_floor"]))
        return out

    by_band = {}
    for lo, hi in MOVE_BANDS:
        b = band_of(lo, MOVE_BANDS)
        g = [e for e in events if e["band"] == b]
        if g:
            by_band[b] = agg(g)
    out = {
        "sample": a.label, "books": a.books, "state": a.state,
        "windows": len(slugs), "n_brti_events": len(events),
        "measured_latency_ms": LATENCY_MS,
        "latency_provenance": ("analysis/l5/live_tape_2026-09-13/"
                               "r5ft_live_tape.jsonl, 65 authenticated venue "
                               "round-trips; READ round-trips, so an order "
                               "submit (preview POST + order POST) is worse"),
        "NOTE_availability": ("every measurement starts at t_avail = bucket "
                              "close, because a 1s bucket is not knowable "
                              "before its second ends; venue publication delay "
                              "beyond that is unmeasured and >= 0, so every "
                              "lead here is an OVERSTATEMENT"),
        "NOTE_signal": ("move size = d(distance to strike) / sigma_1s, "
                        "direction = sign of that change. Settlement is used "
                        "only in the final economics column."),
        "ALL_events": agg(events),
        "by_move_magnitude_band": by_band,
    }
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "brti_lag_%s.json" % a.label.lower())
    with open(p, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
