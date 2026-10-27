# Three corrections to PATH_TO_FIVE_DOLLARS.md

Owner-directed, and all three are right. The errors are mine.

## 1. $0.128/window is a SCENARIO, not established expectancy

**What I wrote:** "$0.128/window is the repeatable number."

**Why that was wrong.** I took L5's unpaired-first-leg return of −46.8%
(n=378, his corpus, his clip, his venue) and applied it to our three
strands to get an expected residual of −$0.8421. That is a borrowed prior,
not our measurement. Our own residual came in at **+32.3% of cost** on
n=3. Calling the result "the repeatable number" dressed one scenario as an
estimate.

**What the evidence actually supports — a band, labelled:**

| scenario | residual assumption | net/window | basis |
|---|---|---:|---|
| **observed** | our 3 strands as they landed | **$0.2225** | 15 windows, venue-confirmed |
| **observed ex-windfall** | drop 1415z's +$1.7612 redemption | **$0.1051** | removes the single largest term |
| **L5-prior residual loss scenario** | −46.8% of residual cost | **$0.1277** | L5 corpus n=378, transfer unproven |
| matched surplus alone | residual excluded entirely | $0.1838 | 35.84 matched contracts |

**Established expectancy: none of them.** n=15 windows and n=3 strands
cannot separate these. The residual term's sign is not established, let
alone its size. $0.128 is the middle scenario and is now labelled as such
everywhere it is used.

## 2. First-leg prediction is NOT mathematically impossible

**What I wrote:** "No decision-time predictor of completion-vs-strand can
exist... the split *is* the sign of the next BTC move."

**Why that was wrong.** The identity `UP_own + DOWN_own = 1.00 + spread`
holds at an instant and does establish that completion needs a favourable
move. It does **not** establish that nothing predicts the *distribution* of
subsequent moves. Two concrete counter-examples I skipped past:

- **Volatility is not direction.** Completion needs a ≥5¢ move of the right
  sign; strand risk is the wrong sign. But the probability that the
  complement becomes reachable *at all* rises with realized volatility,
  which is decision-time observable and is a two-sided quantity. A
  predictor of move *magnitude* raises completion probability without any
  directional skill.
- **The side is ours to choose after the fact.** Across several rounds the
  engine may open on either side, so the relevant event is "some leg
  completes", not "this leg's side was right". That is a different and
  weaker condition than a signed forecast.

So the correct claim is narrower: **on our data no first-leg predictor is
testable, because every opening plan carries a constant** —
`depth_ticks: 2, pair_cost: "0.9500", edge_per_contract: "0.0500"` on
every plan in 1500z, whatever the book — **and n=3 strands has no power.**
What I showed is that the engine currently emits no varying decision-time
signal to regress on, and that the variables already tested (own price,
elapsed time, inventory ratio, side at open) failed. Not that none exists.

The missing evidence, unchanged: ~200 of our own first-leg cohorts with
venue-confirmed settlement (≈17 campaigns), or an offline corpus carrying
both a book and fills, which neither L5's corpus nor the public tape does.
A realized-volatility feature at decision time is a legitimate candidate
and is untested.

## 3. Expected loss is not the probability of halting

**What I wrote:** a table of expected cumulative residual loss over 8
windows against the $5 daily halt, concluding the halt "caps the achievable
target at roughly $1/window". That compared an expectation to a threshold,
and it also measured the wrong quantity: the halt fires on cumulative
**realized net**, which includes matched surplus, not on residual losses
alone.

**Redone as a probability.** 200,000 simulated 8-window days. Per window:
strand with p = 3/15; a stranded leg of cost C either redeems (payoff
`C·(1/px − 1)`, px = 0.283 measured) with probability w, or expires
worthless. Matched surplus $0.1838/window at k=1, scaled by k. The halt
fires the first time cumulative realized crosses −$5.

**P(halt fires inside an 8-window day):**

| target $/window | k | w = 0.16 (L5-implied) | w = 0.33 (ours, n=3) | w = 0.50 |
|---|---:|---:|---:|---:|
| $0.128 | 1.0 | 0.0% | 0.0% | 0.0% |
| $0.39 | 3.0 | 1.9% | 0.8% | 0.3% |
| $1.00 | 7.8 | **21.2%** | **13.0%** | 7.1% |
| $2.50 | 19.6 | 42.0% | 31.7% | 22.2% |
| $5.00 | 39.2 | **47.5%** | **36.7%** | 26.9% |

**And the expectation, which is a different question with a different
answer:**

| target | k | w | E[net]/window | E[residual]/window | E[8-window day] |
|---|---:|---|---:|---:|---:|
| $0.128 | 1.0 | 0.16 | +$0.095 | −$0.052 | +$0.76 |
| $0.128 | 1.0 | 0.33 | +$0.168 | +$0.021 | +$1.35 |
| $1.00 | 7.8 | 0.16 | +$0.740 | −$0.407 | +$5.92 |
| $5.00 | 39.2 | 0.16 | +$3.719 | −$2.044 | +$29.76 |

**The two disagree, and that is the point.** At $5/window the day is
strongly positive in expectation (+$29.76) and still hits the halt on
**27–48% of days**. A halt is not a loss of the expectation — it is a stop
that ends the day's earning. The constraint is therefore not "the halt caps
the target at $1/window" as I claimed; it is:

> Above roughly $0.39/window the $5 daily halt starts firing often enough
> to truncate a positive expectation, and by $1/window it fires on 7–21% of
> days. Scaling past that needs the halt re-scaled with the capital — a
> risk-appetite decision — and the number to decide against is the halt
> probability, not the expected loss.

`$0.39/window remains reachable inside every existing limit`, with a 0.3–1.9%
halt probability. That part of the earlier report stands.

## What stands unchanged

- Unit economics: $0.07692 matched surplus per matched contract at a 0.9231
  mean achieved pair cost, measured over 35.84 contracts.
- Envelope utilisation: `sized_by: CASH_ENVELOPE` on every plan, ~30% of
  the configured $12.00 used.
- 53% of the headline +$3.3376 is 1415z's single directional redemption.
- Capital for $5/window: ~$140 of gate obligation, agreeing with the
  independent $107–138 from L5's corpus.
- No rebate is inside any net figure, and `rebate_credited_usd: 0` means
  never read.
