# Does our ≤0.96 pair gate remove most of L5? Yes — and that is the part that loses money

Read-only counterfactual labelling of L5's own switches. **No gate was changed.**

## The question

Canonical L5's first switch sits at a median attainable pair of ~0.99 with
`gain_per_unit` −0.06 and only 40.7% ≤0.96, while our spring discipline
prefers ≤0.96. So: is ≤0.96 a genuine economic filter, or a transfer
constraint that accidentally deletes most of L5?

## How much of L5 it removes

L5 switched in **12,977 of 17,046 markets (76.1%)**. His own first-switch pair
cost: p10 0.79 · p25 0.903 · **p50 0.9857** · p75 1.07 · p90 1.21.

| gate | allows | rejects | **% of L5's own switches suppressed** |
|---|---|---|---|
| **≤0.96** | 5,258 | 7,719 | **59.48%** |
| ≤0.97 | 5,750 | 7,227 | 55.69% |
| ≤0.98 | 6,308 | 6,669 | 51.39% |
| ≤1.00 | 7,585 | 5,392 | 41.55% |

**Confirmed: our gate suppresses ~60% of L5's actual switching behaviour.**
By cohort (≤0.96): BTC15m 67.42% · ETH15m 62.74% · BTC5m 53.48% · ETH5m
51.68%. Suppression is worse on fast switches (64.24% when ≤48 s vs 54.72%
slower) and in the mid band (0.35–0.65 opens: 62–64%).

## But the suppressed part is where L5 loses money

| cohort | markets | fills | trading cash | matched p50 | switches p50 |
|---|---|---|---|---|---|
| **ALLOWED** (≤0.96) | 5,258 | 235,464 (30.9%) | **+$61,761.02** | 0.8065 | 3 |
| **REJECTED** (>0.96) | 7,719 | 515,756 (67.8%) | **−$59,528.34** | 0.8581 | 6 |
| never switched | 4,069 | 9,800 (1.3%) | +$13,474.77 | — | 0 |

All switched markets together net only **+$2,232.68**. The rejected cohort is
**−$59,528**; the allowed cohort carries essentially all of the positive
trading cash.

## Does it forfeit the incentives? No — tested two ways

The obvious objection: rejected markets hold **67.8% of the fills**, and
rebates scale with volume. Incentive rows carry **no `conditionId`**, so
per-market attribution is impossible; two independent proxies were used.

| split | share by notional | share by `p(1−p)·qty` | proxy incentive | trading cash | **total** |
|---|---|---|---|---|---|
| ALLOWED (≤0.96) | 30.84% | 30.49% | $21,817 | +$61,761 | **+$83,578** |
| REJECTED (>0.96) | 66.70% | 67.57% | $48,349 | −$59,528 | **−$11,179** |
| never switched | 2.47% | 1.94% | $1,385 | +$13,475 | +$14,860 |

`p(1−p)·qty` is the shape of the venue's own maker-rebate formula and is the
more generous proxy for the rejected cohort (those switches cluster near
0.50, where `p(1−p)` peaks). **Both proxies agree: the rejected cohort is net
negative even after crediting it two thirds of lifetime incentives.**

## Verdict

**≤0.96 is a genuine economic filter, not merely a transfer artefact.** It
removes ~60% of L5's switches, and that 60% is the loss-making 60%. The
interpretation that L5 "extracts a small trading edge plus substantial
incentives over enormous turnover" is **not supported** in the form that would
justify relaxing the gate: the high-turnover, high-pair-cost portion does not
pay for itself even on a generous incentive split.

**Caveats, stated rather than buried:**

* Both incentive splits are **proxies**, not source-accounted. If rebates were
  concentrated in the rejected cohort far beyond its `p(1−p)` share, the sign
  could move — but it would take roughly **$59.5k of the $71.6k lifetime
  incentive** landing there to reach break-even, against a proxy share of
  $48.3k.
* This labels L5's **first** switch only.
* Selection runs both ways: a market that ends cheap-paired may be cheap
  *because* it went well, not well because it was cheap. This is an
  association in L5's realised history, not a causal claim about our gate.

**No gate is changed. No recommendation to relax ≤0.96 follows from this.**
