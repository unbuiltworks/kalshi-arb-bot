# Defect-B q1 fill calibration: PREREGISTRATION. Not authorized, not armed.

Builds on the captured private-WS evidence
(`evidence/PRIVATE_WS_FRAMES_20261001T1320Z.json`). Replay economics stay
quarantined. No schema probe rerun.

## The two mechanisms our contaminated replay conflated

**Case 1 — an OLD RESTING order the market later reaches or crosses.**
Genuinely unknown. A non-marketable maker rests; the book subsequently moves to
or through its price; does it fill, and is it recorded maker or aggressor?

**Case 2 — a REPLENISHMENT submitted at an ALREADY-MARKETABLE price.** This is
V3's actual mechanism, and it does **NOT need a live experiment**, because our
own order entry cannot perform it:

* every maker submission goes out `post_only=True` (`participateDontInitiate`),
  and a post-only order that would cross is refused by the venue rather than
  rested;
* the proven executor additionally refuses it locally via `X.would_cross(...)`
  before submission, raising `StaleRefuse`.

So the clone can never obtain a resting maker at a crossing price. **The replay
granted V3 exactly that**: a persisted leg at a stale price, `ahead0 = 0` because
the price was absent from the ladder, filled as maker. That is not a behaviour our
live system could reproduce at all.

**Therefore B's Case-2 branch is repairable NOW, conservatively and without live
risk**: a persisted quote whose price has ceased to be non-marketable must be
modelled as REFUSED/not-resting, never as a maker fill with zero queue ahead.
That alone removes the mechanism behind V3's +$1.5281/window.

What remains genuinely unobserved for Case 2 is the venue's *rejection* semantics
for a post-only crossing order. We will NOT test that by deliberately submitting
a crossing order — that is the manufactured condition we ruled out, and the
conservative model does not depend on the answer.

## Case 1: the minimum live experiment

**Design principle: do not chase the market.** The order is placed
non-marketable and then LEFT ALONE. No requote, no reprice, no cancel on an
adverse move. If the book never reaches it, that is a valid null observation. An
order that fills because we moved toward the market answers nothing about B.

| parameter | value |
|---|---|
| quantity | **1** |
| outstanding orders | **1** |
| price | post-only, **1-3 ticks below the executable touch**, non-marketable, `would_cross` refused |
| **entry price cap** | **0.50** — bounds a single unhedged contract |
| submissions, campaign total | **6** |
| windows, campaign total | **6** |
| **cumulative realized loss cap** | **$1.50** |
| stop on | 2 qualifying observations, the loss cap, 6 windows, UNKNOWN terminal state, feed staleness, any `_dispose()` invariant raise, 90s before window end |
| after a fill | ONE fee-safe complement via the proven reactive-take path; otherwise carry one contract to settlement |

**Worst realistic case:** a filled leg at 0.50 that settles worthless, repeated
until the $1.50 cap trips. Checked before every submission via
`worst_case_loss()`.

### A qualifying observation requires ALL of

1. our order resting non-marketable, with private `EXECUTION_TYPE_NEW` and REST
   `OPEN` both proved, and the pre-fill book state recorded;
2. the book subsequently moving to or through our price **on its own**;
3. a terminal outcome for our order — filled or not — with positive venue proof;
4. the public book/trade sequence across that interval complete enough to order
   events.

A null (book never reaches us) is recorded and does **not** count toward the two.

### Evidence captured, preregistered

* **private**: execution `type`, `tradeId`, `aggressor`, `transactTime`,
  `lastShares`, `lastPx`, `commissionNotionalCollected`, `cumQuantity`,
  `leavesQuantity`, `state`, every frame verbatim with wall + monotonic stamps;
* **public**: per-trade `id`, `taker.side`/`maker.side`, price, quantity,
  nanosecond `tradeTime`; full-depth BBO immediately before and after;
* **ours**: submission intent, ACK with timestamps, REST `OPEN` proof, displayed
  quantity at our exact price at submission and at each book transition, cancel
  attempts and results, terminal reconciliation, settlement and balance delta.

## The three admissible conclusions

1. **B repaired directly** — the observation establishes maker/aggressor
   treatment and queue consumption for a crossed resting order, and the fill
   model is corrected to match.
2. **B partially constrained** — Case 1 answered, some portion unresolved; the
   unresolved portion gets CONSERVATIVE replay semantics (no fill where ordering
   is unknowable, `UNRESOLVED` rather than an invented fill).
3. **A specifically defined second calibration is necessary** — named precisely,
   with its own minimum design, not an open-ended programme.

"Build more infrastructure" is not a fourth outcome.

## Then, and only then

Repair the simulator under corrected **A + B + C** semantics, rerun the
quarantined economics (Batch 1 and Batch 2 as development/regression, a fresh
untouched block as OOS), and report what survives once the generous execution
assumptions are gone. The question that matters is whether L5-style continued
acquisition while imbalanced still produces executable $/window — and a negative
answer found here is far cheaper than one found with scaled capital.

## Status

PREREGISTERED ONLY. Not authorized, not armed, zero orders. The risk envelope
above is the exact proposal for review.
