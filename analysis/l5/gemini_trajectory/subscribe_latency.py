"""Decompose GEMINI_WINDOW_CLOSE digests into the subscribe-path latencies.

THE MEASURE THAT MATTERS IS `expiry - first_frame_at`, NOT `tte_first_any`.
`tte_first_any` is clamped to the window duration by construction, so for a
5m window it can never exceed 300 however early we attach -- using it to
verify an earlier subscribe would silently cap the very number under test.

Reads digest lines (one per line, with or without the GEMINI_WINDOW_CLOSE
prefix) from files or stdin. Read-only; computes nothing from settlement.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional

PREFIX = "GEMINI_WINDOW_CLOSE "
# the collector's intended lead and the FULL requirement, for reference only
INTENDED_LEAD_S = 330.0
FULL_MIN_SPAN_S = 290.0


def iter_digests(lines: Iterable[str]) -> Iterable[Dict[str, Any]]:
    for line in lines:
        i = line.find(PREFIX)
        if i >= 0:
            line = line[i + len(PREFIX):]
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and "market" in d and "expiry_t" in d:
            yield d


def first_frame_tte(d: Mapping[str, Any]) -> Optional[float]:
    """Unclamped TTE of the first frame. None when UNKNOWN -- never 0."""
    exp, ff = d.get("expiry"), d.get("first_frame_at")
    if exp is None:
        exp = d.get("expiry_t")
    if exp is None or ff is None:
        return None
    return round(float(exp) - float(ff), 2)


def rows(digests: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for d in digests:
        out.append({
            "market": d.get("market"),
            "asset": d.get("asset"),
            "window_minutes": d.get("window_minutes"),
            "expiry_t": d.get("expiry_t"),
            "first_frame_tte": first_frame_tte(d),
            "tte_first_any": d.get("tte_first_any"),
            "span_s": d.get("span_s"),
            "span_any_s": d.get("span_any_s"),
            "complete": d.get("complete"),
            "two_sided_pct": d.get("two_sided_pct"),
            "lat_discovery_to_due_s": d.get("lat_discovery_to_due_s"),
            "lat_due_to_subscribe_s": d.get("lat_due_to_subscribe_s"),
            "lat_subscribe_to_ack_s": d.get("lat_subscribe_to_ack_s"),
            "lat_ack_to_first_frame_s": d.get("lat_ack_to_first_frame_s"),
            "discovery_tick_index": d.get("discovery_tick_index"),
            "session_index": d.get("session_index"),
        })
    out.sort(key=lambda r: (r["expiry_t"] or 0, r["market"] or ""))
    return out


def _fmt(v: Any, w: int = 9, nd: int = 2) -> str:
    if v is None:
        return "UNKNOWN".rjust(w)
    if isinstance(v, bool):
        return ("YES" if v else "no").rjust(w)
    if isinstance(v, (int, float)):
        return f"{v:{w}.{nd}f}"
    return str(v).rjust(w)


def report(rs: List[Dict[str, Any]]) -> str:
    lines = []
    hdr = (f"{'market':<26}{'ff_tte':>9}{'disc>due':>10}{'due>sub':>9}"
           f"{'sub>ack':>9}{'ack>ff':>9}{'span_any':>10}{'span':>9}"
           f"{'FULL':>6}{'sess':>6}")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in rs:
        lines.append(
            f"{str(r['market']).replace('GEMI-', '')[:26]:<26}"
            f"{_fmt(r['first_frame_tte'])}"
            f"{_fmt(r['lat_discovery_to_due_s'], 10)}"
            f"{_fmt(r['lat_due_to_subscribe_s'])}"
            f"{_fmt(r['lat_subscribe_to_ack_s'])}"
            f"{_fmt(r['lat_ack_to_first_frame_s'])}"
            f"{_fmt(r['span_any_s'], 10)}"
            f"{_fmt(r['span_s'])}"
            f"{_fmt(r['complete'], 6)}"
            f"{_fmt(r['session_index'], 6, 0)}")
    known = [r["first_frame_tte"] for r in rs if r["first_frame_tte"] is not None]
    lines.append("")
    lines.append(f"windows: {len(rs)}  first_frame_tte known: {len(known)}")
    if known:
        lines.append(f"  first_frame_tte  min {min(known):.2f}  max {max(known):.2f}")
        lines.append(f"  >300 s (beats the FULL requirement): "
                     f"{sum(1 for v in known if v > 300.0)}/{len(known)}")
        lines.append(f"  >={INTENDED_LEAD_S:.0f} s (full intended lead):  "
                     f"{sum(1 for v in known if v >= INTENDED_LEAD_S)}/{len(known)}")
    full = [r for r in rs if r["complete"]]
    lines.append(f"  naturally COMPLETE (span_s >= dur-10): {len(full)}/{len(rs)}")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        src: Iterable[str] = []
        for p in argv[1:]:
            with open(p, encoding="utf-8") as fh:
                src = list(src) + fh.readlines()
    else:
        src = sys.stdin
    print(report(rows(iter_digests(src))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
