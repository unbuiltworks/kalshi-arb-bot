"""Pick the markets by measured activity, and measure it BEFORE observing them.

OWNER GO (#167): "wide universe at low cadence -> measure recent activity ->
rank by contemporaneous prints/shares/two-sidedness -> pin the 12 most active
markets -> observe those at 5-second resolution for 120 seconds. Crucially,
the ranking has to use flow measured BEFORE each persistence window. That
prevents accidentally selecting markets because we already know what happened
afterward."

WHY THIS EXISTS
---------------
The 5-second cadence fix was correct and it produced a worthless sample. At
BUDGET_RPS 3.0 a 5 s poll affords 12 markets, and `discover()` took the FIRST
12 the venue listed rather than the 12 that trade. Measured 2026-09-09: ten
county cricket matches and an Uzbek league game, 75 prints in total, two
markets with no print at all, and 4 opportunities across 2,136 observations.
The persistence buckets were arithmetically sound and answered a question
about markets nobody trades. Cadence was never the binding constraint on
that run; SELECTION was.

THE LOOK-AHEAD THIS MUST NOT COMMIT
------------------------------------
The tempting shortcut is to poll everything, then rank on the whole record
and report the busiest markets' windows. That ranks on flow which overlaps
the very interval being measured, so the "active" markets are partly selected
for having been active DURING their own windows -- and a persistence result
read off them would be an artifact of the selection, in the same family as
the six silent measurement failures this campaign has already found.

So the rotation is strictly two-phase, and the freeze is structural:

  SURVEY  wide universe, one batch per pass, no windows opened at all.
          Flow accumulates. Nothing is measured for the experiment.
  PIN     rank on the survey flow, SNAPSHOT the scores, and stop ranking.
  PINNED  windows open only on pinned slugs, scored only from the frozen
          snapshot. Flow keeps accumulating and CANNOT change the roster:
          `advance` ignores its `flows` argument entirely in this phase.

`eligible_for_window` therefore answers from `pinned_scores` -- the scores as
they stood at the pin -- never from a live recomputation. A market that was
never surveyed has no score and cannot enter a window, whatever it is doing
now.

UNMEASURED IS NOT QUIET
------------------------
`activity_score` returns None below `MIN_SURVEY_SAMPLES` intervals rather
than 0.0. A market with one poll behind it is unknown, and ranking it as
silent is exactly how a market gets pinned for being unobserved. This is the
same rule `pmus_persistence_window.resolvable` applies to time buckets: too
little data means UNRESOLVABLE, never zero.

Two-sidedness MULTIPLIES rather than adds, so a one-sided tape cannot buy its
way to the top of the roster on share volume alone. The complement we care
about is the other side of the book; a market that only ever prints at one
touch is not a market we can pair in.

This module selects markets. It proposes no rule, moves no gate, and places
no order.
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence

DEFAULT_ROSTER_SIZE = 12
# flow_delta yields one interval per CONSECUTIVE poll, so N intervals need
# N+1 cycles on the same batch.
MIN_SURVEY_SAMPLES = 4
SURVEY_CYCLES_PER_BATCH = MIN_SURVEY_SAMPLES + 1
# 120 s window at a 5 s poll is 24 cycles. Two consecutive windows plus
# margin, because the survey is expensive and the observation phase is the
# only part that produces evidence: the live universe measured 202 markets,
# which is 17 batches and ~425 s to survey once. One 150 s pin phase behind
# that is a 26% duty cycle; two windows takes it to ~39% at the cost of
# ranking on flow up to ~5 minutes old, which is well inside the timescale
# on which a live sports market changes character.
DEFAULT_PIN_CYCLES = 55
SHARES_PER_MIN_SCALE = 1000.0
# Print FREQUENCY dominates share SIZE, and deliberately so. The window asks
# whether the complement touch becomes reachable repeatedly inside 120 s,
# which is a recurrence property; one enormous print in an otherwise silent
# market does not offer a second chance to complete. Unweighted, a single
# 10^9-share print outscored a market printing in every interval -- the first
# version of this scorer did exactly that.
SHARES_WEIGHT = 0.5
ONE_SIDED_PENALTY = 0.5

SURVEY = "survey"
PINNED = "pinned"

LABELS = {
    "pre_window_flow": "the roster is ranked on flow measured strictly before "
                       "any pinned window opens; the scores are frozen at the "
                       "pin and never recomputed while windows are running",
    "unmeasured_is_not_quiet": "a market with fewer than %d flow intervals has "
                               "no score and cannot be pinned" % MIN_SURVEY_SAMPLES,
    "not_a_rule": "this selects which markets to observe; it gates nothing",
}


def batches(slugs: Sequence[str], size: int) -> List[List[str]]:
    """The wide universe cut into pollable batches. Order is preserved so a
    pass covers every market exactly once."""
    if size <= 0:
        return []
    return [list(slugs[i:i + size]) for i in range(0, len(slugs), size)]


def activity_score(flow: Mapping[str, Any]) -> Optional[float]:
    """Contemporaneous activity, or None when the market is UNMEASURED.

    None is not a low score. It means we have not watched this market long
    enough to say anything, and the caller must not rank it as quiet.
    """
    n = int(flow.get("intervals") or 0)
    if n < MIN_SURVEY_SAMPLES:
        return None
    prints = int(flow.get("prints_seen") or 0)
    spm = flow.get("shares_per_min")
    spm = 0.0 if spm is None else float(spm)
    print_rate = prints / n
    shares_term = SHARES_WEIGHT * (
        min(max(spm, 0.0), SHARES_PER_MIN_SCALE) / SHARES_PER_MIN_SCALE)
    # The penalty applies only when two-sidedness was actually DECIDED.
    # Measured on the first live pin: at 4 survey intervals not one of 18
    # scored markets had the two prints needed to decide it, so every market
    # took the same penalty and the term discriminated nothing while looking
    # like it did. Penalising an undecided market is not conservative, it is
    # a constant -- and a constant multiplier is an absent one.
    if not flow.get("two_sided_resolvable"):
        two_sided = 1.0
    else:
        two_sided = 1.0 if flow.get("two_sided") else ONE_SIDED_PENALTY
    return (print_rate + shares_term) * two_sided


def rank_by_flow(flows: Mapping[str, Mapping[str, Any]],
                 roster_size: int = DEFAULT_ROSTER_SIZE
                 ) -> List[Dict[str, Any]]:
    """Scored, measured markets only, best first. Ties break on slug so the
    same survey always produces the same roster."""
    scored = []
    for slug, flow in flows.items():
        s = activity_score(flow or {})
        if s is None:
            continue
        f = flow or {}
        scored.append({"slug": slug, "score": round(s, 6),
                       "intervals": int(f.get("intervals") or 0),
                       "prints_seen": int(f.get("prints_seen") or 0),
                       "shares_per_min": f.get("shares_per_min"),
                       # tri-state on the row: True / False / None undecided
                       "two_sided": (bool(f.get("two_sided"))
                                     if f.get("two_sided_resolvable") else None),
                       "two_sided_resolvable": bool(
                           f.get("two_sided_resolvable"))})
    scored.sort(key=lambda r: (-r["score"], r["slug"]))
    return scored[:roster_size]


def rotation_plan(universe: int, roster_size: int, poll_s: float,
                  pin_cycles: int = DEFAULT_PIN_CYCLES,
                  window_s: float = 120.0) -> Dict[str, Any]:
    """What one full rotation costs, stated at boot rather than inferred.

    The survey is the price of not being flow-blind, and it is not small. It
    belongs in the boot row so the duty cycle is visible before a run, not
    reconstructed from heartbeats afterwards.
    """
    n_batches = len(batches(list(range(universe)), roster_size))
    survey_cycles = n_batches * SURVEY_CYCLES_PER_BATCH
    survey_s = survey_cycles * poll_s
    pinned_s = pin_cycles * poll_s
    per_window = window_s / poll_s if poll_s > 0 else 0.0
    return {
        "universe": universe, "batches": n_batches,
        "survey_cycles": survey_cycles, "survey_s": round(survey_s, 1),
        "pinned_cycles": pin_cycles, "pinned_s": round(pinned_s, 1),
        "rotation_s": round(survey_s + pinned_s, 1),
        "windows_per_rotation": int(roster_size * (pin_cycles // per_window))
                                if per_window else 0,
        "observation_duty_cycle": (round(pinned_s / (survey_s + pinned_s), 4)
                                   if (survey_s + pinned_s) else None),
    }


class Roster:
    """Survey the wide universe, pin the busiest, observe only the pinned."""

    def __init__(self, roster_size: int = DEFAULT_ROSTER_SIZE,
                 pin_cycles: int = DEFAULT_PIN_CYCLES,
                 survey_cycles_per_batch: int = SURVEY_CYCLES_PER_BATCH):
        self.roster_size = roster_size
        self.pin_cycles = pin_cycles
        self.survey_cycles_per_batch = survey_cycles_per_batch
        self.universe: List[str] = []
        self.phase = SURVEY
        self.batch_index = 0
        self.cycles_in_phase = 0
        self.passes = 0
        self.pins = 0
        self.roster: List[str] = []
        # the frozen pre-window scores; the ONLY thing eligibility consults
        self.pinned_scores: Dict[str, float] = {}
        self.pinned_ranking: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- state

    def needs_universe(self) -> bool:
        """Rediscovery happens once per survey pass, not once per cycle."""
        return self.phase == SURVEY and not self.universe

    def set_universe(self, slugs: Sequence[str]) -> None:
        seen: List[str] = []
        for s in slugs:
            if s not in seen:
                seen.append(s)
        self.universe = seen
        self.batch_index = 0
        self.cycles_in_phase = 0

    def current(self) -> List[str]:
        """The slugs to poll this cycle."""
        if self.phase == PINNED:
            return list(self.roster)
        bs = batches(self.universe, self.roster_size)
        if not bs:
            return []
        return bs[min(self.batch_index, len(bs) - 1)]

    def eligible_for_window(self, slug: str) -> bool:
        """May a persistence window open on this market right now?

        Two conditions, both structural: we are in the observation phase, and
        this slug carries a score that was frozen BEFORE the phase began. A
        market cannot qualify on flow it generated during its own window.
        """
        return self.phase == PINNED and slug in self.pinned_scores

    # ---------------------------------------------------------------- clock

    def advance(self, flows: Optional[Mapping[str, Mapping[str, Any]]] = None
                ) -> Optional[Dict[str, Any]]:
        """One cycle elapsed. Returns a row when the phase changes.

        `flows` is read ONLY to decide a pin, and a pin only ever happens at
        the end of a survey pass. While pinned the argument is ignored, which
        is what makes the frozen ranking frozen.
        """
        self.cycles_in_phase += 1
        if self.phase == PINNED:
            if self.cycles_in_phase >= self.pin_cycles:
                return self._start_survey()
            return None
        bs = batches(self.universe, self.roster_size)
        if not bs:
            return None
        if self.cycles_in_phase < self.survey_cycles_per_batch:
            return None
        self.cycles_in_phase = 0
        self.batch_index += 1
        if self.batch_index < len(bs):
            return None
        return self._pin(flows or {})

    def _pin(self, flows: Mapping[str, Mapping[str, Any]]
             ) -> Dict[str, Any]:
        ranking = rank_by_flow(flows, self.roster_size)
        self.passes += 1
        if not ranking:
            # Nothing measurable in the whole universe. Survey again rather
            # than pin an arbitrary slate -- an unmeasured roster would
            # reproduce exactly the flow-blind selection this replaces.
            self.batch_index = 0
            self.cycles_in_phase = 0
            return {"kind": "roster_survey_empty", "phase": SURVEY,
                    "pass": self.passes, "universe": len(self.universe),
                    "why": "no market reached %d flow intervals"
                           % MIN_SURVEY_SAMPLES, "labels": LABELS}
        self.phase = PINNED
        self.pins += 1
        self.cycles_in_phase = 0
        self.roster = [r["slug"] for r in ranking]
        self.pinned_scores = {r["slug"]: r["score"] for r in ranking}
        self.pinned_ranking = ranking
        return {"kind": "roster_pinned", "phase": PINNED, "pin": self.pins,
                "pass": self.passes, "universe": len(self.universe),
                "roster_size": len(self.roster), "pin_cycles": self.pin_cycles,
                "ranking": ranking,
                "scored_markets": sum(
                    1 for f in flows.values()
                    if activity_score(f or {}) is not None),
                # If this is 0 the survey could not decide two-sidedness on
                # ANY market and the ranking is prints and shares only. Say
                # so on the row rather than leaving it to be inferred from a
                # column of identical falses.
                "two_sidedness_decided_on": sum(
                    1 for r in ranking if r["two_sided_resolvable"]),
                "unmeasured_markets": sum(
                    1 for f in flows.values()
                    if activity_score(f or {}) is None),
                "labels": LABELS}

    def _start_survey(self) -> Dict[str, Any]:
        row = {"kind": "roster_survey_start", "phase": SURVEY,
               "pass": self.passes + 1,
               "released": list(self.roster), "labels": LABELS}
        self.phase = SURVEY
        self.roster = []
        self.pinned_scores = {}
        self.pinned_ranking = []
        self.universe = []          # forces a fresh wide discovery
        self.batch_index = 0
        self.cycles_in_phase = 0
        return row

    # ---------------------------------------------------------------- report

    def status(self) -> Dict[str, Any]:
        return {"phase": self.phase, "universe": len(self.universe),
                "batch_index": self.batch_index,
                "batches": len(batches(self.universe, self.roster_size)),
                "cycles_in_phase": self.cycles_in_phase,
                "roster": list(self.roster), "pins": self.pins,
                "passes": self.passes,
                "pinned_scores": dict(self.pinned_scores)}
