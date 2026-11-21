"""Three clocks, never conflated, and the skew between them tracked explicitly.

THE FAILURE THIS EXISTS TO PREVENT. L5's actions reach us late. If we take an
action that ARRIVED at 12:00:30 but is STAMPED 12:00:00 and fold it into "the
state as of 12:00:00", we have built a state out of information we did not have
at 12:00:00. Every prediction made from that state is then contaminated by
hindsight, and it will look like skill. This is the same length-biased /
look-ahead family of error that produced the predicted-0.93 against actual-0.46
ACT rate, and it is subtle precisely because the timestamps all look consistent.

    MARKET_STATE_CLOCK    contemporaneous book/trade state. What the venue's
                          own order book said, at venue time. CLOB surfaces
                          stamp this in milliseconds.

    L5_EVENT_CLOCK        the source timestamp the venue assigns to L5's
                          action. On data-api this is INTEGER SECONDS, so it
                          is itself quantized by up to a second.

    L5_OBSERVATION_CLOCK  when that action first became observable TO US. Our
                          wall clock at first receipt. Always >= the event
                          clock, typically by tens of seconds.

THE RULE, enforced by `assert_no_lookahead`: a state asserted to hold at time T
may only contain actions whose OBSERVATION time is <= T. Using the event clock
for that test is exactly the bug -- it silently admits everything that had
happened but that we could not yet see.

WHEN A LATE ACTION IS LEGITIMATE. Historical and out-of-sample modelling may
use the event clock freely: the whole tape is long since observable, and the
event clock is the right ordering for "what did L5 do next". The observation
clock governs REALTIME claims only. Conflating those two questions is what
section 4 of the operating instruction separates as event-time fidelity versus
realtime replicability, and the two are computed from different clocks here.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Sequence

MARKET_STATE_CLOCK = "market_state"
L5_EVENT_CLOCK = "l5_event"
L5_OBSERVATION_CLOCK = "l5_observation"
CLOCKS = (MARKET_STATE_CLOCK, L5_EVENT_CLOCK, L5_OBSERVATION_CLOCK)

# data-api stamps whole seconds, so an event at t=1.9s is reported as 1.
SOURCE_QUANTIZATION_S = {"data-api": 1.0, "clob": 0.001}


class LookaheadError(AssertionError):
    """A state was built from an action we could not yet have seen."""


def stamp(*, event_ts: float, observed_at: float,
          market_state_ts: Optional[float] = None,
          source: str = "data-api") -> Dict[str, Any]:
    """Attach all three clocks to one observation, plus the skew between them.

    `observation_lag_s` is the honest headline: how long after the venue said
    it happened could we first see it. `quantization_s` is carried so a reader
    can tell how much of a sub-second figure is real.
    """
    if observed_at < event_ts:
        # not impossible -- clock skew can do it -- but it must never be
        # silently treated as a negative lag.
        pass
    d = {
        "clocks": {
            L5_EVENT_CLOCK: event_ts,
            L5_OBSERVATION_CLOCK: observed_at,
            MARKET_STATE_CLOCK: market_state_ts,
        },
        "observation_lag_s": round(observed_at - event_ts, 3),
        "source": source,
        "quantization_s": SOURCE_QUANTIZATION_S.get(source),
    }
    if market_state_ts is not None:
        # how stale the book we priced against was, relative to the action
        d["state_minus_event_s"] = round(market_state_ts - event_ts, 3)
        d["state_minus_observation_s"] = round(market_state_ts - observed_at, 3)
    return d


def observable_at(actions: Iterable[Dict[str, Any]], t: float,
                  *, clock: str = L5_OBSERVATION_CLOCK) -> list:
    """The actions we could actually have known about at wall-clock `t`.

    Defaults to the OBSERVATION clock on purpose: filtering by the event clock
    is the look-ahead bug, so getting it right must be the default rather than
    an option the caller remembers to pass.
    """
    out = []
    for a in actions:
        ts = (a.get("clocks") or {}).get(clock, a.get(clock))
        if ts is not None and ts <= t:
            out.append(a)
    return out


def assert_no_lookahead(state_t: float, actions: Sequence[Dict[str, Any]]) -> None:
    """Refuse a state built from actions that were not yet observable.

    Raises rather than warns. A contaminated state does not announce itself in
    the output -- it just looks like a better model -- so the only safe failure
    mode is a loud one.
    """
    bad = []
    for a in actions:
        c = a.get("clocks") or {}
        obs = c.get(L5_OBSERVATION_CLOCK, a.get(L5_OBSERVATION_CLOCK))
        if obs is not None and obs > state_t:
            bad.append({
                "event_ts": c.get(L5_EVENT_CLOCK, a.get(L5_EVENT_CLOCK)),
                "observed_at": obs,
                "late_by_s": round(obs - state_t, 3),
            })
    if bad:
        raise LookaheadError(
            f"{len(bad)} action(s) were folded into a state asserted at "
            f"{state_t}, but were not observable until later (worst: "
            f"{max(b['late_by_s'] for b in bad)}s after). Using the event "
            f"clock instead of the observation clock is exactly this bug.")


def skew_report(observations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Distribution of event->observation skew. Quantiles, never a mean:
    the lag is batched and right-tailed, so a mean describes nothing."""
    lags = sorted(float(o["observation_lag_s"]) for o in observations
                  if o.get("observation_lag_s") is not None)
    if not lags:
        return {"n": 0, "reason": "no observations carrying an observation lag"}

    def q(p: float) -> float:
        return round(lags[min(len(lags) - 1, int(p * len(lags)))], 3)

    return {"n": len(lags), "p05": q(.05), "p50": q(.50), "p95": q(.95),
            "max": round(lags[-1], 3), "min": round(lags[0], 3),
            "negative_n": sum(1 for x in lags if x < 0),
            "labels": ("quantiles, not a mean: arrival is batched and "
                       "right-tailed. A negative lag means our clock is ahead "
                       "of the venue's, not that we saw the future.")}
