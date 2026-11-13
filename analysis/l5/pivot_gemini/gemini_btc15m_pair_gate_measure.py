#!/usr/bin/env python3
"""Gemini BTC15m: the SAME measurement as analysis/l5/btc15m_pair_gate_measure.py
(PMUS), run on RECORDED Gemini BTC15m top-of-book. Only the loader is new;
the policy, fill rules, cut times and statistics are copied unchanged, plus
an explicit per-filled-leg fee line (the PMUS script carried no fee).

REPLAY. Hypothetical fills, NEVER realized.

DATA (all read-only, all recorded by our own collectors, no orders of ours
were resting on Gemini in this period -- live Gemini trading began 2026-09-17):
  book   data/ref_lag/reflag_*.jsonl            kind=pbook (WS best bid/ask, b/a)
         data/policy_d_port*/tape_*.jsonl       venue=gemini kind=bbo (polled ~5s)
         data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz (polled ~5s)
  outcome data/ref_lag/reflag_*.jsonl           kind=settlement, venue-published
                                                 resolution_side yes/no
Window = [expiry-900s, expiry]; the expiry is parsed from the symbol
(GEMI-BTC15MyymmddHHMM-UP, UTC). Rows before the window start (the contract
is listed early) and after expiry are dropped, so t_offset matches PMUS.
Gemini contracts are a single YES instrument per window; YES=UP, NO=DOWN,
and NO prices are 1 - YES (complement identity, checked as 0.00 in
analysis/gemini/btc15m_v1/continuous_v1.jsonl). Tick = 0.01.

NO LOOKAHEAD: decisions read only the current row; fills are decided only by
LATER rows; the winner is read only to settle a lone leg at window end.

FEES (Gemini published schedule, analysis/gemini/MAKER_FEE_RESOLVED.md):
  ZERO        -- the latest 18 maker fills (2026-09-22) were charged $0.00
  MAKER_RAW   -- 0.0175 * P * (1-P) per contract, unrounded (large-size limit)
  MAKER_CEIL1 -- the same rounded UP to the next cent per 1-contract fill
                 (= $0.01 per fill at any P in 0.01..0.99); charged on this
                 account on 2026-09-01/02, i.e. IN this data period.

    python3 analysis/l5/pivot_gemini/gemini_btc15m_pair_gate_measure.py [--out OUT.json]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_CEILING

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "analysis", "l5"))
import btc15m_pair_gate_measure as pmus  # noqa: E402  (cross-check only)

TICK = D("0.01")
ENTRY_CUT, COMPLETE_CUT = 600.0, 870.0      # identical to the PMUS script
WINDOW_S = 900.0


def fee_fn(name):
    if name == "ZERO":
        return lambda p: D(0)
    if name == "MAKER_RAW":
        return lambda p: D("0.0175") * p * (1 - p)
    if name == "MAKER_CEIL1":
        return lambda p: (D("0.0175") * p * (1 - p)).quantize(TICK, rounding=ROUND_CEILING)
    raise ValueError(name)


def expiry_ts(sym):
    s = sym.split("BTC15M")[1][:10]
    return datetime.strptime(s, "%y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp()


def _px(x):
    return D(x) if x not in (None, "") else None


def load():
    raw = defaultdict(set)               # symbol -> {(t, bid, ask)}
    winners = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data/ref_lag/reflag_*.jsonl"))):
        for line in open(f):
            if "BTC15M" not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            m = r.get("market") or ""
            if not m.startswith("GEMI-BTC15M"):
                continue
            if r.get("kind") == "pbook":
                raw[m].add((r["t_ns"] / 1e9, r.get("b") or "", r.get("a") or ""))
            elif r.get("kind") == "settlement" and r.get("resolution_side") in ("yes", "no"):
                w = "UP" if r["resolution_side"] == "yes" else "DOWN"
                if winners.get(m, w) != w:
                    raise SystemExit("conflicting settlement for %s" % m)
                winners[m] = w
    for f in sorted(glob.glob(os.path.join(ROOT, "data/policy_d_port*/tape_*.jsonl"))):
        for line in open(f):
            if '"bbo"' not in line or "GEMI-BTC15M" not in line:
                continue
            r = json.loads(line)
            if r.get("venue") != "gemini":
                continue
            b = r["bids"][0][0] if r.get("bids") else ""
            a = r["asks"][0][0] if r.get("asks") else ""
            raw[r["market"]].add((r["t"], b, a))
    with gzip.open(os.path.join(ROOT, "data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz"), "rt") as fh:
        for line in fh:
            r = json.loads(line)
            raw[r["symbol"]].add((r["t"], r.get("bid") or "", r.get("ask") or ""))
    books = {}
    for m, s in raw.items():
        start = expiry_ts(m) - WINDOW_S
        rows = []
        for t, b, a in sorted(s):
            off = t - start
            if 0.0 <= off <= WINDOW_S:
                rows.append((off, _px(b), _px(a)))
        if rows:
            books[m] = rows
    return books, winners


def sim(rows, winner, d, T, rule, chase=None, fee=lambda p: D(0)):
    """Identical to btc15m_pair_gate_measure.sim, plus a fee per filled leg."""
    pnl, pairs, lones, opened = D(0), 0, 0, 0
    fees = D(0)
    pair_costs = []
    state = None
    for t, b, a in rows:
        if b is None or a is None:
            continue
        if state is not None:
            if chase is not None and state["fu"] != state["fd"]:
                if state["fu"] and not state["fd"]:
                    state["dn"] = min(max(state["dn"], 1 - a), chase - state["up"])
                elif state["fd"] and not state["fu"]:
                    state["up"] = min(max(state["up"], b), chase - state["dn"])
            if not state["fu"]:
                state["fu"] = (a < state["up"]) if rule == "through" else (a <= state["up"])
            if not state["fd"]:
                lvl = 1 - state["dn"]
                state["fd"] = (b > lvl) if rule == "through" else (b >= lvl)
            if state["fu"] and state["fd"]:
                c = state["up"] + state["dn"]
                pnl += 1 - c; pairs += 1; pair_costs.append(c)
                fees += fee(state["up"]) + fee(state["dn"])
                state = None
                continue
            if not state["fu"] and not state["fd"] and t >= ENTRY_CUT:
                state = None
            elif t >= COMPLETE_CUT:
                break
            continue
        if t >= ENTRY_CUT:
            continue
        up = b - d * TICK
        dn = (1 - a) - d * TICK
        if up < TICK or dn < TICK or up + dn > T:
            continue
        state = {"up": up, "dn": dn, "fu": False, "fd": False}
        opened += 1
    if state is not None and (state["fu"] != state["fd"]):
        side = "UP" if state["fu"] else "DOWN"
        px = state["up"] if state["fu"] else state["dn"]
        pnl += (1 if winner == side else 0) - px
        fees += fee(px)
        lones += 1
    return pnl - fees, pairs, lones, opened, pair_costs, fees


def q(xs, p):
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    books, winners = load()
    wins, spreads, crossed, no_outcome = [], [], 0, []
    cover = []
    for slug in sorted(books):
        rows = books[slug]
        if winners.get(slug) not in ("UP", "DOWN"):
            no_outcome.append(slug)
            continue
        wins.append((slug, rows, winners[slug]))
        cover.append((rows[0][0], rows[-1][0], len(rows)))
        for t, b, a in rows:
            if b is not None and a is not None and t < ENTRY_CUT:
                if a < b:
                    crossed += 1
                spreads.append(float(a - b))
    sp = sorted(spreads)

    def maxgap(rows):
        ts = [r[0] for r in rows]
        return max([b - a for a, b in zip(ts, ts[1:])] or [0.0])
    # END_COVERED: recording reaches the completion cut with no gap > 30s, so a
    # lone leg is never an artefact of the recorder stopping. FULL: also starts
    # within the first 60s, so every entry opportunity was observable.
    endc = [w for w, c in zip(wins, cover) if c[1] >= COMPLETE_CUT and maxgap(w[1]) <= 30]
    full = [w for w, c in zip(wins, cover) if c[0] <= 60 and c[1] >= COMPLETE_CUT and maxgap(w[1]) <= 30]
    pre = [r for _, rows, _ in wins for r in rows if r[0] < ENTRY_CUT]
    one_sided = sum(1 for r in pre if (r[1] is None) != (r[2] is None))
    empty = sum(1 for r in pre if r[1] is None and r[2] is None)
    first = datetime.fromtimestamp(expiry_ts(wins[0][0]) - WINDOW_S, timezone.utc).isoformat()
    last = datetime.fromtimestamp(expiry_ts(wins[-1][0]), timezone.utc).isoformat()
    print("REPLAY, recorded GEMINI BTC15m books; hypothetical fills, NOT realized")
    print("windows with book rows %d | with venue outcome %d (UP %d / DOWN %d) | no outcome %d | end-covered %d | full-coverage %d"
          % (len(books), len(wins), sum(1 for w in wins if w[2] == "UP"),
             sum(1 for w in wins if w[2] == "DOWN"), len(no_outcome), len(endc), len(full)))
    print("rows before %ds: %d | one-sided (bid XOR ask missing) %.3f | both sides empty %.3f"
          % (ENTRY_CUT, len(pre), one_sided / len(pre), empty / len(pre)))
    print("range %s .. %s" % (first, last))
    spread_stats = {"n_rows": len(sp), "median": q(sp, .5), "p25": q(sp, .25), "p75": q(sp, .75),
                    "p90": q(sp, .9), "share_ge_0.05": sum(1 for s in sp if s >= 0.0499) / len(sp),
                    "share_ge_0.10": sum(1 for s in sp if s >= 0.0999) / len(sp), "crossed_rows": crossed}
    print("spread (yes_ask - yes_bid) before %ds: median %.3f p25 %.3f p75 %.3f p90 %.3f | >=0.05 %.3f | >=0.10 %.3f | crossed rows %d"
          % (ENTRY_CUT, spread_stats["median"], spread_stats["p25"], spread_stats["p75"], spread_stats["p90"],
             spread_stats["share_ge_0.05"], spread_stats["share_ge_0.10"], crossed))
    # per-window median spread, so a few dense WS windows do not dominate
    wmed = sorted(st.median([float(a - b) for t, b, a in r if b is not None and a is not None and t < ENTRY_CUT] or [float("nan")])
                  for _, r, _ in wins)
    wmed = [x for x in wmed if x == x]
    spread_stats["per_window_median_of_medians"] = q(wmed, .5)
    spread_stats["per_window_p90_of_medians"] = q(wmed, .9)
    print("per-window median spread: median-of-windows %.3f p90-of-windows %.3f" % (q(wmed, .5), q(wmed, .9)))

    # cross-check: fee ZERO path equals the untouched PMUS sim
    for slug, rows, win in wins:
        for d in (0, 3):
            for T in (D("0.99"), D("0.93")):
                a1 = pmus.sim(rows, win, d, T, "through", None)
                a2 = sim(rows, win, d, T, "through", None)
                assert a1[0] == a2[0] and a1[1:4] == a2[1:4], slug

    out = {"label": "REPLAY, Gemini BTC15m, hypothetical fills, NOT realized",
           "windows": len(wins), "windows_full_coverage": len(full), "range_utc": [first, last],
           "windows_end_covered": len(endc), "no_outcome": no_outcome, "spread": spread_stats,
           "pre_cut_rows": len(pre), "one_sided_share": one_sided / len(pre), "empty_share": empty / len(pre),
           "rows": []}
    configs = (("through", None), ("touch", None), ("through", D("0.99")))
    for sample_name, sample in (("ALL", wins), ("END_COVERED", endc), ("FULL_COVERAGE", full)):
        for fee_name in ("ZERO", "MAKER_RAW", "MAKER_CEIL1"):
            fee = fee_fn(fee_name)
            print("\n=== sample %s (%d windows) | fee %s ===" % (sample_name, len(sample), fee_name))
            print("%-7s %-4s %-5s %8s %8s %7s %7s %8s %8s %18s %8s" % (
                "rule", "d", "T", "opens/w", "pairs/w", "lone/w", "compl%", "paircost", "pnl/w", "95% interval", "pnl/open"))
            for rule, chase in configs:
                print("-- completion %s" % ("STATIC" if chase is None else "CHASE to pair <= %s" % chase))
                for d in (0, 2, 3, 5):
                    for T in (D("0.99"), D("0.97"), D("0.95"), D("0.93"), D("0.90")):
                        per, P, L, O, C, F, PP = [], 0, 0, 0, [], D(0), D(0)
                        for slug, rows, win in sample:
                            p, pr, lo, op, pc, fz = sim(rows, win, d, T, rule, chase, fee)
                            per.append(float(p)); P += pr; L += lo; O += op; C += pc; F += fz
                            PP += sum((1 - c for c in pc), D(0))
                        n = len(per)
                        m = st.mean(per); se = st.pstdev(per) / math.sqrt(n)
                        comp = P / (P + L) if (P + L) else float("nan")
                        row = {"sample": sample_name, "fee": fee_name, "rule": rule,
                               "chase": None if chase is None else str(chase), "depth": d, "target": str(T),
                               "opens_per_w": O / n, "pairs_per_w": P / n, "lone_per_w": L / n,
                               "completion": comp, "pair_cost": float(sum(C) / len(C)) if C else None,
                               "fees_per_w": float(F) / n, "pnl_per_w": m, "ci95": [m - 1.96 * se, m + 1.96 * se],
                               "pnl_per_open": (sum(per) / O) if O else None,
                               # decomposition, before fees: matched-pair economics vs lone legs
                               "pair_pnl_per_w": float(PP) / n,
                               "lone_pnl_per_w": (sum(per) + float(F) - float(PP)) / n,
                               "lone_pnl_per_lone": ((sum(per) + float(F) - float(PP)) / L) if L else None}
                        out["rows"].append(row)
                        print("%-7s %-4d %-5s %8.2f %8.2f %7.2f %7.1f %8s %+8.4f [%+.4f, %+.4f] %+8.4f" % (
                            rule, d, T, O / n, P / n, L / n, 100 * comp,
                            ("%.4f" % row["pair_cost"]) if C else "-", m, m - 1.96 * se, m + 1.96 * se,
                            row["pnl_per_open"] if O else float("nan")))
    if args.out:
        json.dump(out, open(args.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
