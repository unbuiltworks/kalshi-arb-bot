# Ranked improvements

One percentage point of leg completion is worth **$0.06769 per dollar-hour** (~**$1.6747/day**). Observed completion **83.42%**, break-even **84.52%**, gap **+1.10 points**.

| completion points gained | $/dollar-hour | $/day | clears break-even |
|---|---|---|---|
| +1 | +0.0677 | +1.67 | no |
| +3 | +0.2031 | +5.02 | yes |
| +6 | +0.4061 | +10.05 | yes |
| +10 | +0.6769 | +16.75 | yes |

> measured per-leg economics are linear in completion rate, so otherwise incomparable proposals become comparable -- and it is why almost everything resolves to the same lever

> 'Reprice to where L5 moved' is fidelity. 'Pay another cent to get filled' is a new strategy. They are one tick apart in code and a world apart in kind, and no proposal here may drift from the first into the second.

## 1. Reprice a resting COMPLETE when L5's own touch moves

**BEHAVIORAL_FIDELITY_FIX** — L5's touch moved and our clone did not follow it. Following L5's price is cloning him. Paying up to get filled would be a new strategy, and this proposal is explicitly not that: the new price is whatever the model says, never a tick more.

**Evidence**

- THE MECHANISM ALREADY EXISTS AND IS SWITCHED OFF FOR COMPLETE. The live session re-quotes when L5's touch moves, under `if relation_ in ("open", "extend") and touch != px_`. In the tape 36 OPEN and 35 EXTEND orders exited via repriced:touch_moved and 0 of 868 COMPLETEs did.
- The crossing objection is already handled by an existing guard: `px >= opp_ask -> would_cross` runs ahead of the touch check for every relation.
- 404 accepted-but-unfilled COMPLETEs carry a rest book. In 164 (40.6%) L5's touch moved above where we sat, and in 96 of those the opposite ask reached that new touch.
- 27 of the 35 markets with an unfilled COMPLETE (77%) had at least one such moment.
- Placement is not the defect: we price AT L5's touch in 70.3% of unfilled completes.
- Distance, not patience, separates outcomes: filled completes sit a median 1 tick from the ask, unfilled 3 ticks (p75 8, max 48).
- On L5's own history, 98.5% of his multi-fill completions happen at more than one price.

*Source: src/l5_complete_no_fill_forensics.py, src/l5_completion_behavior.py*

**Expected improvement.** UPPER BOUND, not a forecast: 23.8% of unfilled completes had an L5-consistent reprice the book would have met. Conversion is unknown -- reaching a price is not filling it, and a re-quote loses queue position. At 3 points of completion this is +$0.20/dollar-hour and +$5.02/day; 1.10 points clears break-even. NOT AN EFFECT SIZE: EXTEND (which reprices) dies at the deadline 28.5% of the time against COMPLETE's 64.9%, but that gap is confounded by relation and must not be read as what the change would deliver.

**Blocked by.** the COMPLETE extension is live under a frozen gate that needs 48 forward hours and 25 firings. Two changes to the same surface at once would make neither attributable. This waits for that verdict.

**Bounded test**

