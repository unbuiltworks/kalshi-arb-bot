# Sequential taker-transition autopsy

P0, 2026-09-21. Venue-supplied maker/taker labels. No optimization, no strategy
inferred from P&L — P&L is not read anywhere in this analysis.

## VERDICT

> ## FALSIFIED: taker is not materially associated with complementing accumulated inventory

H1 fails in both eras, in the same direction, under every null, and the one
transition it names is the rarest of the four measured.

## What the label is, and what bounds it

`/trades?takerOnly=true` returns the subset of a user's trades where they were
the taker, so a fill in the full set and absent from the taker set is a MAKER
fill. Verified as a subset rather than assumed: in the overlapping window 0.40%
of taker rows were absent from the full set (pagination edge) against **80.86%**
of full rows absent from the taker set.

**The endpoint cannot label history, and this was measured rather than assumed.**
Max usable offset is exactly **10,000** (10,001 → HTTP 400, binary-searched), and
`startTs`, `endTs`, `before`, `after`, `from` and `min_timestamp` are **all
silently ignored** — every one returned the newest trades unchanged. So the
760,817-fill corpus cannot be labelled this way. The autopsy runs on two
complete labelled windows three weeks apart.

| window | fills | markets | taker rate | span |
|---|---|---|---|---|
| TRAIN_20260830 | 10,500 | 312 | **19.1%** | 08-30 07:49 → 20:52 |
| OOS_20260920 | 10,488 | 410 | **7.3%** | 09-20 01:09 → 09-21 01:32 |

L5 is **100% BUY** across both (n=20,988) — he never sells, so inventory is
monotone and EXTEND/OPPOSITE is just whether the outcome repeated.

**The ordering hazard, stated up front.** Timestamps are whole seconds and
**53.4%** of fills share a second with another fill in the same market. Only
13.2% of those groups are a single transaction, so for most the true order is
not recoverable. Every headline is recomputed under three tie-breaking rules
and the spread reported.

## 1–2. The two direct contrasts

| | TRAIN | OOS |
|---|---|---|
| P(TAKER \| EXTEND) | 15.4% (n=7,204) | 4.7% (n=6,416) |
| P(TAKER \| OPPOSITE) | 20.8% (n=2,984) | 4.2% (n=3,662) |
| P(OPPOSITE \| TAKER) | 31.0% (n=2,006) | 20.2% (n=768) |
| P(OPPOSITE \| MAKER) | 27.8% (n=8,494) | 36.1% (n=9,720) |
| RD, OPPOSITE−MAKER | **+0.031** [+0.005, +0.057] | **−0.159** [−0.188, −0.131] |

Item 2 **reverses sign between eras** and the OOS effect is five times larger in
the opposite direction: recently, takers are markedly *less* likely to be
opposite-acquiring.

## 3–4. By prior same-side run — the decisive table

| run | TRAIN P(TAKER) | TRAIN P(OPP+TAKER) | OOS P(TAKER) | OOS P(OPP+TAKER) |
|---|---|---|---|---|
| 1 | 23.4% | 8.3% | 7.5% | 2.2% |
| 2 | 21.8% | 8.4% | 4.3% | 1.7% |
| 3 | 16.8% | 6.0% | 2.8% | 1.4% |
| 4 | 13.9% | 5.4% | 1.2% | 0.2% |
| 5 | 11.8% | 3.8% | 1.9% | 1.1% |
| 6–10 | 8.0% | 2.1% | 1.0% | 0.5% |
| 11+ | 4.6% | 1.9% | 0.9% | 0.0% |

**Monotonically DOWN in both eras.** H1 predicts the opposite: the longer the
same-side run, the more taker should be used to complete. It is used *less* —
by 5× across the range in TRAIN and 8× in OOS.

This is not a time-to-settlement confound. Within every TTE band the run
gradient holds, and the confound would push the other way (longer runs occur
later, and later means *more* taker):

| TRAIN, TTE band | run 1 | run 2–3 | run 4–10 | run 11+ |
|---|---|---|---|---|
| 0–60 s | 67.0% | 56.2% | 32.9% | 8.6% |
| 61–180 s | 49.3% | 45.7% | 25.7% | 6.0% |
| 181–420 s | 26.5% | 18.1% | 6.4% | 4.9% |
| 421–900 s | 3.6% | 5.8% | 5.4% | 3.4% |

## 5–6. By inventory imbalance

| \|imbalance\| | TRAIN P(TAKER) | OOS P(TAKER) |
|---|---|---|
| 0 | 87.2% (n=321) | 76.2% (n=424) |
| 1–2 | 32.5% | 6.0% |
| 3–5 | 41.8% | 13.6% |
| 6–10 | 35.9% | 11.4% |
| 11–25 | 33.6% | 9.5% |
| 26+ | 15.1% (n=9,139) | 3.7% (n=8,731) |

