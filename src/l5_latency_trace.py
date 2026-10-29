"""Where the 18 seconds actually goes, measured stage by stage.

THE FINDING THIS EXISTS TO DECOMPOSE. `engine_row` computes
`latency_s = recv - r["timestamp"]`: L5's own event time to our local
receipt. Across 315 live decisions that is a median of 18.11 s, p90 31.87 s,
max 33.11 s, with a min of 1.34 s. In that time the BTC15m mid moves a
median full tick, which is the whole spread -- so we quote into a book that
has already moved, fail to fill when it moved our way, and fill when it
moved against us.

`latency_s` CANNOT SAY WHOSE DELAY IT IS. It is one number spanning the
feed's indexing, our polling, our decision and our wire. The fix is
completely different depending on the answer, so the number has to be split
before anything is changed.

THE STAGES, AND WHERE EACH TIMESTAMP COMES FROM.

    source_event_utc   the trade's own `timestamp` from the activity row.
                       GIVEN BY THE SOURCE.
    feed_served_utc    the HTTP `Date` response header of the poll that
                       carried the row. GIVEN BY THE SERVER -- its clock
                       when it served us. None when the header is absent or
                       unparseable; NEVER synthesised.
    first_seen_utc     the first poll in which this `action_key` appeared at
                       all. MEASURED BY US, bounded by the poll interval
                       (3 s), so it is a bound on visibility and is labelled
                       as one. It is NOT a publication timestamp and must
                       never be reported as one.
    receipt_utc        when the row reached `handle`. Already recorded.
    evaluated_utc      when the decision was taken.
    submitted_utc      when the order left for the venue.
    venue_ack_utc      the venue's acknowledgement.

THERE IS NO PUBLICATION TIMESTAMP IN THIS FEED. The activity endpoint
returns rows with a trade `timestamp` and nothing describing when the row
became queryable. `feed_served_utc` is the closest honest thing -- it says
when the SERVER answered, not when the row was indexed -- and
`first_seen_utc` bounds visibility from our side. Both are labelled for what
they are. Inventing a publication time would make the index segment look
precise when it is the segment we know least about.

SEGMENTS. Each is None when either endpoint is missing, because a stage
whose boundary is unknown has an unknown duration, and zero is a claim.

    index_lag_s    first_seen - source_event   (upstream + our poll phase)
    serve_lag_s    first_seen - feed_served    (network + our read)
    poll_lag_s     receipt - first_seen        (OUR batching)
    decide_lag_s   evaluated - receipt         (OUR decision)
    submit_lag_s   submitted - evaluated       (OUR gates and wire)
    ack_lag_s      venue_ack - submitted       (the venue)

WHAT THE SPLIT DECIDES. If `index_lag_s` dominates, the delay is the public
feed's and no engineering on our side closes it -- which would mean
L5-action entry is STRUCTURALLY adversely selected and the answer is
strategic, not technical. If `poll_lag_s` or `decide_lag_s` dominates, it is
ours and it is cheap. The point of this module is that the question stops
being arguable.

PURE AND RECORD-ONLY. No I/O, no clock of its own, no order. It changes no
behaviour and cannot: it formats timestamps that callers already hold.
"""
from __future__ import annotations

import datetime as dt
import email.utils
from typing import Any, Dict, Mapping, Optional

#: the stages, in order, as they are reported.
STAGES = ("source_event_utc", "feed_served_utc", "first_seen_utc",
          "receipt_utc", "evaluated_utc", "submitted_utc", "venue_ack_utc")

#: each segment and the two stage clocks it spans.
SEGMENTS = (
    ("index_lag_s", "source_event_utc", "first_seen_utc"),
    ("serve_lag_s", "feed_served_utc", "first_seen_utc"),
    ("poll_lag_s", "first_seen_utc", "receipt_utc"),
    ("decide_lag_s", "receipt_utc", "evaluated_utc"),
    ("submit_lag_s", "evaluated_utc", "submitted_utc"),
    ("ack_lag_s", "submitted_utc", "venue_ack_utc"),
)

#: who owns each segment, so the record answers "is this ours" without the
#: reader having to know the architecture.
OWNER = {"index_lag_s": "SOURCE_OR_POLL_PHASE", "serve_lag_s": "NETWORK",
         "poll_lag_s": "OURS", "decide_lag_s": "OURS",
         "submit_lag_s": "OURS", "ack_lag_s": "VENUE"}


