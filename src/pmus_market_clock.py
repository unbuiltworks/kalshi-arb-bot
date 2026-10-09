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
    # The /v1/markets list payload serves `startDate`; the per-market and
    # BTC15m payloads serve `startTime`. Both are accepted, and the span
    # guard below is what keeps the wrong one from becoming a clock: the
    # sports futures measured 2026-10-09 serve startDate 2026-03-19 against
    # endDate 2026-11-06, a 7-month span, which is refused.
    start = _epoch(md.get("startTime") or md.get("startDate"))
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


# PER-SPORT NOMINAL DURATIONS, DECLARED AS ASSUMPTIONS, NOT VENUE DATA.
#
# The venue serves a game's POSITION (period tokens, the ended flag) and no
# settlement time at all -- pmus_event_progress says so explicitly and
# refuses to manufacture one. The clone's feature vector, however, was fit
# with `tte_s` in seconds, so running the policy on sports requires SOME
# seconds-to-end. These are nominal wall-clock lengths, used only to turn an
# authoritative position into a seconds estimate, and every row that uses
# one carries `tte_s_basis: ASSUMED_NOMINAL_DURATION` plus the number used.
# Nothing here is presented as a venue fact, and the shadow separately
# reports whether the action would change if `tte_s` were the corpus median
# instead -- so the assumption's influence is measured, not trusted.
NOMINAL_GAME_S = {
    "football": 10800.0, "basketball": 8100.0, "baseball": 10800.0,
    "soccer": 7200.0, "hockey": 8400.0, "tennis": 7200.0,
    "esports": 5400.0, "cricket": 14400.0,
}
NOMINAL_DEFAULT_S = 7200.0


def sports_position(slug: Any, progress: Optional[Mapping[str, Any]]) -> dict:
    """Where a SPORTS market sits in its game, from the venue's own tokens.

    `t_frac` is `game_proximity` -- the venue-served position that
    pmus_event_progress derives from period tokens and refuses to invent.
    A market with no recognised token is UNRESOLVED and is not watched,
    exactly as an unparseable crypto slug is.

    CONTRACT EXPIRY IS NEVER USED. `endDate` on these markets is
    gameStartTime + 14 days, identical across markets and unrelated to when
    the position resolves; feeding it in is the defect this whole module
    exists to prevent.
    """
    if not isinstance(progress, Mapping):
        return {"resolved": False, "basis": UNRESOLVED,
                "why": "NO_EVENT_PROGRESS_ROW"}
    if progress.get("ended"):
        return {"resolved": False, "basis": UNRESOLVED,
                "why": "GAME_ENDED", "ended": True}
    prox = progress.get("game_proximity")
    if prox is None:
        return {"resolved": False, "basis": UNRESOLVED,
                "why": "PROXIMITY_%s" % progress.get("proximity_basis")}
    try:
        t_frac = float(prox)
    except (TypeError, ValueError):
        return {"resolved": False, "basis": UNRESOLVED,
                "why": "PROXIMITY_UNPARSEABLE"}
    sport = str(progress.get("sport_type") or "").lower()
    nominal = NOMINAL_GAME_S.get(sport, NOMINAL_DEFAULT_S)
    return {
        "resolved": True,
        "basis": "venue_event_progress",
        "duration_s": nominal,
        "window_start": None,
        "t_frac": min(1.0, max(0.0, t_frac)),
        "tte_s": max(0.0, (1.0 - min(1.0, max(0.0, t_frac))) * nominal),
        "tte_s_basis": "ASSUMED_NOMINAL_DURATION",
        "nominal_duration_s": nominal,
        "expired": False,
        "sport_type": sport or None,
        "league": progress.get("league"),
        "period": progress.get("period"),
        "proximity_basis": progress.get("proximity_basis"),
        "rebate_eligible": progress.get("rebate_eligible"),
        "fee_coefficient": progress.get("fee_coefficient"),
        "CONTRACT_EXPIRY_NOT_USED": True,
    }


def position(slug: Any, market_data: Optional[Mapping[str, Any]],
             now: float, progress: Optional[Mapping[str, Any]] = None) -> dict:
    """Where `now` sits in the market's own window.

    `t_frac` and `tte_s` are the two clock features the policy was fit on,
    computed here exactly as `extract.py` computed them for training:
    `t_frac = min(1, elapsed/duration)` and `tte_s = max(0, close - now)`.
    """
    dur, start, basis = resolve(slug, market_data)
    if dur is None or start is None:
        # A sports market has no window in its slug and no usable
        # start/end pair; its clock comes from the live event feed.
        if progress is not None:
            sp = sports_position(slug, progress)
            if sp.get("resolved"):
                return sp
        return {"resolved": False, "basis": basis, "duration_s": None,
                "window_start": None, "t_frac": None, "tte_s": None,
                "WHY": "no trustworthy duration; the caller must not guess"}
    elapsed = max(0.0, now - start)
    return {"resolved": True, "basis": basis, "duration_s": dur,
             "window_start": start,
             "t_frac": min(1.0, elapsed / dur) if dur > 0 else None,
             "tte_s": max(0.0, start + dur - now),
             "expired": now >= start + dur}
