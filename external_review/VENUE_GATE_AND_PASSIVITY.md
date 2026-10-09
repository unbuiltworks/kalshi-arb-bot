# The venue gate, corrected — and L5 is predominantly a resting maker

Scripts: `../scripts/90_across_time_pair_gate.py` (v1, **superseded**),
`../scripts/91_across_time_gate_v2.py`, `../scripts/92_passive_vs_aggressive.py`.
Results: `ACROSS_TIME_GATE_v2_polymarket.json`,
`PASSIVE_VS_AGGRESSIVE_RESULT.json`.

Data: Polymarket's own 1-second bid/ask ladders (Zenodo 22084977),
**8 coins**, 2026-08-21..24 — 733 settled markets, 369,164 usable
snapshots.

## Three errors of mine, all of which inflated the first answer

My first run reported *"mechanism possible on 92.5% of markets"*. That was
wrong three times over. Stating them because they are the same error class
this project keeps hitting.

1. **Stale/crossed quotes.** v1 took a minimum over each window. 3.4% of
   snapshots are flagged `crossed` (bid above ask) — stale or erroneous —
   and a minimum is precisely the statistic a bad quote captures.
2. **Survivorship.** v1 reported pair cost among markets where *both* legs
   filled and silently dropped the one-sided cases. An unpaired leg is a
   directional bet settling at $1 or $0; keeping only the completed pairs
   measures the strategy's good branch.
3. **A unit bug.** I converted microsecond timestamps as if nanoseconds,
   which silently produced zero matches rather than an error.

## The corrected simultaneous baseline

With crossed rows excluded, measured at the touch rather than as a
window minimum:

| | value |
|---|---|
| snapshots | 169,254 |
| median `ask_Up + ask_Down` | **1.0200** |
| share below $1.00 | **0.0001** (17 snapshots) |

**Polymarket's simultaneous pair is essentially never below $1.00 either**
— the same result previously found on Gemini (0 of 24,520) and Kalshi
(0 of 2,269). That test does not distinguish venues. It was never the
discriminator.

## Taking the touch across time loses money — on L5's own venue

Causal policy: walk forward, buy one unit of a leg the first time its ask
is at or below θ; settle every market, including one-sided ones, using the
dataset's own `is_winning_outcome`.

| θ | markets | engaged | mean P&L/market | CI95 | share profitable |
|---|---|---|---|---|---|
| 0.30 | 733 | 678 | −0.0242 | [−0.048, +0.002] | 0.291 |
| 0.35 | 733 | 685 | **−0.0216** | [−0.047, +0.003] | 0.381 |
| 0.40 | 733 | 690 | −0.0369 | [−0.060, −0.013] | 0.460 |
| 0.44 | 733 | 692 | −0.0353 | [−0.057, −0.014] | 0.561 |
| 0.46 | 733 | 693 | −0.0319 | [−0.053, −0.012] | 0.624 |
| 0.48 | 733 | 693 | −0.0430 | [−0.061, −0.024] | 0.666 |
| 0.50 | 733 | 557 | −0.0349 | [−0.050, −0.019] | 0.580 |

**Every threshold is negative.** Most have confidence intervals entirely
below zero, before fees and before any depth constraint. Negative on all
eight coins individually. Note the shape: at θ=0.46 the *median* market
makes money (62% profitable) but the mean is negative — a fat left tail
from one-sided residuals. That is exactly L5's own structure, and it is
why the survivorship error mattered.

So a strategy of **taking the offered price when it looks cheap does not
work**, on the very venue and in the very week L5 was achieving a 0.98
median pair cost.

## Therefore L5 was not taking — and the book confirms it

3,097 L5 fills matched to the prevailing non-crossed book within 30
seconds:

| | value |
|---|---|
| at or below the prevailing best **bid** — passive | **0.6503** |
| inside the spread | 0.0097 |
| at or above the prevailing **ask** — aggressive | 0.3407 |
| mean L5 fill price | 0.4711 |
| mean prevailing mid | 0.4829 |
| **mean fill minus mid** | **−0.0119** [−0.0137, −0.0102] |
| mean fill minus ask | −0.0174 |

**About two-thirds of L5's fills land at or below the prevailing bid** —
the signature of resting and being crossed into, not of lifting an offer.
And L5 buys on average **1.2 cents below mid**, with a confidence interval
well clear of zero.

This answers the passive-versus-active question that the fill record alone
could not: **predominantly passive, with a real but minority aggressive
component.** It is also consistent by a second, independent route — the
`takerOnly` feed over a different window implies a maker share around 80%.

## What this settles, and what it does not

**Settled:** the 3.9-cent-per-contract advantage is a **maker** edge. It
comes from resting below mid and being crossed into. It is not obtainable
by taking the touch — that loses money on all eight coins.

**Not settled, and now the single blocking question:** whether a resting
quote on a *candidate* venue gets crossed into often enough, and at a
small enough adverse-selection cost, to reproduce it.

That cannot be answered from the data in hand. The simultaneous-pair test
is useless (0.01% everywhere). The across-time taking test is settled
(negative everywhere). What remains requires knowing the fill behaviour of
**our own resting orders** on the candidate venue — which needs either a
venue tape carrying our quotes or a bounded live maker probe. No amount of
further analysis of L5's public fills will produce it.

## Caveats

* 3,097 fills is **0.4%** of the corpus, over four days, all late regime.
  The profitable pre-incentive era (Mar–Jul) has no book at all.
* "At or below the bid" is a price-level test, not a true maker flag.
* The 30-second snapshot tolerance means the book may have moved between
  the quote and the fill.
* Depth is not modelled anywhere here: these are touch prices, so every
  figure is an upper bound on an implementable strategy.
