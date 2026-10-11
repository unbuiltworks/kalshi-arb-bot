"""SUBSCRIBE BEFORE THE DECISION WINDOW, so coverage is not a warm-up artefact.

THE ARITHMETIC THAT FORCES THIS. A BTC5m contract lives 300 seconds and the
lane enters it in the band tte 240-280 -- i.e. 20 to 60 seconds after the
contract exists. Asking the socket to vouch for a trailing 120-second flow
window at that moment demands 120 seconds of history from a contract that is 40
seconds old. It cannot exist. The warm-up gap was therefore not a tuning
problem; for BTC5m it was STRUCTURALLY UNSATISFIABLE.

TWO CHANGES, AND ONLY ONE OF THEM IS THIS MODULE.

  1. SUBSCRIBE EARLY (here). Walk the contract grid forward and open the socket
     for upcoming contracts as soon as they exist, so `ready_since` is as close
     to contract creation as the venue permits rather than as late as the
     decision.

  2. ASK FOR THE LONGEST VOUCHABLE WINDOW (in `ws_flow`). The requirement the
     owner set is that a decision uses a VOUCHED INTERVAL -- not that it uses a
     120-second one. A 60-second window we genuinely watched is fully vouched;
     demanding 120 when only 60 exists discards real evidence to satisfy a
     number. So the flow source narrows the request to what the socket can
     actually attest, floors it at a minimum below which nothing is claimed,
     and RECORDS the window it actually used on every row.

Neither change weakens provenance. Nothing observed after a decision is ever
backfilled into it: `prints_since` is evaluated at the decision instant and the
interval it covers ends there.

WHY A GRID WALK AND NOT A DISCOVERY CALL. `metadata.current_ticker` and
`sibling_ticker` already generate the family's ticker sequence deterministically
from the clock -- the same functions `eligible_symbol` uses to find the contract
the window will bind. Reusing them means the pre-warmer cannot drift from the
selector and start warming contracts the lane will never choose.

BOUNDED. At most `MAX_WATCHED` sockets per cohort, and expired contracts are
dropped every pass, so a long-running collector cannot accumulate threads.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA = "gemini_econ_prewarm/v1"

#: how many contracts ahead of the current one to hold open, per cohort
LOOKAHEAD = 2

#: hard ceiling on concurrently watched symbols per cohort
MAX_WATCHED = 4

#: a contract is dropped once it is this far past expiry
DROP_AFTER_EXPIRY_S = 30.0


@dataclass
class Prewarmer:
    """Keeps the sockets for the current and upcoming contracts open."""

    cohort_name: str
    prefix: str
    minutes: int
    watched: Dict[str, float] = field(default_factory=dict)   # symbol -> t0
    opened: int = 0
    dropped: int = 0
    errors: int = 0
    last_error: Optional[str] = None

    def upcoming(self, *, now: Optional[float] = None) -> List[str]:
        """Current contract plus the next `LOOKAHEAD`, from the same grid
        functions the selector uses."""
        from src.gemini_execution import metadata as MD
        t = time.time() if now is None else now
        out: List[str] = []
        try:
            cur = MD.current_ticker(t, prefix=self.prefix, minutes=self.minutes)
        except Exception as exc:                                 # noqa: BLE001
            self.errors += 1
            self.last_error = "current_ticker: %s" % exc
            return out
        if not cur:
            return out
        for step in range(LOOKAHEAD + 1):
            try:
                tk = (cur if step == 0
                      else MD.sibling_ticker(cur, self.minutes * step))
            except Exception as exc:                             # noqa: BLE001
                self.errors += 1
                self.last_error = "sibling_ticker: %s" % exc
                continue
            if tk:
                out.append("GEMI-%s-UP" % tk)
        return out[:MAX_WATCHED]

    def pump(self, ws: Any, *, now: Optional[float] = None,
             expiry_of: Any = None) -> Dict[str, Any]:
        """Open what should be open, drop what has expired. Idempotent.

        Never raises: a pre-warmer that can crash a collector is worse than a
        pre-warmer that missed a contract.
        """
        t = time.time() if now is None else now
        want = self.upcoming(now=t)
        newly: List[str] = []
        for sym in want:
            if sym in self.watched:
                continue
            if len(self.watched) >= MAX_WATCHED:
                break
            try:
                ws.watch(sym)
                self.watched[sym] = t
                self.opened += 1
                newly.append(sym)
            except Exception as exc:                             # noqa: BLE001
                self.errors += 1
                self.last_error = "watch %s: %s" % (sym, exc)

        # drop anything expired or no longer on the near grid
        gone: List[str] = []
        for sym in list(self.watched):
            expired = False
            if expiry_of is not None:
                try:
                    exp = expiry_of(sym)
                    expired = (exp is not None
                               and t > float(exp) + DROP_AFTER_EXPIRY_S)
                except Exception:                                # noqa: BLE001
                    expired = False
            if expired or (sym not in want and len(self.watched) > MAX_WATCHED):
                try:
                    ws.drop(sym)
                except Exception:                                # noqa: BLE001
                    pass
                self.watched.pop(sym, None)
                self.dropped += 1
                gone.append(sym)

        return {"schema": SCHEMA, "cohort": self.cohort_name,
                "watching": sorted(self.watched), "opened_now": newly,
                "dropped_now": gone, "opened_total": self.opened,
                "dropped_total": self.dropped, "errors": self.errors,
                "last_error": self.last_error}

    def watching_since(self, symbol: str) -> Optional[float]:
        return self.watched.get(symbol)

    def was_prewarmed(self, symbol: str, *, entered_at: float,
                      min_lead_s: float = 5.0) -> bool:
        """Did the socket open meaningfully BEFORE the lane entered?

        Used only to classify a warm-up exclusion as "pre-warming happened and
        was still not enough" versus "pre-warming never got the chance", which
        are different problems with different fixes.
        """
        t0 = self.watched.get(symbol)
        return bool(t0 is not None and (entered_at - t0) >= min_lead_s)

    def report(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "cohort": self.cohort_name,
                "watching": sorted(self.watched), "opened_total": self.opened,
                "dropped_total": self.dropped, "errors": self.errors,
                "last_error": self.last_error, "lookahead": LOOKAHEAD,
                "max_watched": MAX_WATCHED}
