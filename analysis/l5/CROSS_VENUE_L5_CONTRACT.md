# Canonical L5 Cross-Venue Contract

This document is the research/implementation contract for one market-agnostic
L5 behavioral engine with venue-specific execution adapters. Gemini is not a
separate strategy.

Comparison target:

`Canonical L5 -> PMUS Sports -> Gemini 5m -> Gemini 15m`

## Shared behavioral state

Every venue must expose the same behavioral/accounting concepts:

- continuous UP/DOWN inventory
- running average cost on both outcomes
- matched inventory
- signed directional residual
- `gain_per_unit` as an explanatory state variable only
- effective pair cost
- sampled action: OPEN / EXTEND / COMPLETE / HOLD
- acted action
- shell override/refusal reason
- price/depth/spread/flow at each decision
- time to settlement
- side switches, same-side runs, and inter-action/inter-fill cadence

`gain_per_unit` is not a COMPLETE rule and is not an entry predictor.  It is
reported as the held residual side's executable mark minus that side's running
average cost. Positive gain mechanically makes the complementary side cheaper
relative to the held average.  The PMUS finding that entry price did not predict
later <=97c complement states (AUC ~0.497) means we must not build a predictive
entry story around this variable.

## Accounting contract

Matched-pair economics and residual economics are always separate.

Required fields/outputs:

1. UP quantity / DOWN quantity
2. running average UP / DOWN cost
3. matched quantity
4. residual quantity and sign
5. matched fraction (`2*matched/(up+down)` when inventory exists)
6. effective pair cost before fees and after known fees
7. matched-pair P&L
8. residual settlement P&L
9. fees
10. rebates/rewards separately
11. gross capital deployed / capital utilization
12. net per capital
13. status progression:
   `SHADOW -> SUBMITTED -> FILLED -> SETTLED -> REALIZED`

Shadow trade-through evidence never advances the accounting stage to FILLED.
Modeled settlement proxies never advance it to SETTLED or REALIZED.

## Action/fill conversion contract

For every venue/horizon report:

- fills / action
- OPEN conversion
- EXTEND conversion
- COMPLETE conversion
- maker vs taker attempts and fills
- switches / window
- runs / window
- favorable `gain_per_unit` states / window
- matched fraction
- terminal residual
- pair economics
- residual settlement P&L
- fees
- rebates/rewards
- gross capital deployed
- theoretical or realized net / capital, explicitly labeled

For Gemini, 5m and 15m are reported separately under the exact same field
names as PMUS.

## Finding classification

Every finding must be classified as exactly one of:

1. **SHARED L5 BEHAVIOR**
   - state variables
   - action selection
   - inventory cycling
   - persistence / switching behavior
   - gain_per_unit explanation

2. **VENUE-SPECIFIC EXECUTION**
   - tick size
   - spread/depth/queue
   - maker/taker conversion
   - post-only behavior
   - cancel/reprice behavior
   - ACK/fill latency

3. **VENUE-SPECIFIC ECONOMICS/SETTLEMENT**
   - fees
   - rebates/rewards
   - settlement source/rules
   - redemption/cashflow timing

A behavioral discovery on Gemini must be checked against canonical L5/PMUS
data. A PMUS behavioral discovery must be checked on Gemini. Do not transfer a
venue-specific execution/economic finding as though it were L5 behavior.

## Current PMUS findings that constrain Gemini interpretation

- L5 continuously works inventory rather than performing one open/complement cycle.
- Cheap complements are largely an accounting consequence of held inventory
  becoming profitable relative to its running average.
- Entry price did not predict later <=97c complement states (AUC ~0.497).
- L5 frequently EXTENDs while underwater and continues cycling inventory.
- More EXTEND under current PMUS conditions increased directional residual
  without increasing matched inventory; `more EXTEND = more profit` is invalid.
- PMUS COMPLETE maker conversion has been ~6%, materially below EXTEND.
- The first supported <=97c PMUS complement filled via the existing maker order
  at a better price than the hypothetical taker cross; crossing is not assumed
  to be the answer.
- `gain_per_unit` is explanatory, not a hard COMPLETE gate.

## Current Gemini evidence snapshot before canonical-reporting redeploy

From the long-running READ-ONLY queue shadow ending near 2026-09-14 02:47Z:

- 244 multi-fill inventory windows observed
- 16 windows with any strict hypothetical fill
- 24 strict hypothetical fills total
- 2 matched increments / completed flat segments
- 0 residual sign flips
- inside-spread complement counterfactual strict conversion around 28-33%
  depending on arm, but these are correlated counterfactual opportunities,
  not independent live fills
- canonical complement decision sample: 6 COMPLETE attempts, 1 strict
  completion at the canonical target (tiny sample)
- base stochastic shells showed materially better complement conversion than
  modal shells, but all results remain theoretical

Interpretation: Gemini may have better *execution opportunity* for completion
than PMUS in some states, but the clean continuous sample is still sparse and
cannot justify meaningful live capital. This is currently classified as
**VENUE-SPECIFIC EXECUTION**, not a new Gemini strategy.

## Live-capital rule

Volume alone never advances risk. Gemini must first demonstrate executable
both-side behavior in shadow and then bounded q=1 probes. Any live result must
be tracked through submitted -> filled -> settled -> realized before it is
counted as profit.
