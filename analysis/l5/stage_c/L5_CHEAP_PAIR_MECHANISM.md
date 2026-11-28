# How L5 creates low-cost paired inventory

Read-only, existing pipeline, on commit `7a8a9305`. Executor untouched and
still disarmed; no limit raised, no crossing enabled.

## 0. Retraction first: the 0.98 ceiling is NOT validated

Validated on a **chronological** split (TRAIN 2026-03-25..07-06, TEST
07-06..08-31, 957 windows each, no shuffling), reporting net **dollars**:

| ceiling | TRAIN net | TEST net | TEST 90% bootstrap |
|---|---|---|---|
| <= 0.96 | **+$14,314** | +$13,296 | [+10,304, +16,549] |
| <= 0.97 | +$14,217 | +$16,480 | [+13,462, +19,753] |
| <= 0.98 | +$13,641 | +$18,146 | [+15,005, +21,481] |
| <= 0.99 | +$12,968 | +$18,976 | [+15,287, +22,840] |
| <= 1.00 | +$12,247 | **+$19,036** | [+15,793, +22,598] |
| none | +$3,908 | +$13,056 | [+9,398, +17,339] |

**The optimum flips**: TRAIN maximises at the tightest ceiling (0.96), TEST at
the loosest (1.00), and the bootstrap intervals overlap across every level.
My "0.98 maximises net" came from the **pooled** set, which averaged two
different regimes. **I withdraw it as a recommendation.**

What *is* robust in both halves: **some** ceiling beats none (TRAIN +$12,247
vs +$3,908; TEST +$13,296 vs +$13,056). Our engine already has one — opening
cap 0.96, completion line 1.00. The specific level is not resolvable here.

Note also that a ceiling **does not avoid the residual**: the unmatched loss
stays at −$9,200 to −$19,500 across every level, because the first leg
already filled. Refusing to complete does not un-buy a leg.

## 1. Audit

**The two baselines reconcile exactly.** $17,394 counts all 1,914 reconciled
windows; $16,964 is the pair-cost table, which cannot bucket a window with no
pair. The difference is **19 one-sided windows** carrying **+$429.57** of pure
directional residual and $0.00 of matched P&L.

**Selection bias is real and material.** Exclusion tracks activity:

| | included | excluded |
|---|---|---|
| n | 1,914 | 3,635 |
| median trades | **83** | **121** |
| windows with >=101 trades | **37.8%** | **63.2%** |
| median pair cost | 0.9671 | 0.9729 |

The reconciled subset **under-represents high-activity windows by ~1.7x** —
exactly the regime that carries the matched edge. Every per-bucket rate for
the 151+ buckets rests on a thinned sample and should be read as indicative.

**The economics are not stationary.** TRAIN **+$4.18**/window
(+0.00695/matched contract) against TEST **+$13.9975**/window
(+0.01344) — a 3.3x difference in level across halves of the same account,
with pair cost nearly identical (0.9656 vs 0.9676). Any rule tuned on level
rather than on mechanism will not survive.

**The $480/day target is used nowhere as evidence.** It was only ever a smell
test that caught a 12.5x-wrong figure; it validates nothing.

## 2. The mechanism: the discount is earned LATE, by buying the
## out-of-the-money leg cheaply

Pair cost L5 would have locked had it stopped at each quartile — median,
**both halves agree**:

| stopped at | TRAIN | TEST |
|---|---|---|
| 225 s (25%) | 0.9810 | **0.9904** |
| 450 s (50%) | 0.9765 | 0.9798 |
| 675 s (75%) | 0.9661 | 0.9723 |
| 900 s (100%) | **0.9589** | **0.9648** |

Pair cost **falls monotonically through the window**: −0.0221 on TRAIN,
−0.0256 on TEST. So **most of the 3.5–4.1 cent discount is earned after the
first quartile, and about half of it after the midpoint.** It is not an
entry effect.

And the late trades are deep out-of-the-money: **44.6%** of all trades in the
final quarter are priced **below 0.25**.

That is the mechanism. L5 opens both sides near 0.50, extends whichever side
the market favours, and **completes the pair by buying the losing side at
0.10–0.36 late in the window**. A leg bought at 0.11 paired against one
bought at 0.54 is a 0.65 pair; blended across the window it lands at ~0.96.

## 3. There is no predictive skill in it — four independent checks

| check | result | reading |
|---|---|---|
| residual P&L per contract | **−$0.0937** | directional exposure is a cost |
| residual win rate | **42.2%** | worse than a coin flip |
| basis position within each side's traded range | **0.476 / 0.472** | ~neutral (0.5), not dip-timing |
| basis-improving trades that follow a price fall | **46.9%** | random |
| entry discount at a **zero** time gap | **+0.0100** | a mechanical floor exists |
| entry discount by gap: 1–10s / 11–60s / 61–300s / >300s | +0.010 / +0.020 / **+0.030** / **−0.020** | oscillation harvest, and it **reverses** past 300 s |

The reversal beyond 300 s is the tell: if L5 were predicting direction, a
longer wait would help. It hurts. L5 is harvesting **mean reversion inside
the window** and the deep discount on the side the market has abandoned —
neither of which requires alpha.

**This is good news for transferability**: the mechanism is mechanical.

