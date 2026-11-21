# Why MIRROR_RAW has lower amplitude than L5_NATIVE

Phase 1, measurement only. Nothing here proposes or applies a change to the
band, controller, sizing, timing, persistence or market selection.

Five windows now carry authenticated tape and have settled: 16:15, 16:30,
16:45 (yes 0.0000, DOWN), 17:30 and 19:30 (yes 1.0000, UP). The aggressor
mapping is proven on 1778/1778 rows. The decomposition below is scored only on
windows where the STRICT stage is evaluable, i.e. where authenticated
interaction evidence exists for the actions. Windows without it report
`STRICT_NOT_EVALUABLE` and are excluded rather than guessed.

## The headline correction

The zero-crossing gap is much smaller than it looked, because the earlier
comparison was against the wrong population.

| | crossed zero |
|---|---|
| L5_NATIVE, in these windows | 1 / 7 |
| MIRROR_RAW, in these windows | 1 / 7 |

The 84.3% figure is L5's **historical corpus** rate. In the windows we actually
hold evidence for, **L5 himself barely overshoots**: his own `overshoot_past_zero`
is 0 in six of seven and 1 in the seventh. Comparing our clone against the
corpus rate, rather than against L5_NATIVE in the same windows, manufactured a
discrepancy that the like-for-like comparison does not support.

This is not a clean bill of health. The rates match at 1/7 but the crossings
are in **different windows** — L5 crossed in 19:30, MIRROR_RAW in 16:45 — so
the shapes do not coincide, and seven windows with eleven acquisitions is a
weakly powered sample. It does mean the amplitude question cannot be settled
on this evidence, and that the windows carrying authenticated tape are
systematically not L5's overshoot-heavy ones.

## Where the actions are actually lost

290 L5 actions across the STRICT-evaluable windows, each attributed to the
FIRST stage it failed at:

| stage | cause | actions | share |
|---|---|---|---|
| venue | REQUIRES_CROSSING | 120 | 41.4% |
| venue | NO_BOOK | 41 | 14.1% |
| evidence | NO_SUBSEQUENT_INTERACTION | 98 | 33.8% |
| evidence | QUEUE_AMBIGUOUS | 20 | 6.9% |
| — | acquired under STRICT | 11 | 3.8% |

Of the **129 actions PMUS would have permitted**, 118 (91.5%) fail the STRICT
stage. The split inside that matters: **98 never traded at all** during the
order's life, and only **20** are queue-ambiguous.

## The four hypotheses

**A — PMUS genuinely cannot reproduce enough of L5's actions. SUPPORTED, and
dominant.** 55.5% of L5's actions die at the venue stage before evidence is
even considered, overwhelmingly `REQUIRES_CROSSING`: L5's price is already at
or through our touch, so reproducing him as a maker is impossible. Add the 98
permitted actions where nothing ever traded — also a venue liquidity fact, not
a measurement artefact — and the great majority of the amplitude loss is the
venue, not our method.

**B — the q=1 translation destroys the dynamics. NOT MEASURABLE HERE, one
suggestive window.** In 16:45 the q=1 clamp discards 118 units of L5 size, and
replaying the same acquired actions at L5's relative sizes turns an overshoot
of 1 into **79.4** with the crossing preserved. That is the shape B predicts.
But 17:30 discards 292 units and its counterfactual overshoot stays 0, and
across all seven windows only 11 actions acquire at all, so there is not
enough surviving inventory for the clamp to be the binding constraint yet. B
is neither confirmed nor excluded.

**C — observation latency misses the overshoot actions. NOT SUPPORTED.**
Latency of lost actions (18.5–26.2s p50) is not systematically higher than
that of reached ones (4.2–35.5s p50); in 16:45 and 17:30 the *reached* actions
were the slower ones. Backlog is 0 in every window, so no action was lost to
arriving after its window closed.

**D — STRICT is undercounting executions that would really fill. SMALL, and
it does not restore the amplitude.** Only 20 of 290 actions are
`QUEUE_AMBIGUOUS`, the sole class where STRICT's queue-independence is what
rejects them. Re-running the whole decomposition under PLAUSIBLE — which
accepts the queue-dependent classes outright — recovers essentially nothing:
overshoot stays 0 in six of seven windows and reaches 1 in the seventh. If
STRICT's conservatism were the explanation, PLAUSIBLE would close the gap. It
does not.

## What this leaves

The binding constraint is the venue, in this order: L5 trades at or through
our touch (`REQUIRES_CROSSING`, 41.4%), then no counterparty arrives for a
resting maker order (`NO_SUBSEQUENT_INTERACTION`, 33.8%), then no readable
two-sided book (`NO_BOOK`, 14.1%). Our own evidence standard costs 6.9%, and
relaxing it does not buy amplitude back.

Eleven STRICT acquisitions across five authenticated windows is the honest
size of this sample. It is enough to rank the causes and to retire the
zero-crossing discrepancy as stated; it is not enough to settle B, and it is
not a basis for a live manifest.
