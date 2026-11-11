"""COVERAGE IS A DENOMINATOR PROBLEM, and getting it wrong invents a result.

THE FAILURE THIS PREVENTS. A socket that has just connected cannot vouch for a
trailing flow window, so its first ~120 seconds produce cycles on which the
flow gates CANNOT BE EVALUATED. On a 300-second BTC5m contract that is ~40% of
the window, and it lands on exactly the early-window states the experiment
exists to study.

If those cycles are counted as entry refusals, the arm looks like a strategy
that rejects early-window states. It is not. It is a strategy that was not
WATCHING yet. The two are opposite findings and they would be reported as the
same number.

So every cycle is classified into exactly one bucket:

    COVERAGE_WARMUP_EXCLUDED   the socket could not vouch for the interval;
                               the strategy was never asked, and this cycle is
                               NOT a refusal and NOT eligible
    STRATEGY_ELIGIBLE          coverage was vouched and the strategy decided
    NOT_QUOTING                the cohort policy was not seeking entry here
                               (WITHDRAW / INVENTORY mode) -- also not a
                               refusal, because nothing was asked

and within STRATEGY_ELIGIBLE, a decision is either ADMITTED or a REAL refusal
with a named gate.

THE REPORTED DENOMINATOR IS THEREFORE:

    total_cycles
      = warmup_excluded + not_quoting + strategy_eligible
    strategy_eligible
      = entries_admitted + strategy_refusals

and an admission RATE is only ever computed against `strategy_eligible`. A rate
over `total_cycles` would silently fold a coverage gap into a strategy verdict.

COVERAGE IS NOT WEAKENED TO BUY ELIGIBILITY. The fix for a warm-up gap is to
SUBSCRIBE EARLIER (see `prewarm.py`), never to relax what "vouched" means. A
decision still uses only a vouched interval, and nothing observed later is ever
backfilled into an earlier decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

SCHEMA = "gemini_econ_coverage/v1"

WARMUP_EXCLUDED = "COVERAGE_WARMUP_EXCLUDED"
STRATEGY_ELIGIBLE = "STRATEGY_ELIGIBLE"
NOT_QUOTING = "NOT_QUOTING"
BUCKETS = (WARMUP_EXCLUDED, STRATEGY_ELIGIBLE, NOT_QUOTING)

LABELS = (
    "A cycle the socket could not vouch for is COVERAGE_WARMUP_EXCLUDED. It "
    "is NOT an entry refusal: the strategy was never asked.",
    "Admission rates are computed against strategy_eligible ONLY. A rate over "
    "total cycles would report a coverage gap as a strategy verdict.",
    "Coverage is never weakened to buy eligibility. The remedy for a warm-up "
    "gap is to subscribe earlier, not to relax what vouched means.",
)


@dataclass
class CoverageLedger:
    """Per-cohort cycle accounting. Counts only; decides nothing."""

    total_cycles: int = 0
    warmup_excluded: int = 0
    not_quoting: int = 0
    strategy_eligible: int = 0
    entries_admitted: int = 0
    strategy_refusals: int = 0
    #: how many eligible cycles produced a counted outcome, and how many
    #: attempts were refused for being in the wrong bucket or over-counting
    counted_decisions: int = 0
    decisions_refused_wrong_bucket: int = 0
    decisions_refused_over_count: int = 0
    #: warm-up cycles split by whether pre-warming had been possible at all
    warmup_prewarmed: int = 0
    warmup_not_prewarmed: int = 0
    #: how long each contract took to become vouched, in seconds from first
    #: sight of the contract
    time_to_vouched_s: Dict[str, float] = field(default_factory=dict)
    first_seen_s: Dict[str, float] = field(default_factory=dict)

    def see(self, symbol: str, t: float) -> None:
        self.first_seen_s.setdefault(symbol, float(t))

    def cycle(self, *, symbol: str, t: float, vouched: bool,
              quoting: bool, prewarmed: bool = False) -> str:
        """Classify one cycle into exactly one bucket and return it."""
        self.total_cycles += 1
        self.see(symbol, t)
        if not vouched:
            self.warmup_excluded += 1
            if prewarmed:
                self.warmup_prewarmed += 1
            else:
                self.warmup_not_prewarmed += 1
            return WARMUP_EXCLUDED
        # first vouched cycle for this contract: record how long it took
        if symbol not in self.time_to_vouched_s:
            self.time_to_vouched_s[symbol] = round(
                float(t) - self.first_seen_s.get(symbol, float(t)), 2)
        if not quoting:
            self.not_quoting += 1
            return NOT_QUOTING
        self.strategy_eligible += 1
        return STRATEGY_ELIGIBLE

    def decision(self, admitted: bool, *, bucket: Optional[str] = None
                 ) -> bool:
        """ONE ELIGIBLE CYCLE's outcome. Returns whether it was counted.

        GUARDED, because the first live run proved the guard necessary: it
        printed `eligible=0 ... refusals=4`, which is arithmetically
        impossible. Two mistakes produced it, and both are now refused here
        rather than trusted to the caller:

          * the caller invoked this on cycles the socket could not vouch for,
            so warm-up exclusions were being recounted as strategy refusals --
            the exact conflation this whole module exists to prevent;
          * it invoked it once per ACTION, so a cycle proposing two rungs
            recorded two outcomes against one cycle.

        A decision is counted only when `bucket` is STRATEGY_ELIGIBLE, and only
        as many times as there are eligible cycles -- `counted_decisions`
        cannot exceed `strategy_eligible`.
        """
        if bucket is not None and bucket != STRATEGY_ELIGIBLE:
            self.decisions_refused_wrong_bucket += 1
            return False
        if self.counted_decisions >= self.strategy_eligible:
            self.decisions_refused_over_count += 1
            return False
        self.counted_decisions += 1
        if admitted:
            self.entries_admitted += 1
        else:
            self.strategy_refusals += 1
        return True

    # ------------------------------------------------------------ report
    def report(self) -> Dict[str, Any]:
        ttv = sorted(self.time_to_vouched_s.values())
        n = len(ttv)
        return {
            "schema": SCHEMA,
            "total_cycles": self.total_cycles,
            "coverage_vouched_cycles": (self.total_cycles
                                        - self.warmup_excluded),
            "warmup_excluded_cycles": self.warmup_excluded,
            "not_quoting_cycles": self.not_quoting,
            "strategy_eligible_cycles": self.strategy_eligible,
            "entries_admitted": self.entries_admitted,
            "strategy_refusals": self.strategy_refusals,
            # the ONLY admission rate that means anything
            "admission_rate_of_eligible": (
                (self.entries_admitted / self.strategy_eligible)
                if self.strategy_eligible else None),
            "warmup_share_of_total": (
                (self.warmup_excluded / self.total_cycles)
                if self.total_cycles else None),
            "warmup_prewarmed": self.warmup_prewarmed,
            "warmup_not_prewarmed": self.warmup_not_prewarmed,
            "contracts_seen": len(self.first_seen_s),
            "contracts_vouched": n,
            "median_time_to_vouched_s": (ttv[n // 2] if n else None),
            "buckets_are_exclusive": (
                self.warmup_excluded + self.not_quoting
                + self.strategy_eligible == self.total_cycles),
            # the invariant the first live run violated
            "accounting_consistent": (
                self.entries_admitted + self.strategy_refusals
                == self.counted_decisions <= self.strategy_eligible),
            "eligible_cycles_without_a_decision": (
                self.strategy_eligible - self.counted_decisions),
            "decisions_refused_wrong_bucket":
                self.decisions_refused_wrong_bucket,
            "decisions_refused_over_count": self.decisions_refused_over_count,
            "labels": list(LABELS),
        }
