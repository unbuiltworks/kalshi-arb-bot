"""Count USABLE windows, not recorded windows.

A BTC05M window is USABLE for the BTC-state conditional-calibration test only
if it has ALL of:
  1. SETTLED  -- a settlement row with resolution_side
  2. STRIKE   -- the threshold, which the settled listing now supplies directly
  3. CLEAN CONTRACT COVERAGE -- our own event-level Gemini book spanning the
     window, with the depth-diff sequence chain intact
  4. CLEAN BTC COVERAGE -- synchronized high-frequency Coinbase state across
     the window, dense enough for the frozen momentum horizons

Reporting anything looser (e.g. "windows recorded") would overstate progress,
which is the specific mistake this script exists to prevent.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
TICK = re.compile(r"^BTC05M\d{10}$")
MIN_BOOK_COVER_S = 240.0     # of the 300 s window
MIN_CB_PER_MIN = 30.0        # Coinbase matches/min over the window
MAX_CB_GAP_S = 5.0           # largest acceptable BTC observation gap


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc).timestamp()


def main(outdir="data/gemini_infoedge"):
    settled = {}          # ticker -> row (carries strike + resolution_side)
    book = defaultdict(list)
    depth_ok = defaultdict(lambda: True)
    expiry = {}
    cb = []
    for f in sorted(Path(outdir).glob("ie_*.jsonl")):
        last_u = {}
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                t = r.get("ticker")
                if t and TICK.match(t) and r.get("resolution_side"):
                    settled[t] = r
            elif k == "cb_match":
                cb.append(r["t_ns"] / 1e9)
            elif k in ("window", "window_poll"):
                m = SYM.match(r.get("market") or "")
                if m and r.get("expiry"):
                    expiry[m.group(1)] = parse(r["expiry"])
            elif k in ("p_depth", "p_snapshot"):
                m = SYM.match(r.get("market") or "")
                if not m:
                    continue
                tk = m.group(1)
                book[tk].append(r["t_ns"] / 1e9)
                # CHAIN CHECK. The @depth20 snapshot stream and the @depth diff
                # stream are INDEPENDENT, so comparing a diff's U against a
                # snapshot's lastUpdateId marks the chain desynced constantly
                # -- the same defect already fixed once in p3_hifi_capture.py.
                # Seed from a snapshot only when unseeded, drop diffs entirely
                # behind the seed, and chain strictly on u -> U among diffs.
                if k == "p_snapshot":
                    if tk not in last_u:
                        last_u[tk] = r.get("lastUpdateId")
                    continue
                U, u = r.get("U"), r.get("u")
                pv = last_u.get(tk)
                if pv is None:
                    if u is not None:
                        last_u[tk] = u
                    continue
                if u is not None and u <= pv:
                    continue
                if U is not None and U != pv:
                    depth_ok[tk] = False
                if u is not None:
                    last_u[tk] = u
    cb.sort()

    rows = []
    for tk, s in settled.items():
        exp = expiry.get(tk)
        if exp is None and s.get("expiry"):
            exp = parse(s["expiry"])
        if exp is None:
            continue
        start = exp - 300.0
        bt = sorted(t for t in book.get(tk, []) if start <= t <= exp)
        cover = (bt[-1] - bt[0]) if len(bt) > 1 else 0.0
        seg = [t for t in cb if start <= t <= exp]
        rate = len(seg) / 5.0 if seg else 0.0
        gap = max((b - a for a, b in zip(seg, seg[1:])), default=999.0)
        has_strike = s.get("strike") not in (None, "")
        ok = (has_strike and cover >= MIN_BOOK_COVER_S and depth_ok[tk]
              and rate >= MIN_CB_PER_MIN and gap <= MAX_CB_GAP_S)
        rows.append({"ticker": tk, "expiry": exp, "settled": True,
                     "strike": has_strike,
                     "resolution": s.get("resolution_side"),
                     "source": s.get("source"),
                     "book_cover_s": round(cover, 1),
                     "chain_intact": depth_ok[tk],
                     "cb_per_min": round(rate, 1),
                     "cb_max_gap_s": round(gap, 2),
                     "USABLE": ok})

    rows.sort(key=lambda r: r["expiry"])
    usable = [r for r in rows if r["USABLE"]]
    Path("analysis/gemini/info_edge/ie_usable_windows.json").write_text(
        json.dumps(rows, indent=1))

    print("=" * 72)
    print("USABLE-WINDOW COUNT (clean + strike-bearing + settled)")
    print("=" * 72)
    print(f"settlement rows with resolution_side : {len(settled)}")
    print(f"  of those, strike present           : {sum(1 for r in rows if r['strike'])}")
    print(f"  with our book cover >= {int(MIN_BOOK_COVER_S)} s        : "
          f"{sum(1 for r in rows if r['book_cover_s'] >= MIN_BOOK_COVER_S)}")
    print(f"  with depth chain intact            : {sum(1 for r in rows if r['chain_intact'])}")
    print(f"  with Coinbase >= {int(MIN_CB_PER_MIN)}/min, gap <= {int(MAX_CB_GAP_S)} s : "
          f"{sum(1 for r in rows if r['cb_per_min'] >= MIN_CB_PER_MIN and r['cb_max_gap_s'] <= MAX_CB_GAP_S)}")
    print()
    print(f"  >>> USABLE WINDOWS: {len(usable)} <<<")
    print()
    if usable:
        import math
        se = 0.5 / math.sqrt(len(usable))
        print(f"  implied clustered se on a settlement frequency : {se:.4f}")
        print(f"  target se 0.03 needs ~{int((0.5/0.03)**2)} usable windows")
        src = defaultdict(int)
        for r in usable:
            src[r["source"]] += 1
        print(f"  settlement sources in usable set : {dict(src)}")
        res = defaultdict(int)
        for r in usable:
            res[r["resolution"]] += 1
        print(f"  resolution split                 : {dict(res)}")
    for mark in (100, 200, 300):
        print(f"  milestone {mark}: "
              f"{'REACHED' if len(usable) >= mark else f'{mark-len(usable)} to go'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or ["data/gemini_infoedge"])))
