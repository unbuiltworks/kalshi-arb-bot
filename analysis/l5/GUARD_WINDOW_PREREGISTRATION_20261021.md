# Pre-registration — defensive guard-window observation

**Written and committed BEFORE any observation was collected.** The point of
writing it first is that the stopping rule and the interpretation of a null
result cannot then be adjusted to suit whatever the data turns out to be.

Agreed with the owner, 2026-10-21.

## Question

Does an unmatched first leg, in the final minute of completion eligibility
`[T+660, T+720)`, encounter an executable exit that reduces its worst-case
residual loss?

## Stopping rule

**Up to 24 consecutive BTC15m windows (six hours).** Observation stops at 24
windows whatever the result. No extension.

## Pre-committed interpretation

| Outcome | Reported as |
|---|---|
| Unmatched positions reach the guard and exits are available | measured; report counts, depth, fees, loss reduction |
| Unmatched positions reach the guard and exits are NOT available | measured negative; the policy cannot help |
| **No unmatched position reaches the guard at all** | **INCONCLUSIVE — explicitly NOT evidence of safety** |

That last row is the one that matters. Zero stranded legs in 24 windows does
not mean stranding is rare; with 3 TWO_SIDED windows of history and one
stranding (2315z), the sample cannot distinguish a low rate from an unlucky
draw. A null result here says the experiment did not reach the question, not
that the risk is absent.

## What is measured, per guard tick

Executable bid/ask, displayed depth on the exit side, modelled unmatched
quantity, cost basis, frozen complement, estimated taker fee, worst-case
hold loss, worst-case exit loss, and the verdict — separately for the
conservative and optimistic fill bands.

## Constraints that bound the claim

- **Forward-only.** No BTC15m recordings exist locally and the recorder
  volume cannot be mounted from this session, so the 24 windows accrue in
  real time rather than from existing tape. "Use existing recordings first"
  is not available; this is the precise data limitation.
- **Simulated inventory.** Every position is modelled. No verdict here is a
  fill, and `simulated: True` rides each one.
- **Displayed depth is not a promise.** Depth is recorded as a ratio and
  never treated as a guaranteed fill.
- **An exit is not edge.** At the touch it is expected-value neutral before
  fees and negative by the fee. The question is tail reduction only.

## Not in scope

No live defensive submission, no campaign, no limit changes, no new
infrastructure.
