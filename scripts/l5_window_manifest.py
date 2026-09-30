"""Window manifest: which raw windows are USABLE for replay, and why not.

Completeness is NOT redefined here. It is delegated to the rules that already
refused a partial window on this branch:

  * `src.l5_settled_scoreboard.assert_complete` -- full window coverage and no
    observation gap over MAX_GAP_S;
  * `src.l5_settled_scoreboard.assert_settled` -- an authoritative up/down
    winner, where a terminal `last` price is explicitly not settlement;
  * `src.l5_settlement_source.winner_from_bbo` -- the venue's settlementPx on a
    market it reports settled, refusing a fractional payout.

PROVENANCE. The replay input is built from RAW recorder records, never from the
normalized trade file: that file de-duplicates identical prints, which can drop
legitimate separate trades at the same price and size, and a queue-aware fill
model consumes exactly those prints. Every manifest row names the raw source
files it was built from.

    python3 scripts/l5_window_manifest.py <raw.jsonl[.gz]> [more...]
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from src import l5_settled_scoreboard as SB                    # noqa: E402
from src import l5_settlement_source as SS                     # noqa: E402
from scripts.l5_slice_window import _ep, row_slug              # noqa: E402

GATEWAY = "https://gateway.polymarket.us"


def _open(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return io.open(path, "rt", encoding="utf-8", errors="replace")


def scan(paths: List[str]) -> Dict[str, Dict[str, Any]]:
    """Group raw rows by slug, counting books and prints and tracking gaps."""
    windows: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        with _open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:                            # noqa: BLE001
                    continue
                slug = row_slug(row)
                t = _ep(row.get("t"))
                if not slug or t is None:
                    continue
                w = windows.setdefault(slug, {
                    "slug": slug, "sources": set(), "ts": [],
                    "book_updates": 0, "prints": 0})
                w["sources"].add(os.path.basename(path))
                w["ts"].append(t)
                msg = row.get("msg") or {}
                if isinstance(msg.get("marketData"), dict):
                    w["book_updates"] += 1
                if isinstance(msg.get("trade"), dict):
                    w["prints"] += 1
    return windows


def settlement_for(slug: str) -> Dict[str, Any]:
    """The venue's own settlement, or an explicit refusal. Public GET."""
    import subprocess
    try:
        raw = subprocess.run(
            ["curl", "-sS", "--max-time", "20",
             f"{GATEWAY}/v1/markets/{slug}/bbo"],
            capture_output=True, text=True).stdout
        bbo = json.loads(raw)
    except Exception as exc:                                 # noqa: BLE001
        return {"source": "venue_bbo", "winner": None,
                "why": f"{type(exc).__name__}: {str(exc)[:80]}"}
    try:
        return {"source": "venue settlementPx", "winner": SS.winner_from_bbo(bbo)}
    except SS.SettlementUnavailable as exc:
        return {"source": "venue settlementPx", "winner": None,
                "why": str(exc)[:160]}


def window_start_from_slug(slug: str):
    import datetime as dt
    import re
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})z$", slug)
    if not m:
        return None
    y, mo, d, hh, mi = (int(g) for g in m.groups())
    return dt.datetime(y, mo, d, hh, mi, tzinfo=dt.timezone.utc).timestamp()


def manifest(paths: List[str]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for slug, w in sorted(scan(paths).items()):
        ts = sorted(w["ts"])
        start = window_start_from_slug(slug)
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        row: Dict[str, Any] = {
            "slug": slug,
            "window_start": start,
            "window_end": (start + SB.WINDOW_S) if start else None,
            "raw_sources": sorted(w["sources"]),
            "first_receipt": ts[0] if ts else None,
            "last_receipt": ts[-1] if ts else None,
            "max_observation_gap_s": round(max(gaps), 3) if gaps else None,
            "book_updates": w["book_updates"],
            "prints": w["prints"],
        }
        if start is None:
            row.update(complete=False, exclusion="slug carries no window time")
            rows.append(row)
            continue
        events = [(t, "book", {}) for t in ts]
        try:
            SB.assert_complete(events, window_start=start)
            coverage_ok, why = True, None
        except SB.WindowNotComplete as exc:
            coverage_ok, why = False, str(exc)
        st = settlement_for(slug)
        row["settlement_source"] = st["source"]
        row["winner"] = st["winner"]
        settled_ok = st["winner"] in ("up", "down")
        row["complete"] = bool(coverage_ok and settled_ok)
        if not row["complete"]:
            row["exclusion"] = "; ".join(
                x for x in (why, (None if settled_ok
                                  else st.get("why", "no authoritative winner")))
                if x)
        rows.append(row)
    usable = [r for r in rows if r["complete"]]
    return {"windows": rows, "usable": len(usable),
            "usable_slugs": [r["slug"] for r in usable],
            "total_seen": len(rows),
            "completeness_rules": ("src.l5_settled_scoreboard.assert_complete "
                                   "+ assert_settled + "
                                   "l5_settlement_source.winner_from_bbo"),
            "provenance": "built from RAW recorder records, not the "
                          "de-duplicated normalized trade file"}


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = manifest(argv[1:])
    print(json.dumps(out, indent=1, default=str))
    print(f"\nUSABLE WINDOWS: {out['usable']} of {out['total_seen']}")
    return 0 if out["usable"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
