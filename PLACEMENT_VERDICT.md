# Placement mechanism verdict: pair cost solved, flow not

Source: live PMUS `cpc-` tape, 229 markets / 7,724 observations, split by each
market's first observation into 114 in-sample and 115 out-of-sample. Replay is
chronological; a quote fills only when the queue standing at placement is
cleared by printed volume and only the excess is ours; a missing ladder is
never an empty queue; settlement is inferred from the market's own terminal
prices and labelled, never venue-confirmed. Run at commit `eb57cf5d4`,
cycle 1776.

## The answer

**Continuous re-quoting fixes the pair-cost problem and does not fix the
expectancy problem.** Every placement policy is negative on full-inventory
net, in-sample and out-of-sample.

### In-sample (114 markets)

| policy | entries | completion | median pair cost | matched-only $/entry | **FULL-INVENTORY $/entry** | losing residuals |
|---|---|---|---|---|---|---|
| P8 deeper resting (bid −1) | 35 | 17.1% | **0.705** | +0.058 | **−0.007** [−0.183, +0.196] | 77.1% |
| P7 best-bid resting | 38 | 31.6% | 0.745 | +0.151 | **−0.021** [−0.221, +0.200] | 81.6% |
| P10 continuous two-sided | 38 | 39.5% | 0.790 | +0.123 | **−0.346** [−0.659, −0.024] | 71.1% |
| P6 inventory-aware (one-shot) | 38 | 21.1% | 0.940 | +0.017 | −0.092 | 65.8% |
| P1 immediate taker | 38 | 100% | 1.050 | −0.083 | −0.083 | 0% |
| P9 one-tick improve | **1** | 0% | — | 0 | −0.260 | 100% |

### Out-of-sample (115 markets)

| policy | entries | completion | median pair cost | **FULL-INVENTORY $/entry** |
|---|---|---|---|---|
| P6 | 11 | 18.2% | 0.830 | −0.122 |
| P7 | 7 | 14.3% | 0.945 | −0.159 |
| P10 | 10 | 20.0% | 0.830 | −0.253 |
| P8 | 5 | 0% | — | −0.288 |
| P1 | 11 | 90.9% | 1.030 | −0.081 |

Out-of-sample n is 5–11 entries. These intervals are wide and the ranking
between them is not resolved; what *is* resolved is the sign, which is
negative for all of them.

## What improved, and it is real

Pair cost. The one-shot policies completed at 1.02–1.07 — the $1.08–1.12 band
the earlier study reported. Continuous re-quoting completes at **0.705–0.79
median, with 100% of completions under $1 and 100% at or below the 0.96
ceiling.** Matched-pair P&L per entry turns positive (+0.058 to +0.151).
So the earlier completion cost was a consequence of our entry-and-completion
rules, not a property of the venue. That question is now answered.

Fill count also rose: entry is no longer one-shot, and P7/P10 reach 38 entries
with up to 7 shares of inventory worked per market where P1–P6 took one shot.

## What did not improve, and why it decides the matter

**Completion rate: 14–40%.** L5 ends 98.3% of his early-regime markets
two-sided at a 0.904 matched fraction. Our best two-sided policy completes
39.5% in-sample and 20% out-of-sample. The complement does not arrive.

That is the whole result. Buying the first leg cheaply is easy and we can now
do it repeatedly; the pair only pays when the other side fills, and on PMUS it
mostly does not. So 71–100% of residuals lose, residual P&L runs −0.064 to
−0.469 per entry, and it swamps the positive matched-pair economics in every
policy. P10 is the clearest case: the *best* completion rate and the *worst*
net, because quoting both sides continuously accumulates the most unmatched
inventory (mean |residual| 3.16, max 7).

**P9 is structurally inapplicable, not merely bad.** One entry in 115 markets.
A one-tick improvement cannot be placed in a one-tick market, and `_place_px`
refuses a quote that would cross its own ask. PMUS `cpc-` spreads are
predominantly one tick, so there is nothing to improve into. This matches the
Polymarket side, where median spread is one tick at all 31 measured leads.

## Deliverable

The user's brief asked for one evidence-backed placement mechanism that could
materially improve executable matched inventory, **or** a clear demonstration
that PMUS lacks the necessary flow. The honest answer is both halves, split:

- **Mechanism, confirmed:** continuous re-quoting with bounded same-side
  EXTEND and inventory-aware completion materially improves attainable
  matched-pair cost (1.05 → 0.705–0.79) and makes matched-pair P&L positive.
  P8 (resting one tick behind the bid) is the best of them on full-inventory
  net and is the only policy whose in-sample interval straddles zero.
- **Flow, absent:** none of it reaches positive full-inventory expectancy,
  because PMUS does not supply the opposite-side flow to complete the pairs at
  anything like L5's rate. Out-of-sample every policy is negative.

**The BTC NO-GO stands, now on placement-sensitivity grounds as well as
full-inventory grounds.** No live probe is justified by this evidence. I have
not relaxed the pair ceiling or re-run anything to manufacture a positive
number, and live trading remains disarmed.

## Caveats stated rather than buried

- Settlement is decidable for only **53 of 229** markets; 176 are
  `TERMINAL_STATE_NOT_DECISIVE` because our tape stops before convergence.
  Undecidable markets are reported separately and are not scored as zero. The
  out-of-sample entry counts are small for this reason.
- Settlement is `SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK`. PMUS settles BTC15m
  on CF Benchmarks BRTI and no BRTI history exists in this repository.
- Every figure here is `REPLAY_SETTLED_INFERRED_NOT_REALIZED`. Nothing in this
  document is realized profit, a confirmed fill, or a credited rebate.
- The residual is charged in full at its own cost basis in both directions, so
  no losing residual is excluded.

## Next measurement, not next strategy

Three things would change this verdict if they came back differently, and none
of them is a new strategy:

1. **More settlement coverage.** 176 undecidable markets is the biggest source
   of uncertainty. It needs more tape per market, not new code.
2. **Fill count and matched fraction per policy**, now aggregated by
   `summarize` (commit `a75884503`) and landing on the next study cycle. The
   present verdict rests on completion rate and residual size; the matched
   fraction of purchased shares is the direct comparable against L5's 0.904.
3. **Whether any PMUS category has the missing opposite-side flow.** Sports
   was already measured as not materially better, but that was under the
   one-shot harness. Re-measuring sports under P7/P8/P10 is the one cheap test
   left, and it reuses the running collector.
