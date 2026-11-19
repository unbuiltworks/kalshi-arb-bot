"""MEASURE the Gemini BTC05M tape, so the capacity gate is fed facts.

    python -m src.gemini_btc5m_tape_probe --minutes 30 --out <path>.json

WHY A PROBE AND NOT A SERVICE. `gemini_btc5m_capacity_gate` refuses to guess
its inputs, which means somebody has to measure them. The two that decide the
verdict -- per-window volume and the touch spread -- are on a PUBLIC endpoint
that needs no API key, so measuring them costs nothing and requires no
authorization, no deployment and no persistent collector. This module is a
command you run when you want the number refreshed, and nothing runs it on a
schedule. The programme has enough always-on collectors.

WHAT IT MEASURES, and why each one is the honest version of itself.

  per-window volume   taken ONLY from windows observed at or after their own
                      expiry. An unstarted window reads $0 and averaging those
                      in would understate the tape by whatever fraction of the
                      listing happens to be in the future -- which was 11 of 13
                      on the first read of 2026-11-19 and is pure artefact.

  touch spread        the MEDIAN over observations, with degenerate readings
                      (>= $0.50, i.e. a book with nothing on one side)
                      excluded. The mean is useless here: one empty book at
                      0.98 drags it anywhere. On 2026-11-19 the median was
                      $0.01 at p25, p50 and p75, while a single window showed
                      $0.15 -- and reporting the outlier as the spread would
                      have overstated the venue's gross margin fifteenfold.

READ-ONLY BY CONSTRUCTION. One GET against
`/v1/prediction-markets/events`, which the docs state needs no credential. No
key is read, no order route is imported, and the module holds no venue
authentication of any kind.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Optional

LABEL = "GEMINI_BTC5M_TAPE_PROBE_V1"
EVENTS = "https://api.gemini.com/v1/prediction-markets/events?limit=100&offset=%d"

#: a book quoted this wide has nothing resting on one side
DEGENERATE_SPREAD = D("0.5")

DEFAULT_SERIES = ("BTC05M",)


def _dec(x: Any) -> Optional[D]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def _parse_ts(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_events(opener=urllib.request.urlopen) -> List[Dict[str, Any]]:
    """Every listed event, paginated. Public; no credential is sent."""
    out: List[Dict[str, Any]] = []
    off = 0
    while True:
        with opener(EVENTS % off, timeout=60) as r:
            page = json.load(r)
        out += page.get("data") or []
        total = ((page.get("pagination") or {}).get("total")) or 0
        off += 100
        if off >= total or not page.get("data"):
            break
    return out


def observe(events: List[Dict[str, Any]], series: tuple,
            windows: Dict[str, Dict[str, Any]],
            spreads: List[D], now: datetime) -> None:
    """Fold one poll into the running observation state. PURE given inputs."""
    for e in events:
        if e.get("series") not in series:
            continue
        t = e.get("ticker")
        if not t:
            continue
        rec = windows.setdefault(t, {"ticker": t, "series": e.get("series"),
                                     "expiry": e.get("expiryDate")})
        rec["volume"] = str(_dec(e.get("volume")) or D("0"))
        rec["status"] = e.get("status")
        rec["last_seen"] = now.isoformat()
        for c in e.get("contracts") or []:
            p = c.get("prices") or {}
            bid, ask = _dec(p.get("bestBid")), _dec(p.get("bestAsk"))
            if bid is None or ask is None:
                continue
            sp = ask - bid
            if sp > D("0"):
                spreads.append(sp)


def summarize(windows: Dict[str, Dict[str, Any]], spreads: List[D],
              now: datetime) -> Dict[str, Any]:
    """The capacity gate's two inputs, with everything excluded named."""
    completed, unstarted = [], 0
    for rec in windows.values():
        exp = _parse_ts(rec.get("expiry"))
        if exp is not None and exp <= now:
            completed.append(_dec(rec.get("volume")) or D("0"))
        else:
            unstarted += 1

    real = [s for s in spreads if s < DEGENERATE_SPREAD]
    degenerate = len(spreads) - len(real)

    def pct(vals: List[D], q: float) -> Optional[str]:
        if not vals:
            return None
        s = sorted(vals)
        return str(s[min(len(s) - 1, int(q * len(s)))])

    vol_mean = (sum(completed) / D(len(completed))) if completed else None
    return {
        "LABEL": LABEL,
        "as_of_utc": now.isoformat(),
        "read_only": True,
        "order_entry": False,
        "credentials_used": False,
        "completed_windows_observed": len(completed),
        "unstarted_windows_excluded": unstarted,
        "window_volume_usd_mean": str(vol_mean) if vol_mean is not None else None,
        "window_volume_usd_median": pct(completed, 0.5),
        "window_volume_usd_all": [str(v) for v in sorted(completed)],
        "spread_observations": len(spreads),
        "spread_degenerate_excluded": degenerate,
        "touch_spread_median": pct(real, 0.5),
        "touch_spread_p25": pct(real, 0.25),
        "touch_spread_p75": pct(real, 0.75),
        "note": ("window_volume is total tape across ALL participants, not "
                 "ours. Unstarted windows read $0 and are excluded rather "
                 "than averaged in. Spreads >= $0.50 are one-sided books."),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the Gemini BTC05M tape")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--interval-s", type=float, default=60.0)
    ap.add_argument("--series", default=",".join(DEFAULT_SERIES))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    series = tuple(s.strip() for s in a.series.split(",") if s.strip())
    windows: Dict[str, Dict[str, Any]] = {}
    spreads: List[D] = []
    deadline = time.time() + a.minutes * 60.0
    polls = 0
    while True:
        now = datetime.now(timezone.utc)
        try:
            observe(fetch_events(), series, windows, spreads, now)
            polls += 1
        except Exception as exc:          # a failed poll is reported, not fatal
            print("%s POLL_ERROR %r" % (LABEL, exc), flush=True)
        if time.time() >= deadline:
            break
        time.sleep(a.interval_s)

    out = summarize(windows, spreads, datetime.now(timezone.utc))
    out["polls"] = polls
    text = json.dumps(out, indent=2)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    print(text, flush=True)
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
