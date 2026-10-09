"""ONE market clock, for training and for the live shadow.

WHY THIS IS A MODULE. `analysis/l5/clone/extract.py` read the market's
duration and window start from the Polymarket slug form `...-15m-<unix>`,
which is what the historical corpus serves. PMUS serves a DIFFERENT form:
`cpc-btc-updown-15m-2026-09-23-1345z`, a datestamp, not a unix stamp. A
shadow that reconstructed `t_frac` and `tte_s` with the training regex
would have found no match on every live crypto market and silently fed the
policy a different clock from the one it was fit on -- two of the model's
fourteen features, wrong, with no error anywhere.

WHAT IT REFUSES TO DO. It never defaults a duration. An unrecognised market
returns `UNRESOLVED` and the caller must decide; guessing 15 minutes
because most crypto markets are 15 minutes is how a 60-minute market gets
scored as though it were four minutes from expiry.

RESOLUTION ORDER, strongest evidence first:

  1. `venue_window`   -- the venue's own `startTime` and `endDate`, when
     both parse and the span is positive and under a day. This is a fact
     served by the exchange.
  2. `slug_datestamp`  -- `-<Nm>-YYYY-MM-DD-HHMMz`, the PMUS crypto form.
  3. `slug_unix`       -- `-<Nm>-<unix>`, the historical Polymarket form the
     training corpus uses.

`endDate` ALONE is deliberately not a source: measured 2026-09-17 on 18/18
sports markets it is a fixed gameStartTime + 14 days contract expiry and
carries no settlement information (see pmus_l5_continuous_session), so a
clock built on it reads every game as two weeks from resolution.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping, Optional, Tuple

SLUG_UNIX = re.compile(r"-(\d+)m-(\d+)$")
SLUG_STAMP = re.compile(r"-(\d+)m-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})z$")

UNRESOLVED = "UNRESOLVED"
MAX_SPAN_S = 86400.0


def _epoch(iso: Any) -> Optional[float]:
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def resolve(slug: Any, market_data: Optional[Mapping[str, Any]] = None
            ) -> Tuple[Optional[float], Optional[float], str]:
    """(duration_s, window_start_epoch, basis). Never guesses."""
    md = market_data or {}
    start = _epoch(md.get("startTime"))
    end = _epoch(md.get("endDate"))
    if start is not None and end is not None:
        span = end - start
        if 0 < span <= MAX_SPAN_S:
            return float(span), float(start), "venue_window"

    s = str(slug or "")
    m = SLUG_STAMP.search(s)
    if m:
        mins = int(m.group(1))
        y, mo, dd, hh, mi = (int(x) for x in m.groups()[1:])
        st = dt.datetime(y, mo, dd, hh, mi, tzinfo=dt.timezone.utc).timestamp()
        return float(mins * 60), float(st), "slug_datestamp"

    m = SLUG_UNIX.search(s)
    if m:
        return float(int(m.group(1)) * 60), float(int(m.group(2))), "slug_unix"

    return None, None, UNRESOLVED


def position(slug: Any, market_data: Optional[Mapping[str, Any]],
             now: float) -> dict:
    """Where `now` sits in the market's own window.

    `t_frac` and `tte_s` are the two clock features the policy was fit on,
    computed here exactly as `extract.py` computed them for training:
    `t_frac = min(1, elapsed/duration)` and `tte_s = max(0, close - now)`.
    """
    dur, start, basis = resolve(slug, market_data)
    if dur is None or start is None:
        return {"resolved": False, "basis": basis, "duration_s": None,
                "window_start": None, "t_frac": None, "tte_s": None,
                "WHY": "no trustworthy duration; the caller must not guess"}
    elapsed = max(0.0, now - start)
    return {"resolved": True, "basis": basis, "duration_s": dur,
             "window_start": start,
             "t_frac": min(1.0, elapsed / dur) if dur > 0 else None,
             "tte_s": max(0.0, start + dur - now),
             "expired": now >= start + dur}
