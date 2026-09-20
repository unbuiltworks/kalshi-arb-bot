"""PER-COHORT STATE THAT OUTLIVES A WINDOW, because the arm does not.

THE DEFECT THIS FIXES, STATED PLAINLY. `shadow_soak.one_window` builds
`arms: List[_Arm] = []` fresh on every window, so `GeminiEconArm` -- and with
it the spread census, the hazard estimator and the counterfactual ledger -- was
CONSTRUCTED AND DESTROYED ONCE PER CONTRACT. Two consequences, both of which
showed up in the first live logs:

  1. `ECON_SPREAD` printed `BTC5m|121-300|0.56-0.75|unknown` twice with
     different n and medians (n=12 med=0.230, then n=2 med=0.315). That is NOT
     an omitted key dimension and NOT an aggregation bug in the key: the two
     lines are two INDEPENDENT short-lived censuses that happen to share a cell
     name. The dimension that distinguished them was the contract -- and it
     must NOT go into the key, because pooling across contracts is the entire
     point of a distribution.
  2. `cells_sufficient` could never reach 1. A census that resets every ~5
     minutes cannot accumulate 30 observations in a cell, so the measurement
     the spread decision waits on was structurally unreachable.

The hazard estimator had the same shape with an extra twist: the arm LOADED it
from `hazard_path` and never SAVED it, so every window started from whatever
was last on disk, which was nothing.

WHAT THIS IS. One `CohortState` per contract family, held in a process-level
registry and handed to each new arm. It survives window rollover because it is
not owned by the window, and it survives a container restart because it writes
itself to the service's volume.

WHY A REGISTRY RATHER THAN THREADING IT THROUGH. `one_window` is called from a
loop that already owns the cohort; passing state down would mean changing the
control's call signature, and the control's path is preserved byte-for-byte on
this branch. A lookup keyed by cohort name touches nothing the control reads.

SAVING IS BEST-EFFORT AND NEVER BLOCKS A DECISION. A failed write is recorded
and the run continues on in-memory state: losing a census to a full disk is bad,
stopping a collector because a census could not be written is worse.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.gemini_execution.econ import attribution as AT
from src.gemini_execution.econ import blocked_labeler as BL
from src.gemini_execution.econ import counterfactual as CF
from src.gemini_execution.econ import coverage as CV
from src.gemini_execution.econ import hazard as HZ
from src.gemini_execution.econ import prewarm as PW
from src.gemini_execution.econ import spread_census as SC
from src.gemini_execution.econ import ws_flow as WF

SCHEMA = "gemini_econ_cohort_state/v1"


@dataclass
class CohortState:
    """Everything that must accumulate across windows for one cohort."""

    cohort: str
    state_dir: Optional[str] = None
    hazard: HZ.HazardEstimator = field(default_factory=HZ.HazardEstimator)
    spreads: SC.SpreadCensus = field(default_factory=SC.SpreadCensus)
    cf: CF.CounterfactualLedger = field(
        default_factory=CF.CounterfactualLedger)
    coverage: CV.CoverageLedger = field(default_factory=CV.CoverageLedger)
    #: PROFIT SPRINT. Why eligible states are refused, and whether being
    #: refused was good for us. Research only: the arm writes, never reads.
    attribution: AT.GateAttribution = field(default_factory=AT.GateAttribution)
    blocked: BL.BlockedLabeler = field(default_factory=BL.BlockedLabeler)
    #: ONE socket source and ONE pre-warmer per cohort. Both must outlive the
    #: window or pre-warming is pointless: a socket opened for the NEXT
    #: contract is worthless if it is dropped when THIS window ends.
    ws: Optional[Any] = None
    prewarm: Optional[Any] = None
    windows_seen: int = 0
    save_errors: int = 0
    last_save_error: Optional[str] = None

    # ------------------------------------------------------------ paths
    def _p(self, name: str) -> Optional[str]:
        if not self.state_dir:
            return None
        return os.path.join(self.state_dir, "%s.%s.json" % (self.cohort, name))

    def load(self) -> "CohortState":
        h, s = self._p("hazard"), self._p("spreads")
        if h:
            self.hazard = HZ.HazardEstimator.load(h)
        if s:
            self.spreads = SC.SpreadCensus.load(s)
        return self

    def save(self) -> Dict[str, Any]:
        """Best effort. A write failure never stops the collector."""
        out: Dict[str, Any] = {"saved": [], "errors": []}
        if not self.state_dir:
            return out
        try:
            os.makedirs(self.state_dir, exist_ok=True)
        except OSError as exc:
            self.save_errors += 1
            self.last_save_error = str(exc)
            out["errors"].append(str(exc))
            return out
        for name, obj in (("hazard", self.hazard), ("spreads", self.spreads)):
            p = self._p(name)
            try:
                obj.save(p)
                out["saved"].append(name)
            except Exception as exc:                             # noqa: BLE001
                self.save_errors += 1
                self.last_save_error = "%s: %s" % (name, exc)
                out["errors"].append(self.last_save_error)
        return out

    def ensure_ws(self, ws: Any = None) -> Any:
        if self.ws is None:
            self.ws = ws if ws is not None else WF.WsFlowSource()
        return self.ws

    def ensure_prewarm(self, *, prefix: str, minutes: int) -> Any:
        if self.prewarm is None:
            self.prewarm = PW.Prewarmer(cohort_name=self.cohort,
                                        prefix=prefix, minutes=minutes)
        return self.prewarm

    def window_done(self) -> None:
        self.windows_seen += 1
        self.save()

    def report(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "cohort": self.cohort,
                "windows_seen": self.windows_seen,
                "state_dir": self.state_dir,
                "save_errors": self.save_errors,
                "last_save_error": self.last_save_error}


_REGISTRY: Dict[str, CohortState] = {}


def state_for(cohort: str, *, state_dir: Optional[str] = None) -> CohortState:
    """The one state object for this cohort in this process.

    Created and loaded from disk on first use; returned as-is afterwards, so a
    new arm every window inherits the accumulated sample instead of starting
    over.
    """
    st = _REGISTRY.get(cohort)
    if st is None:
        st = CohortState(cohort=cohort, state_dir=state_dir).load()
        _REGISTRY[cohort] = st
    return st


def reset(cohort: Optional[str] = None) -> None:
    """Test hook only. Never called from the lane."""
    if cohort is None:
        _REGISTRY.clear()
    else:
        _REGISTRY.pop(cohort, None)
