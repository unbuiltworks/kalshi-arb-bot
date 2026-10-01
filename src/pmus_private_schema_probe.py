"""PHASE 1 OBSERVABILITY PROBE: what does /v1/ws/private actually send?

SOLE OBJECTIVE: observe the private-WS accept/open/cancel frames for ONE real
resting order of ours, and record their RAW schema. It is not permitted to chase
a fill, measure profitability, or inform any strategy decision. **A fill is a
FAILURE of this design, not a goal.**

WHAT IT CANNOT ANSWER, stated here so the result is never oversold: the FILL
frame schema -- venue trade id, fill timestamp, executed price/quantity,
maker/taker role. Only a real fill reveals those, and this probe is built so a
fill essentially cannot happen.

EXPOSURE. One order, quantity 1, priced at most MAX_PX and refused unless it is
at least MIN_DISTANCE below the executable touch in own terms. Worst case if it
somehow filled in full is $0.01, and filling at all would need the market to
collapse 20+ ticks while we are live.

ARMING, exactly: `PMUS_Q1_LIVE` must be the literal string `YES`, and
`PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` and `PMUS_SCHEMA_PROBE_ARM` must all equal
the SAME session id. `PMUS_Q1_LIVE` is NOT the session id. Absent or mismatched
on any of the four, the module refuses to submit.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_order_body as OB
from src import pmus_q1_executor as X
from src import pmus_ws_lifecycle_observer as PV

LIVE_ENV = "PMUS_Q1_LIVE"
SESSION_ENV = "PMUS_Q1_SESSION_ID"
ARM_ENV = "PMUS_Q1_ARM"
PROBE_ARM_ENV = "PMUS_SCHEMA_PROBE_ARM"

#: the venue price grid. A price off this grid is rejected by the venue, and
#: an earlier version of this rule could emit 0.005 at a touch of 0.205.
PRICE_TICK = D("0.01")
#: the order is priced here or lower, in the side's own terms. Since this equals
#: one tick, the only on-grid price available is exactly one tick.
MAX_PX = D("0.01")
#: and refused unless it is at least this far BELOW the executable touch
MIN_DISTANCE = D("0.20")
#: how long we are willing to hold the resting order purely to observe
OBSERVE_MAX_S = 120.0
#: both sockets must deliver at least this many frames before we submit
READY_FRAMES = 3


class ProbeRefuse(RuntimeError):
    """Preflight failed. Nothing was submitted."""


def armed(session_id: str) -> bool:
    return (os.getenv(LIVE_ENV, "").strip().upper() == "YES"
            and os.getenv(SESSION_ENV, "").strip() == session_id
            and os.getenv(ARM_ENV, "").strip() == session_id
            and os.getenv(PROBE_ARM_ENV, "").strip() == session_id)


def probe_price(touch: Optional[D]) -> D:
    """At most MAX_PX, and at least MIN_DISTANCE below the executable touch.

    Refuses rather than submitting anything that could be near the market.
    """
    if touch is None:
        raise ProbeRefuse("NO_TOUCH: refusing to price without a live touch")
    touch = D(str(touch))
    #: MAX_PX is exactly one tick, so the only on-grid candidate is one tick.
    #: Taking min(MAX_PX, touch - MIN_DISTANCE) could yield an OFF-GRID price
    #: (touch 0.205 -> 0.005), which the venue would reject; refuse instead.
    px = MAX_PX
    if px % PRICE_TICK != 0:
        raise ProbeRefuse(f"OFF_GRID: {px} is not a multiple of {PRICE_TICK}")
    if touch - px < MIN_DISTANCE:
        raise ProbeRefuse(
            f"TOO_CLOSE: {px} is only {touch - px} below the touch {touch}, "
            f"need {MIN_DISTANCE}")
    return px


def stamp(extra: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    out = {"wall_utc": datetime.now(timezone.utc).isoformat(),
           "monotonic": time.monotonic()}
    if extra:
        out.update(extra)
    return out


class FrameLedger:
    """Every private frame, VERBATIM. No field is renamed, dropped, coerced or
    mapped onto a presumed REST shape -- the whole point is the raw schema."""

    def __init__(self) -> None:
        self.frames: List[Dict[str, Any]] = []

    def add(self, raw: Any, *, source: str) -> None:
        self.frames.append(stamp({"source": source, "raw": raw}))

    def observed_keys(self) -> Dict[str, List[str]]:
        """What the venue actually sent, as a key census per frame shape."""
        out: Dict[str, List[str]] = {}
        for f in self.frames:
            raw = f.get("raw")
            if isinstance(raw, dict):
                k = str(raw.get("subscriptionType") or raw.get("type") or "?")
                out.setdefault(k, [])
                for key in raw:
                    if key not in out[k]:
                        out[k].append(key)
        return out

    def report(self) -> Dict[str, Any]:
        return {"frame_count": len(self.frames),
                "observed_frame_keys": self.observed_keys(),
                "frames_verbatim": self.frames,
                "note": "raw venue frames; no mapping onto REST field names"}


def preflight(t: X.Transport, slug: str, *, public_frames: int,
              private_frames: int, sleep=time.sleep) -> Dict[str, Any]:
    """Prove both sockets are RECEIVING and the price is safe. Submit nothing."""
    if public_frames < READY_FRAMES:
        raise ProbeRefuse(
            f"PUBLIC_SOCKET_NOT_PROVEN: {public_frames} frames "
            f"< {READY_FRAMES}")
    if private_frames < READY_FRAMES:
        raise ProbeRefuse(
            f"PRIVATE_SOCKET_NOT_PROVEN: {private_frames} frames "
            f"< {READY_FRAMES}")
    bbo = X.fresh_bbo(t, slug, sleep=sleep)       # raises on stale feed
    touch = bbo.get("bid")
    px = probe_price(D(str(touch)) if touch is not None else None)
    if X.would_cross(OB.outcome_side("up"), px, bbo):
        raise ProbeRefuse(f"WOULD_CROSS: {px} against {bbo}")
    return {"price": px, "bbo": bbo, "touch": touch}


def build_body(slug: str, px: D) -> Dict[str, Any]:
    """The proven serializer, post-only GTC. No new serializer exists here."""
    return OB.build(slug=slug, side="up", price=px, qty=X.QTY,
                    post_only=True, tif=OB.TIF_GTC)


def classify_outcome(filled_qty: D) -> str:
    """A fill is a FAILURE of this design and is labelled as one."""
    return "PROBE_FAILURE_UNEXPECTED_FILL" if filled_qty > 0 else "OBSERVED"
