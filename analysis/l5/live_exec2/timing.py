"""Timing, written out so "TTE 120s" cannot be read two ways.

EXEC-1 froze `withdrawal.tte_shutdown_s = 120` and said "cancel at window
end". That is one number doing two jobs, and the ambiguity matters: does 120
mean "stop placing" or "stop resting"? Both, but at different moments, so
both are encoded here and tested:

  PLACE   allowed only while TTE  >  120 s   (and TTE <= the window length)
  REST    allowed only while TTE  >  120 s
  WITHDRAW required the moment    TTE <= 120 s
  AT THE BOUNDARY, an order still open is CANCELLED. It is never left to
  ride to settlement, and the cancel attempt happens even if the order's
  state is unknown.

The cutoff is a hard floor on PLACEMENT: a run that arrives late does not
place a later, worse order -- it declines and reports. There is no
"just inside the boundary" allowance, because an allowance is how a cutoff
becomes advisory.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: btc-updown-15m windows are 900 s long, from the slug's own 15-minute mark
WINDOW_LEN_S = 900.0

#: the single frozen EXEC-1 number, now with both of its jobs spelled out
TTE_SHUTDOWN_S = 120.0

PLACEMENT_CUTOFF_TTE_S = TTE_SHUTDOWN_S     # no new order at or below this
WITHDRAW_AT_TTE_S = TTE_SHUTDOWN_S          # resting exposure comes off here


def tte_s(window_start_unix: Optional[float], now: float) -> Optional[float]:
    if window_start_unix is None:
        return None
    return (float(window_start_unix) + WINDOW_LEN_S) - now


def placement_allowed(tte: Optional[float]) -> Dict[str, Any]:
    if tte is None:
        return {"allowed": False, "tte_s": None,
                "reason": "TTE_UNKNOWN: the window start could not be read"}
    if tte <= PLACEMENT_CUTOFF_TTE_S:
        return {"allowed": False, "tte_s": round(tte, 3),
                "reason": "PAST_PLACEMENT_CUTOFF: TTE %.3fs <= %.1fs"
                          % (tte, PLACEMENT_CUTOFF_TTE_S)}
    if tte > WINDOW_LEN_S:
        return {"allowed": False, "tte_s": round(tte, 3),
                "reason": "WINDOW_NOT_OPEN: TTE %.3fs > %.1fs"
                          % (tte, WINDOW_LEN_S)}
    return {"allowed": True, "tte_s": round(tte, 3),
            "reason": None,
            "cutoff_tte_s": PLACEMENT_CUTOFF_TTE_S}


def must_withdraw(tte: Optional[float]) -> Dict[str, Any]:
    """At the boundary, or with an unreadable clock, exposure comes off."""
    if tte is None:
        return {"withdraw": True, "tte_s": None,
                "reason": "TTE_UNKNOWN: withdraw rather than rest blind"}
    if tte <= WITHDRAW_AT_TTE_S:
        return {"withdraw": True, "tte_s": round(tte, 3),
                "reason": "AT_OR_PAST_WITHDRAWAL_BOUNDARY: TTE %.3fs <= %.1fs"
                          % (tte, WITHDRAW_AT_TTE_S)}
    return {"withdraw": False, "tte_s": round(tte, 3), "reason": None}