**The zero bucket is an artifact and must not be read as an inventory effect:
97.2% / 96.7% of it is the market's FIRST fill**, which has zero inventory by
construction. Read correctly it is the real finding of this section —
P(TAKER | OPEN) = **87.2% / 76.6%**. Beyond the open, taker use *falls* as
imbalance grows, again opposite to H1.

## 7–8. Time

| seconds to settlement | TRAIN | OOS |
|---|---|---|
| 421–900 | 5.1% | 1.3% |
| 181–420 | 21.1% | 7.0% |
| 61–180 | 41.0% | 15.2% |
| 0–60 | **51.6%** | **21.3%** |

A 10× and 16× swing, monotone, both eras. **Time-to-settlement is the dominant
driver of taker use.** Seconds since the prior opposite fill also rises
(TRAIN 7.6% → 30.4%) but is far weaker and partly collinear with it.

## 9. Transitions out of MAKER EXTEND

| transition | TRAIN | OOS |
|---|---|---|
| → MAKER EXTEND | 70.8% | 65.5% |
| → MAKER OPPOSITE | 23.9% | 33.6% |
| → TAKER EXTEND | 3.6% | 0.5% |
| → **TAKER OPPOSITE** | **1.8%** | **0.4%** |

The transition H1 is about is the **rarest of the four in both eras**. After a
maker extend, the next action is a maker action 94.7% / 99.1% of the time.

## H1 against its nulls

P(TAKER|OPPOSITE) − P(TAKER|EXTEND), cluster-bootstrapped over markets:

| | TRAIN | OOS |
|---|---|---|
| **OBSERVED** | **+0.0536** [+0.032, +0.080], RR 1.35 | **−0.0043** [−0.012, +0.003] |
| null: labels shuffled in-market | +0.0562 [+0.036, +0.080] | −0.0059 |
| null: actions shuffled in-market | +0.0531 [+0.032, +0.079] | −0.0017 |
| state-matched (price × TTE, 40 strata, 100% coverage) | +0.0280 | −0.0039 |
| tie-break spread (corpus/tx/shuffle) | +0.0489 … +0.0583 | −0.0043 … −0.0029 |

**The nulls reproduce the observed effect almost exactly.** A within-market
label shuffle preserves each market's taker rate and action composition while
destroying any within-market association — and it returns +0.0562 against an
observed +0.0536. So the small positive TRAIN effect is **between-market
composition**, not a within-market behavioural link. The state-matched arm halves
what remains. In OOS the observed effect is zero and **sign-reversed**.

The tie-break spread never changes a sign, so the verdict is not an artifact of
the 53.4% ambiguous ordering.

## Era split

The eras differ in the *level* of taker use (19.1% vs 7.3%) and item 2 reverses
sign. They do **not** differ in the H1 relationship: it fails in both, in the
same direction, with the same monotone run-length gradient. That is why the
verdict is FALSIFIED and not MIXED — the era dependence is in how much L5 takes,
not in whether taking complements inventory.

## The simplest state machine justified by this result

Taker is an **entry and urgency** instrument. It is not an inventory-completion
instrument.

```
                 ┌─────────────────────────────────────────┐
                 │  OPEN                                   │
                 │  cross to establish the first position  │
                 │  P(taker) = 87% / 77%                   │
                 └──────────────────┬──────────────────────┘
                                    │
                 ┌──────────────────▼──────────────────────┐
             ┌──▶│  WORK  — rest as maker, either side      │
             │   │  P(taker) falls with run length:         │
             │   │  23% at run 1 → 5% at run 11+            │
             │   │  EXTEND and OPPOSITE are both MAKER      │
             │   └──────────────────┬──────────────────────┘
             │                      │  time, not inventory
             └──────────────────────┤
                                    ▼
                 ┌─────────────────────────────────────────┐
                 │  URGENCY — as settlement nears, cross    │
                 │  P(taker) 5% → 52% as TTE 900s → 0s      │
                 │  gated by CLOCK, not by imbalance        │
                 └─────────────────────────────────────────┘
```

Three states, two transition drivers, and **inventory is not one of them**. The
only place inventory enters is that an OPEN has none by definition.

## What this retires

- The OPEN→EXTEND→COMPLETE reading, insofar as it implied the complete leg is
  taken aggressively. It is not: OPPOSITE is a maker action 95–99% of the time.
- Any model that escalates aggressiveness as imbalance grows. Measured, L5 does
  the reverse.
- Any transfer rule keyed to run length as a completion trigger.

What a transfer rule *would* have to reproduce is the clock: near-zero taker use
early in a market's life and heavy crossing in the last minute.

## Not claimed

- Two windows, not the full corpus — the venue API cannot label history.
- 53.4% of fills share a second; ordering is recovered only up to a rule, and
  the spread is reported rather than hidden.
- Why the era level shifted from 19.1% to 7.3% is **not explained** here.
