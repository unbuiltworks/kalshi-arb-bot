# Do L5's EXTEND decisions pay out of sample? No.

READ-ONLY over `data/l5_master/btc15m_sequences.jsonl`. **No fill model:**
every leg here is one of HIS executed fills, so nothing is simulated and
nothing below is a theoretical fill. Temporal 70/30 split; HOLDOUT read once.

## Method — an identity, not an attribution choice

For a leg of qty `q` at price `p` on side `s`, its marginal settlement value
is `(1 if s wins else 0) - p` per share. Summed over every leg of a window
that is **exactly** `redeem - cost`, because `sum(win*q)` is the winning
side's quantity (the redemption) and `sum(p*q)` is the cost.

So a leg's `(win - p)*q` is its exact share of the window's net, and it sums
correctly whether the leg ended up **paired** (two legs summing to
`1 - pair_cost`) or **residual**. No pairing convention has to be chosen.

Cost is calibrated per window to the corpus `cost` field, as established in
`FEE_RECONCILIATION.md`: the raw `price*qty` reconstruction understates it
by 0.434% of turnover while the entire edge is 0.2935%.

All features are knowable **at decision time** — run length, adverse move
since the run began, inventory imbalance, and the extend's own price.

## The aggregate answer

| | n | cost | net | margin |
|---|---|---|---|---|
| **DEV** (first 70%, 3,882 windows) | 12,235 | $108,034.01 | **+$135.49** | **+0.1254%** |
| **HOLDOUT** (last 30%, 1,664 windows) | 7,147 | $75,946.01 | **−$3,051.91** | **−4.0185%** |

**Marginally positive in development, strongly negative out of sample.** A
sign flip of 4.14 percentage points, on 7,147 real holdout legs.

## No conditioning family survives

### By run length at the moment of the decision

| bucket | DEV margin | HOLDOUT margin |
|---|---|---|
| run 1 | **+2.342%** | **−0.420%** |
| run 2–3 | −1.078% | −5.837% |
| run 4–8 | −0.957% | −1.791% |
| run 9+ | **+1.722%** | **−9.566%** |

All four holdout buckets are negative. The two DEV winners both flip.

### By adverse move since the run began

| bucket | DEV margin | HOLDOUT margin |
|---|---|---|
| adverse ≤ −0.10 | **+10.251%** | **−11.493%** |
| adverse −0.10..−0.02 | −2.424% | −10.838% |
| flat −0.02..+0.02 | +0.794% | −0.764% |
| favourable ≥ +0.02 | −0.890% | −4.448% |

The most promising DEV bucket — adding into a side that has already fallen
ten cents, +10.251% — is **−11.493%** out of sample. That is the single
largest reversal in the study, and it is exactly the behaviour
`L5_CANONICAL` §88.1#3 flagged as averaging down into a collapsing side.

### By the extend's own price

| bucket | DEV margin | HOLDOUT margin |
|---|---|---|
| px < 0.20 | −43.336% | −1.779% |
| px 0.20–0.35 | **+10.629%** | **−5.281%** |
| **px 0.35–0.50** | **+1.646%** | **+2.947%** |
| px 0.50–0.65 | −1.223% | −3.657% |
| px 0.65–0.80 | +0.947% | −15.048% |
| px ≥ 0.80 | −2.775% | +7.418% (n=89) |

## The one bucket that survives, and why it is not yet a finding

**`px 0.35–0.50` is positive in both halves** (+1.646% DEV, +2.947%
HOLDOUT) on 3,770 DEV and 1,892 HOLDOUT legs. That is the only candidate in
the study.

It must be treated as a **hypothesis, not a result**, for a specific
reason: this analysis inspected **14 buckets** across three feature
families. One bucket surviving both halves at +2–3% is close to what chance
alone would produce from 14 looks, and I did not pre-register it. The
`px ≥ 0.80` bucket flips the *other* way on n=89, which is the same noise
showing its other face.

To become evidence it would need pre-registration — the bucket boundary and
the direction fixed in advance — and a test on data neither half has seen.
The read-only collectors already accumulate post-2026-08-31 windows, which
is where that test should run.

## Verdict

**There is no evidence that enabling same-side EXTEND would pay.** The
aggregate flips to −4.02% out of sample, every run-length bucket is negative
in holdout, and the most attractive development condition reverses by 21.7
percentage points.

This is consistent with, and strengthens, §90.1: taking every OPEN/EXTEND as
he did and refusing dear COMPLETEs went DEV +1.7735% / HOLDOUT −1.7798%.
Two independent decompositions now put the same sign flip on the same
behaviour.

**EXTEND stays off.** The 51.2% decision divergence measured in
`L5_SEQUENTIAL_DECISION_COMPARISON.md` is real, and it remains the largest
behavioural difference between his policy and ours — but the divergence is
our engine declining a behaviour that does not pay out of sample, not our
engine missing profit. Frequency of divergence was never evidence of
profit, and now it has been tested directly.