- change — add "complete" to the relation tuple on the existing touch_moved re-quote condition -- one condition on a code path that is already live and already proven on OPEN and EXTEND. Nothing new is built.
- explicitly NOT — no price the model did not return; no crossing; no widening of any cap
- unchanged — ~$2.50 per action, $7.50 per market, concurrency 1, maker-only / post-only, daily loss stop, the frozen model sha
- new bound required — a per-session cap on reprices, and the existing consecutive-rejection breaker must cover reprices too -- more orders on the #93 surface is the risk this carries, and #93 concentrates on COMPLETE. Repricing also surrenders queue position, which is a real cost the upper bound ignores: 2% of unfilled completes had the ask reach our price and still did not fill.
- sample before reading — pre-register it the way the completion extension was: forward hours, sessions where it fired, settled venue-confirmed markets
- what would make it fail — reprices become rejections (the #93 surface), or completion rises while pair profit per matched pair falls -- the capacity curve's half-profit sensitivity moves break-even from 84.5% to above 93%, so a cheaper fill that costs price is not a win
- rollback — revert; it restores prior behaviour and costs nothing

## 2. Prefer markets with time left and two live sides

**EXECUTION_LAYER_IMPROVEMENT** — the model still decides what to do; this changes only which markets are put in front of it. That is selection, not behaviour -- but it is a real intervention and is not labelled a fidelity fix, because L5's own selection is not observable.

**Evidence**

- L5 pairs 93.3% of 15-minute windows against 65.7% of 5-minute ones.
- Within a window, 82.9% when he opens with 80-100% of it left against 38.0% with under 20% left -- monotone across five buckets.
- Balanced books (0.35-0.65) pair 74.6% on 5-minute windows and 97.7% on 15-minute ones; lopsided (<0.20 or >0.80) pair 49.7% and 33.3%.

*Source: src/l5_residual_state_replay.py*

**Expected improvement.** RANGE, and transfer-limited: these magnitudes are measured on L5's crypto history and our lane trades sports. The mechanisms -- time to work a position, liquidity on the far side -- are not crypto-specific, but the sizes are not sports measurements. Direction is well supported; magnitude is not.

**Bounded test**

- change — add time-to-settlement and book balance to the admission filter, shadow-only first
- explicitly NOT — no change to what the model decides once a market is admitted
- unchanged — every money cap, concurrency 1, the frozen model sha
- new bound required — measure the candidate supply cost first: 50% of flat cycles already admit nothing, and a tighter filter that starves the lane trades a completion gain for an opportunity loss
- sample before reading — run it as a shadow filter beside the live one and compare admitted sets before anything is enforced
- what would make it fail — admitted-candidate count falls materially, or the sports transfer does not hold and completion is flat
- rollback — shadow-only until it earns enforcement

## 3. Close #93 for EXTEND and COMPLETE

**EXECUTION_LAYER_IMPROVEMENT** — a rejected order is not a behaviour, it is a failure to transmit one

**Evidence**

- 17.9% of COMPLETEs die by venue rejection, against 68.5% by no-fill.
- OPEN is solved and the fix shipped: the authenticated book caught real mispricings live.
- EXTEND (median +1 tick, 18 rows) and COMPLETE (median +2 ticks, 16 rows) rejections are NON-crossing on the venue's own book, and both still read NO_CONTROL_YET -- zero accepted controls in those stages.

*Source: src/l5_rejection_forensics.py*

**Expected improvement.** NOT YET QUANTIFIABLE. Without accepted controls in those stages there is no comparison to make, and the honest answer is that the size of this is unknown rather than small.

**Bounded test**

- change — none yet -- accumulate accepted EXTEND/COMPLETE controls until within_stage leaves NO_CONTROL_YET
- explicitly NOT — no pricing change on a hypothesis
- unchanged — everything
- sample before reading — enough accepted controls in each stage for a matched comparison
- what would make it fail — n/a -- this is measurement
- rollback — n/a

## 4. More than one market at a time

**NOT_AN_OPPORTUNITY_ON_THIS_EVIDENCE** — premature rather than wrong. The slope through the current rung is negative, so scaling multiplies the loss; and the P&L arm is UNVERIFIABLE because on held cycles the selector never ran, so the universe a second slot would have drawn from was never recorded.

**Evidence**

- 41.9% of flat selection cycles admitted two or more markets, so SUPPLY would support a second slot -- but that is supply, not profit.
- At 83.4% completion the lane runs at -$1.84/day. Two slots would run at roughly twice that.
- Stranded residual already consumes 44% of capital-hours while being 25% of markets.

*Source: src/l5_capacity_curve.py, src/l5_concurrency_counterfactual.py*

**Expected improvement.** NEGATIVE at the current completion rate. Revisit once realized dollars per dollar-hour is positive, not before.

**Bounded test**

- change — none
- explicitly NOT — do not scale a negative unit economic
- unchanged — everything
- sample before reading — n/a
- what would make it fail — n/a
- rollback — n/a

## 5. Add capital

**NOT_AN_OPPORTUNITY_ON_THIS_EVIDENCE** — the money is not what is scarce

**Evidence**

- Mean capital in use is $1.03 against a bankroll of about $111 -- under 1%.
- The largest single position ever committed $19.31, 17% of it.

*Source: src/l5_capital_efficiency.py*

**Expected improvement.** ZERO. More money buys nothing while concurrency is 1 and the slope is negative.

**Bounded test**

- change — none
- explicitly NOT — do not request funding
- unchanged — everything
- sample before reading — n/a
- what would make it fail — n/a
- rollback — n/a

## Every number here is a bound

the evidence supports 'this many opportunities existed', never 'this many would have converted'. Each entry says which it is.

## Read-only

Nothing here is deployed. No cap, gate, threshold, band or concurrency setting moves without the owner's explicit go, and the frozen COMPLETE experiment is untouched.
