# What predicts L5's next action class? Conditioning, out-of-sample

116,675 labelled actions, 1,007 BTC15m markets, from the preserved decision tape
(`4ca8cad2`). **Chronological split** at window epoch 1788957900: train 93,192 /
test 23,483. Nothing was optimised on the test markets. No new data. No engine
change. Fills only — no book, no resting-order claims.

## FIRST, A CORRECTION TO MY OWN EARLIER CLAIM

I wrote that the 43/24/33 split was a **"state-independent mixture"**. That was too
strong and it is now contradicted. I had tested exactly one variable, residual
magnitude. The supportable claim then was only: *residual magnitude does not
explain the mixture*. Conditioning on other state moves the probabilities a long
way, as below.

## The mixture is NOT fixed

Conditional probabilities span roughly **P(A) 0.28–0.60, P(B) 0.03–0.50,
P(D) 0.15–0.69** against the unconditional 0.44/0.23/0.33. Leaf examples:

* `size [25,100) & d_px≈+0.01 & d_size≈0 & run_len 2` → **P(D) 0.69**
* `size [5,25) & quiet & d_px≈0 & run_len 9–17` → **P(B) 0.50**
* `size [25,100) & |mv10| ≥ 0.05` → **P(A) 0.60**

The train/test baseline itself drifts (train P(B) 0.230 vs test 0.264), which is
further evidence the split is not a constant of nature.

## Which variables actually move it

Weighted TV = mean total-variation distance from the unconditional split;
spread = widest swing in any one class across buckets with n ≥ 500.

| variable | weighted TV | max spread |
|---|---|---|
| **size** | **0.1051** | **0.256** |
| **d_px_same_side** | **0.0675** | 0.179 |
| d_size | 0.0530 | 0.198 |
| **mv2 / mv5 / mv10 / mv1** (recent price movement) | 0.050 / 0.050 / 0.046 / 0.046 | ~0.18 |
| run_len | 0.0421 | 0.178 |
| mv30 | 0.0416 | 0.124 |
| same_side_cheaper | 0.0355 | 0.077 |
| gap_s | 0.0329 | 0.096 |
| t_remaining | 0.0321 | 0.180 |
| lvl_band | 0.0316 | 0.092 |
| matched | 0.0305 | 0.080 |
| prev_same_side | 0.0260 | 0.095 |
| fragments | 0.0172 | 0.101 |
| **residual_signed** | **0.0094** | **0.027** |
| side | 0.0053 | 0.011 |
| **residual_dir** | **0.0013** | **0.002** |

**The inventory variables are the WEAKEST predictors in the whole set.**
`residual_dir` moves the distribution by 0.2 of a percentage point. The strongest
is **action size**, then **distance from his own last same-side level**, then
**recent price movement**. This is a second, independent blow to the
inventory-control thesis: it is not merely that residual magnitude fails to
predict — residual *direction* fails too.

### The three strongest relationships, in plain terms

**1. Size decides whether he repeats a level.** P(B) falls monotonically as size
grows: 0.366 at size < 5, 0.303 at 5–25, **0.109** at 25–100, 0.113 at 100–400.
Large actions almost never repeat a price; they re-price or switch (P(D) 0.419 at
size 25–100 vs 0.237 at size < 5).

**2. Movement drives re-pricing.** With |mv2| ≥ 0.03, P(A) rises to **0.566–0.568**
and P(B) falls to ~0.15. In a quiet market (|mv2| < 0.01), P(A) 0.389 and P(B)
0.275. Same pattern at 1 s, 5 s, 10 s, 30 s horizons. **When the market moves, he
re-prices; when it is quiet, he is more willing to sit at a level.** Our engine
does the opposite: it never moves for market movement at all.

**3. Distance from his own last level.** Having already moved ≥ 5c away from his
previous same-side execution, P(A) is 0.46–0.52; sitting within half a cent of it,
P(A) 0.375 and P(B) 0.313.

## Out-of-sample prediction