def parse_http_date(value: Any) -> Optional[float]:
    """An RFC 7231 `Date` header as a unix timestamp, or None.

    None on anything unparseable, because a header we cannot read is not a
    time we may guess at.
    """
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return dt.datetime.fromtimestamp(
            float(ts), dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def trace(*, action_key: str,
          source_event_t: Optional[float] = None,
          feed_served_t: Optional[float] = None,
          first_seen_t: Optional[float] = None,
          receipt_t: Optional[float] = None,
          evaluated_t: Optional[float] = None,
          submitted_t: Optional[float] = None,
          venue_ack_t: Optional[float] = None,
          poll_interval_s: Optional[float] = None,
          market: Optional[str] = None,
          outcome: Optional[str] = None) -> Dict[str, Any]:
    """One action's stage timestamps and the segments between them."""
    at = {
        "source_event_utc": source_event_t,
        "feed_served_utc": feed_served_t,
        "first_seen_utc": first_seen_t,
        "receipt_utc": receipt_t,
        "evaluated_utc": evaluated_t,
        "submitted_utc": submitted_t,
        "venue_ack_utc": venue_ack_t,
    }
    segs: Dict[str, Any] = {}
    for name, a, b in SEGMENTS:
        ta, tb = at.get(a), at.get(b)
        # NEGATIVE IS REPORTED, NOT CLAMPED. Clocks here come from three
        # different machines, so a negative segment is real evidence of
        # skew and hiding it would turn a measurable problem into a
        # mysterious one.
        segs[name] = (None if ta is None or tb is None
                      else round(float(tb) - float(ta), 3))
    total = (None if at["source_event_utc"] is None
             or at["receipt_utc"] is None
             else round(float(at["receipt_utc"])
                        - float(at["source_event_utc"]), 3))
    return {
        "label": "L5_LATENCY_TRACE",
        "action_key": action_key,
        "market": market,
        "outcome": outcome,
        "at": {k: _iso(v) for k, v in at.items()},
        "segments": segs,
        "segment_owner": dict(OWNER),
        "source_event_to_receipt_s": total,
        "first_seen_bound_s": poll_interval_s,
        "labels": {
            "first_seen_utc": ("MEASURED BY US: the first poll in which "
                               "this action appeared. Bounded by the poll "
                               "interval. NOT a publication timestamp."),
            "feed_served_utc": ("the server's own HTTP Date header -- when "
                                "it ANSWERED, not when it indexed the row. "
                                "None when absent; never synthesised."),
            "publication": ("this feed publishes no publication timestamp. "
                            "None is recorded rather than invented."),
            "negatives": ("segments may be negative under clock skew "
                          "between three machines; they are reported as "
                          "measured, never clamped"),
        },
    }


def first_seen_tracker(cap: int = 4096):
    """Remembers the first poll an action_key appeared in.

    BOUNDED so a long run cannot grow it without limit: the oldest half is
    dropped at the cap. An action whose first sighting has been evicted
    reports None rather than the time of a later poll, because reporting the
    later one would understate the very lag this measures.
    """
    seen: Dict[str, float] = {}
    order: list = []

    def mark(action_key: str, now: float) -> float:
        if action_key not in seen:
            seen[action_key] = float(now)
            order.append(action_key)
            if len(order) > cap:
                for k in order[: cap // 2]:
                    seen.pop(k, None)
                del order[: cap // 2]
        return seen[action_key]

    def get(action_key: str) -> Optional[float]:
        return seen.get(action_key)

    mark.get = get                                             # type: ignore
    mark.size = lambda: len(seen)                              # type: ignore
    return mark


def summarise(traces) -> Dict[str, Any]:
    """Medians per segment over many traces, with the n each rests on.

    Reports the n beside every median because segments have different
    coverage: `venue_ack_utc` exists only for submitted orders, and a median
    over three rows next to one over three hundred should not look alike.
    """
    out: Dict[str, Any] = {}
    for name, _a, _b in SEGMENTS:
        vals = sorted(t["segments"][name] for t in traces
                      if t.get("segments", {}).get(name) is not None)
        out[name] = {
            "n": len(vals),
            "median_s": (vals[len(vals) // 2] if vals else None),
            "p90_s": (vals[int(len(vals) * 0.9)] if vals else None),
            "max_s": (vals[-1] if vals else None),
            "owner": OWNER[name],
        }
    tot = sorted(t["source_event_to_receipt_s"] for t in traces
                 if t.get("source_event_to_receipt_s") is not None)
    out["source_event_to_receipt_s"] = {
        "n": len(tot),
        "median_s": (tot[len(tot) // 2] if tot else None),
        "p90_s": (tot[int(len(tot) * 0.9)] if tot else None),
    }
    return out
