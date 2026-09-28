# COMPLETENESS_RULE_AMENDMENT_001 — prospective clarification

**Authorized by:** owner, 2026-09-27.
**Recorded at:** 2026-09-27T23:57:12Z (commit `fea39051`), **before 2315z was scored under it** — the
amendment is written down first so the sequence cannot later be read as an
economics-driven exception fitted to one window.
**Status:** PROSPECTIVE CLARIFICATION, not a retroactive exception.

## Previous rule, as it stood

> A window is usable only if it was observed from first open through a TERMINAL
> row ON THE TAPE, **and no bbo gap exceeds 32s**, and it has at least one
> two-sided state, and settlement is finalized (`settlementPx` 1.0000 or 0.0000
> AND differing from `lastTradePx`).

The 32s figure is `HEARTBEAT_S` 30 plus one 2s poll: it is a test of whether the
**tradable** period was densely observed.

## Amended rule

    max_preclose_observation_gap <= 32s

A post-close gap between the final tradable observation and the terminal-state
publication **does not count** toward the 32s observation-gap test, provided all
six elements hold:

1. the tape continuously observed the market through its scheduled close;
2. the final pre-close observation occurred ≤32s before the scheduled boundary;
3. no pre-close observation gap exceeds 32s;
4. the **same collector process** subsequently captured an authoritative terminal
   state through normal or drain polling;
5. settlement is finalized under the existing settlement rule;
6. no post-close price/book information is used to make replay decisions.

## Rationale

The bounded drain (COLLECTOR_001/002/003, `DRAIN_S`=240) exists because the venue
publishes a terminal state **after** the scheduled boundary — measured between 6s
and 2m58s late. Keeping the slug polled until that arrives is what stopped 31% of
windows being destroyed. But the drain, by construction, leaves a quiet stretch
between the last tradable quote and the terminal row, because a closed market's
book stops changing.

Under the previous wording that quiet stretch counted as an observation gap, so
**the fix for one data-loss mechanism triggered a different exclusion rule.** The
32s test was written to guarantee a densely observed *tradable* period; a gap that
lies entirely after the close cannot affect that, and cannot affect any decision
either, because `run_window` only ever acts on rows whose state is
`MARKET_STATE_OPEN` — element 6 is structural in the scorer, not a promise.

## What this does NOT do

* It does **not** relax the requirement for a terminal state on the tape. Element 4
  still demands one, captured contemporaneously by the collector.
* It does **not** permit reconstructing a missing terminal observation from later
  venue data. A window whose close was never observed stays excluded forever.
* It is applied **mechanically to every window** by
  `scripts/pmus_completeness_rule.py`, not to 2315z specifically. Previously
  excluded windows are re-evaluated only against all six elements, on their own
  contemporaneous tapes.
* It changes no economics: θ, the q grid, the fill model, the frozen arm, the
  decision rule and the $5 lock threshold are all untouched.

## Expected effect, stated before running

2015z, 2100z, 2130z, 2145z, 2200z and 2230z have **no terminal row on the tape at
all**, so element 4 fails and they remain excluded. 1815z fails the separate
two-sided-states requirement. 2315z is expected to pass. Recorded in advance so the
outcome can be checked against the prediction rather than the prediction against
the outcome.