| model | log loss | accuracy |
|---|---|---|
| baseline, fixed 43/24/33 | 1.08160 | 0.4253 |
| decision tree, depth 2 | 1.05198 | 0.4265 |
| depth 3 | 1.04551 | 0.4401 |
| **depth 4 (best)** | **1.04372** | **0.4458** |
| depth 5 | 1.04374 | 0.4457 |

**3.50% better log loss, +2.1 points accuracy, out of sample.** Real but modest.

The honest reading: conditioning produces **large, systematic, interpretable**
shifts in the probabilities, yet only a **modest** gain in predictability. Most of
what decides L5's next action is not captured by any feature reconstructable from
fill data. A controller built from this tree would be better calibrated than a
43/24/33 coin flip, and still mostly guessing.

## B IS CONTAMINATED — do not read 23.7% as "repeats a level by choice"

| class | n | gap to next (p50 / p90) | size p50 | next action within 1 s |
|---|---|---|---|---|
| A | 50,648 | 2 s / 18 s | 21.8 | 41.9% |
| **B** | **27,663** | **1 s / 6 s** | **9.2** | **53.4%** |
| D | 38,364 | 4 s / 23 s | 30.5 | 26.2% |

B is the smallest and fastest class, with over half its cases followed by another
action within one second. Fragment grouping merges rows sharing
(slug, timestamp, outcome, price), so **one resting order filling across a second
boundary becomes two actions and is labelled B**. Much of B is therefore likely a
single order continuing to execute, not a decision to repeat a level.

**23.7% is an upper bound on deliberate level-repetition.** This strengthens the
main conclusion rather than weakening it: L5 re-prices or switches even more than
the raw split suggests.

## Bursts

| window | groups | span MORE than one price |
|---|---|---|
| 0 s | 85,286 | 15,930 (**18.7%**) |
| 1 s | 71,601 | 18,451 (25.8%) |
| 2 s | 65,035 | 19,948 (30.7%) |

Same-second group sizes: 1 → 63,752; 2 → 15,184; 3 → 3,970; 4 → 1,343; 5 → 539;
6 → 227. **5,949 same-second groups touch BOTH sides.**

So ~25% of same-second groups contain two or more fills, and 18.7% span multiple
prices within the same second — including 5,949 that span both sides at once.

This is **consistent with a pre-existing ladder being swept** and **equally
consistent with sub-second sequential decisions**. It does **not** establish
resting orders and no such claim is made. Timestamps are 1-second resolution, so
intra-second ordering does not exist in this evidence; the two hypotheses are not
separable here.

## What remains unknowable without synchronised L2 data

1. **Ladder versus reactive sequence.** The decisive question. Multi-price
   same-second bursts cannot be attributed without order-level events or
   sub-second book snapshots.
2. **Whether he cancels.** Re-pricing is inferred from executions. We cannot see
   cancel-replace versus multiple simultaneous levels — the single most important
   unknown for copying the behaviour.
3. **Where he was relative to the touch.** No aligned Polymarket L2 tape, so
   distance-from-touch, queue position and depth are all absent, and "the market
   moved" is proxied by his own execution path.
4. **His reaction latency.** The collection lag is a median 7,993 s (batch pull),
   so his response time to any market event is unmeasurable here.
5. **Unfilled intent.** Orders he placed that never filled are invisible, so the
   denominator of his decisions is unknown — we only ever see what traded.
6. **Why size varies**, which is the strongest predictor we found. Size is an
   output of his sizing policy; what drives it is not in this data.

## Consequence for the engine — not yet applied

Do **not** implement 43/24/33 as a randomised controller; the mixture is not fixed
and the proportions are not a rule. The defensible corrections, in order of
evidential strength:

1. **Re-price on market movement.** `_still_valid` returning `True`
   unconditionally is contradicted by the strongest robust relationship here.
2. **Stop treating residual as the control variable.** It is the weakest predictor
   in the set, by an order of magnitude.
3. **Treat size as a first-class decision variable**, not a constant clip. It
   predicts his next action better than anything else we can measure.
