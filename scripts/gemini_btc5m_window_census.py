"""How many GENUINELY COMPLETE BTC5m windows does a tape set contain?

The T1 gate, reported BEFORE any analysis result. A window counts only when
ALL FIVE requirements are present, and each is checked against the tape
rather than assumed:

  1. the early checkpoints the preregistration names (T+15..T+180);
  2. an AUTHORITATIVE strike (the contract's own `strike`, strike_type
     reference) -- never inferred;
  3. CONTEMPORANEOUS reference observations covering those checkpoints, with
     the last published value at or before each one no more than
     MAX_REF_AGE_S old;
  4. a settlement;
  5. executable books at those checkpoints (a ladder, not a touch price).

Partial windows are counted under the requirement they are missing, so a
shrinking denominator is always attributable. Read-only; it opens no venue
surface and changes nothing.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import glob
import gzip
import json
from typing import Any, Dict, List, Optional

LABEL = "GEMINI_BTC5M_WINDOW_CENSUS"
EARLY = (15, 30, 45, 60, 90, 120, 150, 180)
MAX_REF_AGE_S = 10.0
#: a window is COMPLETE on the early grid when at least this many of the
#: eight checkpoints are present with everything else. Stated up front.
MIN_EARLY_CHECKPOINTS = 6


def rows(pattern: str) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(glob.glob(pattern)):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except Exception:                              # noqa: BLE001
                    continue
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rs = rows(a.tapes)

    ref = sorted((float(r["parsed_event_time"]), r)
                 for r in rs if r.get("kind") == "index_price"
                 and r.get("parsed_event_time") is not None)
    ref_t = [t for t, _ in ref]

    strikes: Dict[str, Any] = {}
    cps: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    settles: Dict[str, Any] = {}
    for r in rs:
        tk = r.get("ticker")
        if not tk or not str(tk).startswith("BTC05M"):
            continue
        if r.get("kind") == "window" and r.get("strike"):
            strikes[tk] = {"strike": r["strike"],
                           "strike_type": r.get("strike_type")}
        elif r.get("kind") == "checkpoint":
            if r.get("checkpoint_family") == "EARLY_FROM_T0":
                cps[tk].append(r)
        elif r.get("kind") == "settlement":
            ev = r.get("raw_event") or {}
            side = None
            for c in (ev.get("contracts") or []):
                if str(c.get("label", "")).lower() == "up":
                    side = c.get("resolutionSide")
            if side in ("yes", "no"):
                settles[tk] = side

    missing = collections.Counter()
    complete: List[Dict[str, Any]] = []
    seen = set(list(strikes) + list(cps) + list(settles))
    for tk in sorted(seen):
        why = []
        if tk not in strikes:
            why.append("no_authoritative_strike")
        if tk not in settles:
            why.append("no_settlement")
        got = [c for c in cps.get(tk, [])
               if c.get("checkpoint_s") in EARLY]
        if len(got) < MIN_EARLY_CHECKPOINTS:
            why.append("fewer_than_%d_early_checkpoints" % MIN_EARLY_CHECKPOINTS)
        books = [c for c in got
                 if (c.get("yes_ladder") or {}).get("asks") is not None
                 and (c.get("yes_ladder") or {}).get("bids") is not None]
        if len(books) < MIN_EARLY_CHECKPOINTS:
            why.append("fewer_than_%d_executable_books" % MIN_EARLY_CHECKPOINTS)
        covered = 0
        for c in got:
            t0 = c.get("t0_epoch")
            if t0 is None:
                continue
            t = float(t0) + float(c["checkpoint_s"])
            i = bisect.bisect_right(ref_t, t) - 1
            if i >= 0 and (t - ref_t[i]) <= MAX_REF_AGE_S:
                covered += 1
        if covered < MIN_EARLY_CHECKPOINTS:
            why.append("fewer_than_%d_fresh_reference_observations"
                       % MIN_EARLY_CHECKPOINTS)
        if why:
            for w in why:
                missing[w] += 1
            continue
        complete.append({"ticker": tk, "early_checkpoints": len(got),
                         "books": len(books), "reference_covered": covered,
                         "strike": strikes[tk]["strike"],
                         "strike_type": strikes[tk].get("strike_type"),
                         "up_resolution_side": settles[tk]})

    rep = {"label": LABEL, "tapes_glob": a.tapes,
           "tape_rows": len(rs),
           "reference_observations": len(ref),
           "btc5m_tickers_seen": len(seen),
           "min_early_checkpoints_required": MIN_EARLY_CHECKPOINTS,
           "max_reference_age_s": MAX_REF_AGE_S,
           "GENUINELY_COMPLETE_WINDOWS": len(complete),
           "complete": complete,
           "incomplete_by_missing_requirement": dict(missing),
           "requirements": ("early checkpoints, authoritative strike, "
                            "contemporaneous fresh reference, settlement, "
                            "executable books -- all five, per window"),
           "read_only": "opens no venue surface and changes nothing"}
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({k: rep[k] for k in (
        "label", "tape_rows", "reference_observations",
        "btc5m_tickers_seen", "GENUINELY_COMPLETE_WINDOWS",
        "incomplete_by_missing_requirement")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
