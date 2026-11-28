# CAP=2 TRUNCATES L5 — proposed bounded cap 4, empirical coverage 66.9%

## The decision

**`MAX_YES = MAX_NO = MAX_IMBALANCE = 2` is an OURS_ADAPTED SAFETY LIMIT, not an
L5 behavioral limit.** It is not merely un-evidenced — it is **half** the number our
own frozen derivation chose, and the halving is documented in our code as a cash
decision.

## Provenance, quoted from our own source

`continuous_probe.py` derives the SHADOW cap:

```
MAX_SAME_SIDE_RUN = 4
MAX_RESIDUAL      = ACTION_SIZE * MAX_SAME_SIDE_RUN      # 0.04 at q=0.01
MAX_IMBALANCE     = MAX_RESIDUAL
```
> "run 4+ is the out-of-sample validated persistence regime; the modal run is about
> 2.9. Fewer than 2 makes an EXTEND impossible."
> "A cap of 4 x 0.01 admits the modal run (2.9) and reaches the run-4+ regime that was
> validated out of sample."

`live_envelope.py` then sets the LIVE cap to **2**:
> "same-side run 2 — admits ONE EXTEND after the OPEN. The modal L5 run is 2.9, so this
> reaches the behaviour **without funding it**."

"without funding it" is the whole provenance. **L5_BEHAVIORAL: 4 (run-4+ OOS regime).
OURS_ADAPTED: 2 (cost).**

## What L5 actually does — q-normalized, canonical history

`analysis/l5/l5_canonical_sequence.csv.gz`, 2,216 windows, 71,984 rows, 35,576
same-side actions. Unit = each window's own median BUY quantity (the existing
`l5_cap_override_study` definition). No new pipeline.

| measure | p25 | p50 | p75 | p90 | mean | max |
|---|---|---|---|---|---|---|
| peak directional imbalance / window (units) | 1.73 | **2.76** | 5.21 | 25.53 | 7.93 | 155.4 |
| same-side run length (actions) | 1 | **2** | 3 | 6 | 3.00 | 45 |
| max same-side units before an opposite action | 3.16 | **9.16** | 18.56 | 27.99 | 12.64 | 155.4 |

**Coverage — share of windows whose peak imbalance stays within a cap of k units:**

| k | 1 | **2** | 3 | **4** | 5 | 6 | 8 | 10 |
|---|---|---|---|---|---|---|---|---|
| coverage | 16.2% | **38.2%** | 54.2% | **66.9%** | 73.5% | 78.6% | 82.8% | 84.2% |

**Does L5 continue EXTEND beyond imbalance 2?** Yes, routinely:

| already holding ≥ k units when it extends again | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| share of all same-side actions | 91.2% | **82.1%** | 75.3% | 70.0% | 65.2% |

**P(opposite-side action next | currently holding k units)** — 0.479 / 0.481 / 0.503 /
0.487 / 0.490 for k = 1..5+. **Flat.** Holding more inventory does *not* make L5 more
likely to turn around; there is no completion pressure that a low cap is anticipating.

And the prior study (`l5_cap_override_study.json`) on the same file: a cap of **2 action
units refuses 93.09%** of L5's EXTENDs (33,119 of 35,576). At 4 it refuses 82.78%.

## The live stop, replayed through the frozen controller

State at the stop: YES 0, NO 2, imbalance 2, spend $0.39, YES 0.71 resting.

```
FROZEN CONTROLLER REQUESTED:   COMPLETE YES @ 0.71 q=1
                               EXTEND   NO  @ 0.17 q=1     <- neither held by the oscillator

LIVE_Q1_BTC15M (cap 2):  EXTEND NO   -> REFUSED "a fill would take imbalance to 3, past 2"
                         COMPLETE YES-> ALLOWED
cap 3:                   EXTEND NO   -> ALLOWED
cap 4:                   EXTEND NO   -> ALLOWED
```

One correction to the diagnosis: **the cap did not block the completion side.**
`COMPLETE YES @ 0.71` was allowed and was resting the whole time; it simply never
filled. What the cap blocked was the *continuation* — and blocking it is what ended the
window, which is what then cancelled the resting YES.

## Proposed bounded envelope — NOT DEPLOYED

The smallest change that lets the oscillator run, one axis only:

| bound | now | proposed | why |
|---|---|---|---|
| `max_yes` / `max_no` | 2 | **4** | restores our own `MAX_SAME_SIDE_RUN = 4` derivation at q=1 |
| `max_imbalance` / `max_residual` | 2 | **4** | derived from the above, not set separately |
| `max_same_side_run` | 2 | **4** | the run-4+ OOS-validated regime |
| `max_total_spend_usd` / `max_committed_usd` | $2.50 | **$4.00** | 4 binary contracts cannot cost more than $4.00; this is the hard worst-case loss |
| `max_residual_usd` | $2.00 | **$4.00** | same bound; residual is the part that can go to zero |
| `max_working_orders` | 2 | **2 — UNCHANGED** | this is NOT the four-order envelope; order count is a different axis and is not widened |
| `max_actions`, markets, TTE band, q, rest floor, lifecycle | — | **UNCHANGED** | |

**Worst-case loss rises $2.50 → $4.00.** Empirical coverage rises **38.2% → 66.9%**.

Honest limit: even cap 4 truncates the tail. L5's peak-imbalance distribution has
p90 = 25.5 units; no small-dollar envelope reaches it. Cap 4 buys the median and the
run-4+ regime, not the tail.

## Not claimed

`P(opposite | k units)` is measured on FILLS. What L5 quoted and did not fill is
invisible in this feed. Nothing here is a P&L claim, and nothing here is deployed.
