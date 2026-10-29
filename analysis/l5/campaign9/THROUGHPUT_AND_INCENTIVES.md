# Throughput and incentives, C6–C8. What is worth changing, and what is not.

Scope: Campaigns 6, 7 and 8 — 17 windows, 87 live maker orders, 55 fill
events, $3.8861 settled realized net. Completed-pair economics and residual
inventory economics are kept separate throughout.

## 1. Rebates, incentives and fees actually credited: $0.0000

Every fill event across all three campaigns, from
`L5_FILL_CALIB_FEES_ACTUAL` / `tape_fees.jsonl`, source
`venue_order_progress.commission`:

| campaign | fill events | fees paid | rebates credited |
|---|---|---|---|
| C6 | 12 | $0.0000 | none (`rebate_usd: null` ×12) |
| C7 | 18 | $0.0000 | none (`rebate_usd: null` ×18) |
| C8 | 25 | $0.0000 | none (`rebate_usd: null` ×25) |
| **total** | **55** | **$0.0000** | **$0.0000** |

### Already included in realized net — as zero, and provably so

Realized net is derived from `available_cash_usd`, the authoritative venue
balance, so anything credited would appear in it. The Campaign 8 ledger
reconciles to the cent using **only** own-side acquisition costs and $1.00
pair redemptions, with **no fee or rebate term**. Any credit would have
broken that reconciliation. So the answer is not "rebates are excluded" —
it is that they are included and they are zero.

### The schedule predicts a rebate the venue does not pay

