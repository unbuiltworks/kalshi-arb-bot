# The faithful L5 translation on PMUS — fidelity report

**Question.** Given each actual L5 decision, how closely can we reproduce L5's
resulting sequential inventory path on PMUS using the market actually
available to us at that instant?

Five authenticated, settled windows; 284 L5 actions; aggressor proven
1778/1778. SHADOW only — no order exists.

## Two findings that change earlier statements

**1. L5's zero-crossing was hidden by our own q=1 translation.**

| convention | L5_NATIVE crossed zero |
|---|---|
| counting each action as ±1 (q=1) | **1 / 5** |
| L5's own relative sizes | **4 / 5** |

Same actions, same order, same windows. Counting actions instead of shares
destroys exactly the geometry that produces crossings. My earlier report that
"in these windows L5 himself barely overshoots" was an artefact of our
translation, not a property of L5. The corpus rate of 84.3% and this sample
are no longer in obvious tension.

**2. Crossing to copy L5 did not cost price against him — by construction.**

Aggregate slippage is **−$4.88**, i.e. favourable. This is definitional, not
edge: we cross only when L5's price is at or through our touch, so the touch
is weakly cheaper than the price L5 actually paid. It says copying L5 by
crossing on PMUS was not expensive in this sample. It does not say we can
beat him, and it must never be read as one.

## The eleven points, venue-only (SAFETY_OVERLAY off)

| # | metric | result |
|---|---|---|
| 1 | action reproduction | **131 / 284 = 46.1%** (baseline q1+maker-only: 11 = 3.9%) |
| 2 | sequence agreement | first side agrees in 5/5; native longest run 14–35 vs track 3–21 |
| 3 | inventory-path agreement | correlation 0.63, 0.84, 0.49, 0.80, 0.18 |
| 4 | opposite acquisition | agrees 5/5 |
| 5 | zero-cross / overshoot | **3 MATCH, 1 over-reproduces, 1 under-reproduces** |
| 6 | matched vs residual | reported per window; residual dominates economics |
| 7 | maker/taker mix | **11 maker / 120 taker** |
| 8 | incremental translation cost | **−$4.88** slippage vs L5 (favourable, definitional) |
| 9 | fees / rebates | kept separate; taker fees and maker rebates never netted into trading P&L |
| 10 | complete-window economics | **−$2.88** across five windows |
| 11 | irreducible failures | **PMUS_TRANSLATION 153**, all venue/habitat |

Layer attribution, venue-only: every one of the 153 misses is
PMUS_TRANSLATION. `NO_SUBSEQUENT_INTERACTION` 98, `NO_BOOK` 35,
`QUEUE_AMBIGUOUS` 20. None is ours and none is L5's.

## What the safety overlay costs

| | reproduction | economics |
|---|---|---|
| venue-only | 131 / 284 = 46.1% | −$2.88 |
| with SAFETY_OVERLAY | 54 / 284 = 19.0% | **+$6.90** |

The overlay refuses 77 actions PMUS would have executed. It **more than
halves fidelity and turns the economics positive.** That is the central
tension and it is deliberately left unresolved: preferring the overlay
because it earns more would be choosing P&L over replication, which is
exactly what Phase 1 forbids. The overlay's refusals are recorded as ours,
never as L5 behaviour and never as a PMUS inability.

## Reproducing L5 on PMUS is a taker activity

120 of 131 reproduced actions required crossing. By the time L5's trade
reaches us, his price is almost always at or through our touch. A maker-only
translation is therefore not a conservative version of L5 — it is a
different strategy that reproduces 3.9% of him.

## The translation rule is parameter-free

No threshold, band, ceiling or discretionary branch exists inside it:
reproduce every L5 action by the closest executable means at the receipt
instant, and measure the cost rather than cap it. There is nothing in it that
could have been tuned to these five windows, which is why it can be validated
prospectively on subsequent realtime windows without re-specification.

Notably it was **not** adjusted to fix the 16:30 over-reproduction, even
though a suppression rule would have raised crossing agreement from 3/5 to
4/5. That would be fitting to windows already seen.

## What remains irreducible

`NO_SUBSEQUENT_INTERACTION` (98) and `NO_BOOK` (35) are habitat and venue
evidence. They are preserved, not fixed, and no change to L5_NATIVE may be
made to remove them. Together they bound reproduction at roughly 62% before
any execution choice of ours is considered.

## Not done

No live manifest. Fidelity is 46.1% venue-only with one window each of over-
and under-reproduction, and path correlation ranges 0.18–0.84. That is not a
demonstrated replication yet. The next step is prospective validation of this
unchanged rule on new realtime windows.