## 4. Representative sequences

**Losing window** `btc-updown-15m-1787984100`, pair 0.9978, net **−$43.79**,
settled Down. Opens Down 50 @ 0.49. Then **chases Up as it rises** — 0.54,
0.58, 0.65, 0.64 — driving pair cost to **1.1048** and the residual to 60+
contracts. It spends the rest of the window repairing that: Down at 0.41,
0.34, 0.32 pulls pair back to 0.9978, but 37 trades of churn end roughly
flat on pairs and lose on the residual. **Extending into a rising price at
0.58–0.65 is what cost the window.**

**Winning window** `btc-updown-15m-1784747700`, pair 0.9099, net **+$52.31**,
settled Up. Entry pair was **1.05 — above a dollar** and therefore bad. Every
cent of the discount came later: Up extended through 0.55→0.88 as it rose,
then Down bought at **0.36, 0.11 x6, 0.10** once abandoned. The cheap
completions are the entire result.

The marginal-value column in `l5_sequences.py` is reported at the window's
actual outcome, so for the residual it is **hindsight-contaminated** and
should be read for the matched leg only. Stated rather than presented as
clean attribution.

## 5. PMUS transferability

**Transfers:** the mechanism needs no alpha, and a maker-built pair is
**fee-free on both venues** — L5's rows carry no fee (`usdcSize == size *
price` exactly), PMUS charges no maker commission (`actual_fees_usd: 0`
across 20 Stage A maker orders).

**Our engine already finds this exact setup and cannot act on it.** Stage A
2145z: held DOWN at 0.45, UP collapsed to **0.10**, implied pair **0.55** —
L5's mechanism precisely. It was refused, 41 times run-wide, as
`WOULD_CROSS`: at the out-of-the-money level the fill was only available by
crossing, and maker-only forbids it.

**The open question is the one I cannot answer from this data**: L5's
0.10–0.11 fills may be maker or taker. Polymarket publishes no queue
position, PMUS no print endpoint. **So I do not assume those fills are
available to us as a maker.** That is the single thing the next experiment
must measure.

**Does not transfer:** completing by crossing. PMUS theta 0.0695 is
~$0.02/contract, against a late-window discount of ~0.022–0.026/contract —
it would consume most of the mechanism's value, which is consistent with the
Stage B finding that crossing is fee-negative in expectation.

## 6. The decision rule

**Rule — out-of-the-money patient completion: as soon as the complement's own
price falls below 0.25, post the completion leg as a resting maker bid at the
out-of-the-money level and hold it, rather than only evaluating completion
near the touch and ceasing at the 720 s cutoff.**

Why this one, against the alternatives:

- It targets **acquisition cost**, not volume. It buys inventory more
  cheaply; it does not trade less (the ceiling rule) or more (frequency).
- It is the mechanism that is **stable across train and test** — pair cost
  falls monotonically in both halves — unlike the ceiling, whose optimum
  flipped.
- It raises **no limit**: no change to `max_live_orders`, opening size, round
  allocations or capital, and no crossing.
- Our own `ts_complete_cutoff_s` is **720 s**, which cuts off 180 s of the
  final quarter in which L5 still earns ~0.0075/contract and takes 44.6% of
  its trades below 0.25.

**Estimated PMUS economics, stated with its uncertainty:** the late-window
discount is 0.022–0.026 per matched contract. At Stage A size (~2 contracts
per window) that is **$0.04–$0.05 per window** — negligible. At L5's median
opening (19.6 contracts) it would be **$0.43–$0.51 per window**. The rule is
therefore **not a profit fix at our current size**; it is a prerequisite that
must work before size could matter. Conditional on the fill actually being
reachable as a maker, which is unmeasured.

## 7. Smallest bounded validation

On the existing 24/7 shadow stream — no capital, no new service, no arming:

**Measure whether a resting maker bid at the out-of-the-money level is
reachable on PMUS.** The recorded `L5_SHADOW_RESIDUAL_BOOK_SAMPLE` already
carries full bid/ask rungs and `queue_ahead_bound` whenever modelled
inventory is unmatched. For every sample where the complement's own price is
below 0.25, record: whether a bid one tick inside the complement's ask sits
**within published depth**, the size displayed there, and the queue ahead of
it.

- **Success:** a sub-0.25 maker bid is inside published depth in a material
  fraction of those samples, with queue-ahead comparable to our size. Then
  the mechanism is reachable maker-only and the cutoff change is worth
  proposing.
- **Failure, which I commit to reporting as such:** those levels show no bid
  depth or unbounded queue-ahead. Then L5's cheap completions are
  **taker-only**, the mechanism does not transfer under maker-only, and the
  honest conclusion is that this strategy requires crossing authorization or
  it does not work — not that we need a different quoting tweak.

## 8. Not established

- 65.5% of L5's windows are unusable and excluded, with exclusion biased
  toward high-activity windows.
- Level is non-stationary (3.3x between halves); only the *mechanism* is
  stable.
- These are L5's **fills**, not quotes. Nothing here shows where it rested an
  order, nor whether its 0.10 fills were maker or taker.
- Per-bucket rates are cross-window associations, not causal.
- No incentive or rebate is counted on either side.
- Nothing here is realized P&L for us, and no order was submitted.
