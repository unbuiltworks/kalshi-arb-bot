"""Measurement-validity gates for the BTC15m two-sided recorder tape.

READ-ONLY. Run this BEFORE reading any economic number off a BTC15m tape.

Three gates, each of which can invalidate a fill or profitability conclusion:

G1  WINDOW COVERAGE. The continuous-inventory policy quotes in the TTE band
    [120, 840] of a 900 s window. This gate measures the band fraction the
    tape actually observed. `btc5m_recorder.SUBSCRIBE_LEAD_S = 420.0` is
    inherited unchanged by `btc15m_recorder`, so streams open at TTE 420 and
    the early 58% of every window is ABSENT -- not thin, absent. A fill study
    run on such a tape measures the late window only.

G2  PRINT CAPTURE. Compares captured `p_trade` rows against the number of
    times the REST `lastTradePrice` changed for the same market inside the
    same streamed interval. `lastTradePrice` changes are themselves a LOWER
    bound on trades (consecutive prints at one price do not move it), so a
    shortfall here is a floor on how much print flow the tape is missing.

G3  SETTLEMENT RESOLVABILITY. How many windows can be given a settlement side
    from tape evidence, and by what provenance. The canonical engine reads
    `settled["resolution_side"]` and emits NO settlement block when it is
    absent, so an unresolvable window silently contributes 0 to every total.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import statistics
import sys
from collections import defaultdict
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settlement_bridge as sb  # noqa: E402
from maker_arms import OPEN_TTE_HI, OPEN_TTE_LO, SYM, load  # noqa: E402

WINDOW_S = 900.0
RECORDER_SUBSCRIBE_LEAD_S = 420.0   # btc5m_recorder.SUBSCRIBE_LEAD_S, inherited


def _rows(tape_dir: str):
    files = sorted(glob.glob(os.path.join(tape_dir, "*.jsonl"))) + \
        sorted(glob.glob(os.path.join(tape_dir, "*.jsonl.gz")))
    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except Exception:
                    continue


def audit(tape_dir: str, ticker_prefix: str = "BTC15M") -> Dict[str, Any]:
    ws = load(tape_dir)
    ws = {k: v for k, v in ws.items() if k.startswith(ticker_prefix)}

    # ---- G1 window coverage
    tte_book, tte_print, per_window = [], [], {}
    for tk, w in sorted(ws.items()):
        E = w["expiry"]
        b = [E - e["t"] for e in w["timeline"] if e["type"] == "book"]
        p = [E - e["t"] for e in w["timeline"] if e["type"] == "print"]
        if not b:
            continue
        tte_book += b
        tte_print += p
        per_window[tk] = {"book_events": len(b), "prints": len(p),
                          "max_tte": round(max(b), 1),
                          "min_tte": round(min(b), 1)}
    band = OPEN_TTE_HI - OPEN_TTE_LO
    observed_hi = min(max(tte_book) if tte_book else 0.0, OPEN_TTE_HI)
    covered = max(0.0, observed_hi - OPEN_TTE_LO)
    g1 = {
        "policy_band_tte": [OPEN_TTE_LO, OPEN_TTE_HI],
        "policy_band_seconds": band,
        "max_observed_tte_s": round(max(tte_book), 1) if tte_book else None,
        "book_events": len(tte_book),
        "book_events_above_recorder_lead": sum(
            1 for x in tte_book if x > RECORDER_SUBSCRIBE_LEAD_S),
        "prints_above_recorder_lead": sum(
            1 for x in tte_print if x > RECORDER_SUBSCRIBE_LEAD_S),
        "band_seconds_observed": round(covered, 1),
        "band_fraction_observed": round(covered / band, 4) if band else None,
        "unobserved_band_tte": [round(observed_hi, 1), OPEN_TTE_HI],
        "verdict": ("PASS" if covered / band >= 0.95 else
                    "FAIL_EARLY_WINDOW_ABSENT"),
        "reading": ("Streams open at btc5m SUBSCRIBE_LEAD_S=420 s, inherited "
                    "unchanged by the 15m recorder. A 900 s window's early "
                    "TTE 840->420 is never recorded, so no fill, print or "
                    "pair-completion statistic from this tape describes it."),
    }

    # ---- G2 print capture
    stream_span: Dict[str, list] = defaultdict(lambda: [None, None])
    captured: Dict[str, int] = defaultdict(int)
    polls: Dict[str, list] = defaultdict(list)
    for r in _rows(tape_dir):
        m = SYM.match(r.get("market") or "")
        if not m:
            continue
        s = r["market"]
        k = r.get("kind")
        if k in ("p_snapshot", "p_depth"):
            sp = stream_span[s]
            if sp[0] is None:
                sp[0] = r["t_ns"]
            sp[1] = r["t_ns"]
        elif k == "p_trade":
            captured[s] += 1
        elif k in ("window", "window_poll"):
            polls[s].append((r["t_ns"],
                             (r.get("prices") or {}).get("lastTradePrice")))
    cap = implied = 0
    for s, (a, b) in stream_span.items():
        if a is None:
            continue
        prev, ch = None, 0
        for t, v in sorted(polls.get(s, [])):
            if a <= t <= b:
                if prev is not None and v != prev:
                    ch += 1
                prev = v
        cap += captured[s]
        implied += ch
    g2 = {
        "captured_p_trade_rows": cap,
        "rest_lastTradePrice_changes_in_same_streamed_intervals": implied,
        "capture_ratio_upper_bound": (round(cap / implied, 3)
                                      if implied else None),
        "verdict": ("PASS" if implied and cap >= implied else
                    "FAIL_PRINT_TAPE_INCOMPLETE"),
        "reading": ("lastTradePrice changes are a LOWER bound on trades, so a "
                    "ratio below 1.0 means the print tape is missing flow and "
                    "every evidenced-fill count from it is a lower bound."),
    }

    # ---- G3 settlement resolvability
    tbl = {k: v for k, v in sb.table(tape_dir).items()
           if k.startswith(ticker_prefix)}
    prov: Dict[str, int] = defaultdict(int)
    for v in tbl.values():
        prov[v["provenance"]] += 1
    resolved = sum(1 for v in tbl.values() if v["resolution_side"])
    g3 = {
        "tickers_seen": len(tbl), "resolved": resolved,
        "unresolved": len(tbl) - resolved,
        "by_provenance": dict(prov),
        "windows_with_book_and_resolution": sum(
            1 for tk in ws if tbl.get(tk, {}).get("resolution_side")),
        "windows_with_book": len(ws),
        "verdict": ("PASS" if resolved == len(tbl) else
                    "PARTIAL_SOME_WINDOWS_UNRESOLVABLE"),
        "reading": ("btc15m_shadow_policy emits no settlement block when "
                    "resolution_side is absent, so an unresolvable window "
                    "contributes 0 to every economic total rather than "
                    "failing closed."),
    }

    return {"label": "BTC15M_TAPE_MEASUREMENT_VALIDITY_V1",
            "tape_dir": tape_dir,
            "accounting": "public market-data audit; nothing is realized",
            "G1_window_coverage": g1,
            "G2_print_capture": g2,
            "G3_settlement_resolvability": g3,
            "per_window": per_window,
            "median_book_event_tte_s": round(statistics.median(tte_book), 1)
            if tte_book else None}


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else "data/gemini_infoedge_15m"
    out = sys.argv[2] if len(sys.argv) > 2 else None
    a = audit(d)
    for g in ("G1_window_coverage", "G2_print_capture",
              "G3_settlement_resolvability"):
        print(f"--- {g}: {a[g]['verdict']}")
        for k, v in a[g].items():
            if k not in ("verdict", "reading"):
                print(f"      {k}: {v}")
        print(f"      reading: {a[g]['reading']}")
    if out:
        with open(out, "w") as fh:
            json.dump(a, fh, indent=2, default=str)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
