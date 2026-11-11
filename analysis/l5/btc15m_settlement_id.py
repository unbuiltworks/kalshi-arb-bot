#!/usr/bin/env python3
"""Identify the BTC15m settlement functional from resolved outcomes.

Research only; reads committed corpora, opens no socket, places no order.

WHY THIS EXISTS. The market description tells us the resolution SOURCE but
not, reliably, the FUNCTIONAL. For windows on `btc-usd-twap-60s-streams` it
says the comparison uses "the time-weighted average price (TWAP) of
Bitcoin ... of the time range specified in the title", which read literally
means an average over the whole 900 s. Measured, that reading is the worst of
the candidates in BOTH reference regimes.

HOW THIS IDENTIFIES RATHER THAN FITS. Polymarket switched streams during the
corpus: `btc-usd` windows run to 2026-08-04 and `btc-usd-twap-60s-streams`
windows from 2026-08-11. The two groups are scored SEPARATELY with the SAME
candidate set, and the winner inverts between them, each time landing on the
functional its named stream implies:

    btc-usd            terminal close vs window open      best
    twap-60s-streams   trailing-60s mean at each endpoint  best

A functional chosen by overfitting would not reverse its ranking across a
venue change it was never told about. Both winners beat the description's
literal reading by 12.8 and 23.0 points respectively.

Run: PYTHONPATH=. python3 analysis/l5/btc15m_settlement_id.py
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import os
import re
import sys
from typing import Any, Callable, Dict, List, Sequence, Tuple

from analysis.l5.btc15m_asof import (MINUTE_S, SETTLE_AVERAGE_S, SPOT_SOURCE,
                                     TWAP_SOURCE, WINDOW_S, MinuteSeries)
from analysis.l5.btc15m_calibration_report import load_candles

SLUG_RE = re.compile(r"^btc-updown-15m-\d+$")
CAPTURES = ["data/l5/raw/markets.jsonl"] + sorted(
    glob.glob("data/traders/*/raw/markets.jsonl"))


def load_all_resolved(paths: Sequence[str] = tuple(CAPTURES)) -> Dict[str, Tuple[str, str]]:
    """{slug: (resolutionSource, winner)} across every capture we hold.

    A slug captured twice with conflicting winners is DROPPED, not resolved
    by preferring one capture.
    """
    out: Dict[str, Tuple[str, str]] = {}
    bad: set = set()
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                rows = env.get("payload")
                if not isinstance(rows, list):
                    continue
                for m in rows:
                    if not isinstance(m, dict):
                        continue
                    slug = str(m.get("slug") or "")
                    if not SLUG_RE.match(slug):
                        continue
                    if str(m.get("umaResolutionStatus") or "").lower() != "resolved":
                        continue
                    try:
                        outs, px = m.get("outcomes"), m.get("outcomePrices")
                        if isinstance(outs, str):
                            outs = json.loads(outs)
                        if isinstance(px, str):
                            px = json.loads(px)
                        pair = {str(a): float(b) for a, b in zip(outs, px)}
                    except Exception:                          # noqa: BLE001
                        continue
                    if sorted(pair.values()) != [0.0, 1.0]:
                        continue
                    win = [a for a, b in pair.items() if b == 1.0][0]
                    src = str(m.get("resolutionSource") or "")
                    prev = out.get(slug)
                    if prev and prev != (src, win):
                        bad.add(slug)
                    out[slug] = (src, win)
    for slug in bad:
        out.pop(slug, None)
    return out


# Candidate functionals. Each returns (settled_proxy, strike_proxy) and the
# window resolves Up when settled >= strike (ties resolve Up: the market says
# "greater than or equal to").
def candidates(series: MinuteSeries) -> Dict[str, Callable[[float], Tuple[float, float]]]:
    def m(t: float):
        c = series.minute_starting(t)
        if c is None:
            raise KeyError(t)
        return c
    last = WINDOW_S - SETTLE_AVERAGE_S            # start of the final minute
    return {
        "terminal_close_vs_window_open":
            lambda ws: (m(ws + last).close, m(ws).open),
        "trailing60s_mean_both_endpoints":
            lambda ws: (m(ws + last).mean, m(ws - MINUTE_S).mean),
        "window_wide_mean_vs_open":
            lambda ws: (sum(m(ws + MINUTE_S * i).close for i in range(15)) / 15.0,
                        m(ws).open),
        "terminal_mean_vs_window_open":
            lambda ws: (m(ws + last).mean, m(ws).open),
        "terminal_close_vs_prior_minute_close":
            lambda ws: (m(ws + last).close, m(ws - MINUTE_S).close),
    }


def identify(series: MinuteSeries,
             resolved: Dict[str, Tuple[str, str]]) -> Dict[str, Any]:
    cands = candidates(series)
    by_src: Dict[str, List[Tuple[float, str]]] = collections.defaultdict(list)
    for slug, (src, win) in resolved.items():
        ws = float(slug.rsplit("-", 1)[1])
        by_src[src.rstrip("/").rsplit("/", 1)[-1]].append((ws, win))

    out: Dict[str, Any] = {"captures": list(CAPTURES),
                           "resolved_windows": len(resolved), "by_source": {}}
    for src, rows in sorted(by_src.items()):
        scored: Dict[str, Dict[str, Any]] = {}
        covered: List[Tuple[float, str]] = []
        for ws, win in rows:
            try:
                for f in cands.values():
                    f(ws)
            except KeyError:
                continue
            covered.append((ws, win))
        for name, f in cands.items():
            ok = 0
            for ws, win in covered:
                settled, k = f(ws)
                if ("Up" if settled >= k else "Down") == win:
                    ok += 1
            scored[name] = {"agree": ok, "n": len(covered),
                            "rate": (ok / len(covered)) if covered else None}
        best = max(scored, key=lambda k: scored[k]["agree"]) if covered else None
        out["by_source"][src] = {
            "windows_resolved": len(rows), "windows_covered": len(covered),
            "span": [dt.datetime.fromtimestamp(min(w for w, _ in covered), dt.UTC).isoformat(),
                     dt.datetime.fromtimestamp(max(w for w, _ in covered), dt.UTC).isoformat()]
                    if covered else None,
            "best": best, "scored": scored}
    return out


def main() -> int:
    series = load_candles()
    resolved = load_all_resolved()
    out = identify(series, resolved)
    os.makedirs("analysis/l5/btc15m_signal", exist_ok=True)
    with open("analysis/l5/btc15m_signal/settlement_id.json", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print("resolved BTC15m windows across %d captures: %d"
          % (len(out["captures"]), out["resolved_windows"]))
    for src, d in out["by_source"].items():
        if not d["windows_covered"]:
            print("\n=== %s : %d resolved, 0 with reference coverage"
                  % (src, d["windows_resolved"]))
            continue
        print("\n=== %s : %d covered of %d resolved, %s -> %s"
              % (src, d["windows_covered"], d["windows_resolved"],
                 d["span"][0][:10], d["span"][1][:10]))
        for name in sorted(d["scored"], key=lambda k: -d["scored"][k]["agree"]):
            s = d["scored"][name]
            print("   %-42s %4d/%d = %.1f%%%s"
                  % (name, s["agree"], s["n"], 100 * s["rate"],
                     "   <-- best" if name == d["best"] else ""))
    print("\nwrote analysis/l5/btc15m_signal/settlement_id.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