From the `fees.schedule_estimate` block on C8's 32 live presubmits
(`maker_fee_basis`: *"the published schedule has no maker-fee term; makers
receive a rebate"*):

| | total | per order |
|---|---|---|
| predicted maker rebate | **$0.1839** | $0.0057 |
| predicted maker fee | $0.0000 | — |
| **actually credited** | **$0.0000** | **$0.0000** |
| taker fee avoided by staying maker | $1.0213 | $0.0319 |

The $0.1839 shortfall is **15.3% of Campaign 8's entire +$1.2000 net**, so
this is not immaterial — it is simply not ours. **No rebate may be assumed
in any forward projection.**

The genuinely large incentive number is the one we already collect by
construction: **$1.0213 of taker fee avoided**, 85% of the campaign's net.
Maker-only execution is paying for itself; maker *rebates* are not paying at
all.

### Rounding behaviour, from the only rebates ever actually credited

`venue_rebate_credits.json` holds the 25 prints that ever paid us
(2026-09-06/07, **sports markets only** — `tsc-`/`asc-`/`aec-` slugs, never
BTC15m):

- total **−$0.4200** (negative commission = credit) over 119.49 contracts
- **flat per print, not proportional**: 8 prints at $0.01, 17 at $0.02,
  across quantities from 3.13 to 4.64 contracts
- implied average **−$0.003515 per contract**, but the per-contract rate is
  an artefact: the credit is a percentage rounded to the whole cent per
  print

The fee model in the gates rounds the same way — *"taker Theta, rounded UP
to the cent once per ORDER. Per-FILL rounding is not modelled and is bounded
by the live order count; see `fee_extra_per_order_usd`"* ($0.01).

**Consequence for throughput:** cent-rounded per-print credits mean rebate
value would scale with *order count*, not contract count. At our sizes a
$0.01 credit on a ~$2 leg would be ~50 bp — material if it existed. It does
not, on BTC15m.

## 2 & 3. The three throughput candidates, priced with queue-aware fills

**Fill model used throughout: the empirical rate, not price touches.** Of 87
live orders, **53 filled = 60.9%**, and queue depth does not predict it:

| queue_ahead quartile | range | fill rate |
|---|---|---|
| Q1 | 510–1,333 | 71.4% |
| Q2 | 1,347–2,071 | 66.7% |
| Q3 | 2,074–2,769 | 38.1% |
| Q4 | 2,775–5,260 | 66.7% |

Non-monotonic, no gradient; median `queue_ahead` 1,970.8 for fills vs
2,236.8 for non-fills. So a *newly placed* qualifying order is credited at
**60.9%**, and no counterfactual here counts a touch as a fill.

### (a) Longer completion dwell — additional orders: ~0, value ~$0

Of 34 pooled cancels, **19 were pulled before the median fill time
(10.8s)**. Holding all 19 to the deadline gives an upper bound of
19 × 0.609 ≈ **11.6 additional fills**.

That bound is not usable, for two independent reasons:

1. **Informative censoring.** We cancel precisely when the book has moved
   and the quote has gone stale, so any fill probability obtained by holding
   this tape's cancels open is biased upward by construction.
2. **Even if realized, it is worth nothing.** The settled break-even
   completion pair cost is **0.9532** and our achieved quantity-weighted
   pair cost is **0.9530**. Completing a stranded leg and letting it settle
   are economically indistinguishable at the prices we actually get, because
   a resting completion fills when the leg we already hold is becoming a
   winner.

Measured directly: completing all 7 C6–C8 stranded legs at 0.96 would have
changed settled net by **−$0.0877**.

### (b) Entry timing — additional orders: 1–4/window, value ~$0

C8 first-submission offsets ran **+34s to +189s** into a 600s entry window.
At the observed ~42s order cycle the lost time is nominally 1–4 orders per
window. But the evidence contradicts the lever outright:

| window | entry offset | pairs |
|---|---|---|
| 0215z | **+34s** (earliest) | **1** |
| 0230z | +47s | 3 |
| 0300z | +84s | 1 |
| 0245z | +119s | 0 |
| 0315z | +145s | 2 |
| 0200z | **+189s** (latest) | **4** |

The latest entry produced the most pairs. Entry timing is not the
constraint.

### (c) Selectively relaxing one-order concurrency — additional orders: 3, value ~$0

C6's `tape_slot_refusals.jsonl` is the only direct record: **23 ticks
collapse to 10 episodes** (3 refusals landed inside 50ms of each other — the
"don't confuse ticks with opportunities" lesson applies).

| role | episodes |
|---|---|
| FLAT_OPEN | 6 |
| NEW_OPEN | 1 |
| **COMPLEMENT** | **3** |

Only the 3 complements are economically interesting; the 7 opens are new
exposure the risk limits exist to block. All 10 carried `pair_cost 0.9500`.

**Settled value: $0.00.** Every C6 window closed fully matched with zero
residual, so nothing was lost by deferring those complements — they were
placed later and filled. And at pair 0.95 against a 0.9532 break-even, even
a converted complement is worth 3 × ~2.1 × 0.0032 ≈ **$0.02 total**.

### (d) The one candidate that is not zero: inter-order idle time

Measured across all 72 consecutive order pairs in C6–C8, as
*terminal of order N → submission of order N+1*:

| | n | p25 | median | p75 | max | mean |
|---|---|---|---|---|---|---|
| all gaps | 72 | 23.9s | **30.1s** | 62.7s | 314.8s | 57.1s |
| after a FILL | 45 | 23.5s | 29.8s | 57.5s | 189.8s | 48.2s |
| after a CANCEL | 27 | 24.5s | 31.4s | 113.3s | 314.8s | 71.8s |

**The gap is the same after a fill as after a cancel (29.8s vs 31.4s).** A
market-driven wait would not be. There is a floor around 24s with occasional
3.7s, so it is not a hard latency either.

Our order rests only **13%–44%** of each window's trading span; the
remainder is idle with no live quote. We use ~5.12 orders × 42s ≈ 215s of
the ~600s eligible window — **36% utilisation**.

Pairs track orders closely (C8: 9 orders→4 pairs, 7→3, 5→2, 5→1, 5→1,
1→0). Matched economics are **$0.0926 per completed pair**.

**Size of the prize, capital-bounded:** 0200z spent $7.3353 for 9 orders
≈ $0.82/order, so the $12 envelope supports ~14 orders/window against 5.12
observed. Pairs 1.83 → ~4.9, matched $0.1933 → ~$0.454/window:

> **+$0.26/window incremental, conditional on the gap being removable.**

A more conservative bound uses only what was *demonstrated live*: if every
window ran at 0200z's rate (+$0.3947 matched), that is **+$0.225/window**.

## 4. The single highest-credible change

| candidate | incremental settled profit/window | basis |
|---|---|---|
| maker rebates | **$0.00** | 0 of $0.1839 predicted, across 55 fills |
| longer completion dwell | **~$0.00** (−$0.0877 / 7 legs) | settled break-even 0.9532 vs achieved 0.9530 |
| entry timing | **~$0.00** | latest entry produced the most pairs |
| relax one-order concurrency | **~$0.02 total** | 3 complement episodes; C6 closed matched regardless |
| completion requote (prior proposal) | **negative** | see correction below |
| **close the inter-order idle gap** | **+$0.225 to +$0.26** | 30.1s median gap, 36% window utilisation, pairs ∝ orders |

**It is throughput, by an order of magnitude over everything else.**

### Correction to a prior proposal

`analysis/l5/COMPLETE_REQUOTE_REPLAY.md` scored requoting unfilled
completions at **+$16.2153** net at full conversion. That figure is
**invalidated** by C6–C8 settled outcomes. It charges residual at
**−$0.331449 per unit** (a cohort mean), and $17.9281 of its $16.2153 net is
that "residual loss avoided". The settled measurement across 12.82 stranded
contracts is **+$0.0468 per unit — the opposite sign**. Recomputed at the
measured rate, full conversion is **−$4.24**, not +$16.22. Its own
counterfactual median pair cost was **1.01** with 66.7% above par, which is
the same above-par problem from the other direction. The replay's own bars
say *"Authorises shipping: False"*; this correction says do not ship it at
all on that evidence.

### Two things I have to be straight about

**I have not established the mechanism of the 30s gap.** The obvious
candidate was the Oct-27 settlement-sweep treadmill (86 markets ÷ 1.5 rps
≈ 57s, serial, ahead of the decision path). **That is refuted here:** the
C8 heartbeat shows `settlement_sweep_read: 34` *cumulative* over ~3.4 hours
of process life, so the sweep is not running hot and
`PMUS_SETTLEMENT_SWEEP_CAP` is already doing its job. The same heartbeat
carries `ORDER:SUBMITTED: 32`, independently confirming the order count, and
`ts_completion:NO_INVENTORY_TO_COMPLETE: 418`.

So the +$0.225–0.26/window is **the size of a prize, not a forecast of a
fix**, and I will not name a code change for it on this evidence. Proposing
one now would be a guess dressed as an estimate.

**The decision tape that would show the mechanism rolled off** Railway
retention (~1.6h) before Campaign 8 could be reconciled. That is the single
reason this question is still open.

## 5. Campaign 9: the bounded comparison

**Zero strategy change. Zero risk-limit change. No new services. No flags.**

Campaign 9 is a measurement campaign against the Campaign 8 baseline, and I
am explicitly **not** promising incremental profit from it. It buys the one
measurement that unlocks the only non-zero lever found.

### Configuration — identical to Campaign 8

Same deployment branch and commit, same strategy, and unchanged:
$20 exposure, $12 cash envelope, $5 daily-loss halt, $20 cumulative-loss
halt, $3 residual cap, one live order, maker-only, existing pair-cost
ceilings including completion rescue, watchdog, 72 submission backstop.

### The one addition: continuous capture

`analysis/l5/capture_decision_tape.py` — append-only, idempotent (deduped on
`(utc, label, coid|slug|window)`, verified by double run: 67 appended then
0 appended). Polled at **≤30 minutes** while armed (≈3× the retention
margin), committing each poll, via this session's existing scheduled-wake
mechanism. Captures `L5_LIVE_DECISION`, `L5_FILL_CALIB_*`, `L5_LIVE_CASH`
and `L5_LIVE_HB` — the heartbeat because its counters are cumulative, so two
samples bracket each window.

### Pre-registered metrics

Primary, the mechanism question:

> **Every inter-order gap longer than 60s, classified as: (i) no intent
> generated; (ii) intent generated but gate-refused, naming the gate;
> (iii) order already resting; (iv) no affordable pair available at the
> book.**

This distinguishes the two possibilities that imply completely different
fixes — a **follower-bound** cadence (we only act when an L5 action is
admitted, so the cadence is L5's and acting on our own state is a strategy
change) versus an **internal gate** (fixable without changing strategy).

Secondary, directly comparable to C8: orders/window; idle-gap distribution
(after fill and after cancel, separately); pairs/window; quantity-weighted
pair cost; fill rate; completion attempts in every window that strands.

### What would make me drop this line of work

If C9's long gaps classify mostly as **(iv) no affordable pair available**,
then the idle time is the market's, not ours, the +$0.26/window prize is not
real, and throughput is not the bottleneck. That is a genuine possibility
and the metric is designed to show it.

### Authorization required

No deployment, no variable change, and no live execution until you approve.
On approval the mint parameters would be Campaign 8's, unchanged:

```
ASK_WINDOWS=8 ASK_EXPOSURE_USD=20 ASK_DAILY_LOSS_USD=5
ASK_DURATION_S=14400 ASK_OPEN=40 ASK_COMPLETE=40
ASK_REPLACE=64 ASK_TOTAL=72
```
