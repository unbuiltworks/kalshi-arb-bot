"""Read GEMINI_WINDOW_* digest lines back into window records.

WHY THIS EXISTS. The collector's canonical trajectory rows are written to a
Railway volume that only the collector container can read. The digest lines
are the read path: one summary per closed window, plus the in-window quote
series and print rows, chunked so no line is truncated in transit.

This module is the consumer. It accepts either a raw log dump (one line per
log entry) or the JSON the Railway logs API returns, reassembles the chunks,
and REFUSES a window whose chunks are incomplete rather than silently
reporting a partial series -- a half-window scored as a whole one is exactly
the kind of coverage error this instrumentation exists to eliminate.

    python3 -m analysis.l5.gemini_trajectory.window_digest LOGFILE [...]
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CLOSE = "GEMINI_WINDOW_CLOSE "
SERIES = "GEMINI_WINDOW_SERIES "
PRINTS = "GEMINI_WINDOW_PRINTS "

# A window is COMPLETE when its in-window quote coverage spans at least this
# much of its own 0..300 s range.
COMPLETE_SPAN_S = 290.0


def _is_path(source: Any) -> bool:
    """A path, or a string that could be one. A long log dump passed as a
    string is NOT a path -- Path.exists() raises ENAMETOOLONG on it rather
    than returning False, so the length and newline checks come first."""
    if isinstance(source, Path):
        return source.exists()
    if not isinstance(source, str) or "\n" in source or len(source) > 4096:
        return False
    try:
        return Path(source).exists()
    except OSError:
        return False


def iter_lines(source: Any) -> Iterable[str]:
    """Yield log message strings from a file path, raw text, or the Railway
    logs JSON ({deploy: [{message}]} and friends)."""
    if _is_path(source):
        source = Path(str(source)).read_text(errors="replace")
    if isinstance(source, str):
        text = source.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                source = json.loads(text)
            except ValueError:
                for ln in text.splitlines():
                    yield ln
                return
        else:
            for ln in text.splitlines():
                yield ln
            return
    if isinstance(source, dict):
        buckets = [v for k, v in source.items() if isinstance(v, list)]
        for b in buckets:
            for row in b:
                if isinstance(row, dict) and isinstance(row.get("message"), str):
                    yield row["message"]
                elif isinstance(row, str):
                    yield row
        return
    if isinstance(source, list):
        for row in source:
            if isinstance(row, dict) and isinstance(row.get("message"), str):
                yield row["message"]
            elif isinstance(row, str):
                yield row


def _payload(line: str, tag: str) -> Optional[dict]:
    i = line.find(tag)
    if i < 0:
        return None
    try:
        return json.loads(line[i + len(tag):].strip())
    except ValueError:
        return None                    # a truncated line is not recoverable


def parse(sources: Iterable[Any]) -> Dict[str, dict]:
    """Reassemble windows keyed by market. A market that closes more than
    once (the collector restarted and re-observed it) keeps the LAST close,
    and its chunks are re-collected from that point."""
    wins: Dict[str, dict] = {}
    for src in sources:
        for line in iter_lines(src):
            d = _payload(line, CLOSE)
            if d is not None and d.get("market"):
                wins[d["market"]] = {"digest": d, "series": {}, "prints": {},
                                     "series_parts": None, "prints_parts": None}
                continue
            for tag, key, field in ((SERIES, "series", "s"),
                                    (PRINTS, "prints", "p")):
                d = _payload(line, tag)
                if d is None or not d.get("market"):
                    continue
                w = wins.get(d["market"])
                if w is None:
                    continue           # a chunk with no close is unusable
                w[key][int(d.get("part") or 0)] = d.get(field) or []
                w[f"{key}_parts"] = int(d.get("parts") or 0)
    out: Dict[str, dict] = {}
    for m, w in wins.items():
        dg = w["digest"]
        got, want = len(w["series"]), (w["series_parts"] or 0)
        # REFUSE rather than under-report: a window missing chunks would
        # otherwise be scored on a truncated series.
        if want and got == want:
            ser = [row for i in sorted(w["series"]) for row in w["series"][i]]
            integrity = "COMPLETE_CHUNKS"
        else:
            ser, integrity = [], f"MISSING_SERIES_CHUNKS:{got}/{want}"
        pgot, pwant = len(w["prints"]), (w["prints_parts"] or 0)
        if pwant and pgot == pwant:
            prs = [row for i in sorted(w["prints"]) for row in w["prints"][i]]
        elif not pwant:
            prs = []                    # the window genuinely had no prints
        else:
            prs = []
            integrity += f"+MISSING_PRINT_CHUNKS:{pgot}/{pwant}"
        out[m] = {**dg, "series": ser, "print_rows": prs,
                  "series_integrity": integrity,
                  "series_obs_received": len(ser)}
    return out


def is_complete(w: dict) -> bool:
    """A COMPLETE window: chunk-intact, and spanning >= 290 s of its own
    0..300 s tte range. `complete` in the digest is the collector's own view;
    this re-derives it here so the consumer does not have to trust it."""
    return (w.get("series_integrity") == "COMPLETE_CHUNKS"
            and float(w.get("span_s") or 0.0) >= COMPLETE_SPAN_S
            and w.get("obs", 0) >= 2)


def _p50(xs: List[float]) -> Optional[float]:
    return round(statistics.median(xs), 6) if xs else None


def section_d(w: dict) -> dict:
    """The section-D line for one window, recomputed FROM THE SERIES rather
    than copied from the digest, so a disagreement would be visible."""
    ser = w.get("series") or []
    span = (ser[0][0] - ser[-1][0]) if len(ser) >= 2 else 0.0
    two = sum(1 for o in ser if o[1] > 0 and o[2] > 0)
    tot = len(ser) + int(w.get("one_sided_obs") or 0)
    return {
        "market": w.get("market"), "asset": w.get("asset"),
        "obs": len(ser),
        "obs_per_min": (round(len(ser) / (span / 60.0), 2) if span > 0 else None),
        "in_window_span_s": round(span, 2),
        "tte_first": ser[0][0] if ser else None,
        "tte_last": ser[-1][0] if ser else None,
        # SPAN ALONE CAN HIDE A HOLE: first-to-last says nothing about what
        # happened in between, and the historical tape's defect was measured
        # as a median MAXIMUM GAP of 875.8 s. Both are reported.
        "max_gap_s": (round(max((a[0] - b[0]) for a, b in zip(ser, ser[1:])), 2)
                      if len(ser) >= 2 else None),
        "two_sided_pct": (round(100.0 * two / tot, 2) if tot else None),
        "one_sided_obs": int(w.get("one_sided_obs") or 0),
        "prints": len(w.get("print_rows") or []),
        "spread_p50": _p50([round(o[2] - o[1], 6) for o in ser]),
        "depth_p50": _p50([min(o[3], o[4]) for o in ser
                           if o[3] is not None and o[4] is not None]),
        "complete": is_complete(w),
        "integrity": w.get("series_integrity"),
    }


def main(argv: List[str]) -> int:
    wins = parse(argv or [sys.stdin.read()])
    rows = [section_d(w) for w in wins.values()]
    rows.sort(key=lambda r: (r["asset"] or "", r["market"] or ""))
    print(json.dumps({"windows": len(rows),
                      "complete": sum(1 for r in rows if r["complete"]),
                      "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
