# Getting the L5 5m engine working on PMUS: result and the exact blocker

Read-only analysis plus an OFFLINE replay. Nothing was deployed, no order was
placed, `d108c54a` was not redeployed, Gemini was not touched, Ledger D
accounting was not altered, and no gate or risk limit was changed.

## 1. The 5m entry rule is NOT learnable from what we can observe — FALSIFIED

Restricted to BTC5m + ETH5m (10,920 cycles, 57.2% profitable). BTC15m is
excluded throughout so its 30-switch regime cannot contaminate the 5m answer.

**The first target was wrong and it had to be discarded.** Predicting
"profitable cycle" from entry state looks easy — profitable-rate rises
monotonically with entry price, 0.29 → 0.83 on train and 0.24 → 0.80 on test.
It is an artefact:

| entry px | mean price | win rate | net bp |
|---|---|---|---|
| >=0.80 | 0.870 | 0.797 | **-36** |
| 0.65-0.80 | 0.711 | 0.684 | **-3** |
| <0.20 | 0.101 | 0.238 | +100 |

The win rate simply tracks the price. That is the base rate of favourites
winning, not an edge, exactly as `L5_OPERATING_MODEL` warns about residual
directional exposure. On net bp every best-on-train bin fails out of sample:
`first_px>=0.80` +157 → **-36**, `dist_from_50<0.05` +65 → **-320**,
DEAR -12 → +15.

**Retargeted on the economically meaningful, implementable question** — does
this entry lead to an attainable first-switch pair inside the gate? The target
is decided at entry and learned at the switch, so it does not leak.

The target is real: on test, pair<=0.96 earns **+954 bp** and pair>0.96
**-839 bp**. But almost nothing predicts it:

| feature | best-on-train → test lift over base |
|---|---|
| first_px | +0.0211 |
| DEARER/CHEAPER | **-0.0038** |
| dist_from_50 | +0.0157 |
| vol_10 | +0.0044 |
| prev_move | ~0 |
| **tte_s** | **+0.1244** |

TTE is the only real signal (test P(pair<=0.96): <60 0.5750, 60-120 0.5745,
120-180 0.4757, 180-240 0.4624, >=240 0.4183) — **but it does not convert into
economics.** Selecting the threshold on train and evaluating once on test:

| side filter | picked on train | test | no-filter baseline | verdict |
|---|---|---|---|---|
| any | tte<90 | +1559 bp (n=59) | +954 bp | helps |
| DEAR only | tte<120 | +868 bp (n=55) | +1089 bp | **hurts** |
| CHEAP only | tte<120 | +1244 bp (n=49) | +703 bp | helps |

Mixed sign across splits at n≈50 is not an edge.

**Conclusion: no entry-observable feature adds stable OOS economic value.**
The blocker is precise — the only OOS-stable economic signal is the pair-cost
gate, which is evaluated at the SWITCH, not at entry; and spread, depth,
order-flow and queue position, which might carry entry signal, are UNAVAILABLE
in the public tape and were not imputed.

## 2. Where the edge actually is, and a correction

5m first-switch pair bands, chronological (bp on acquisition):

| band | train | test | test net $ |
|---|---|---|---|
| **<=0.90** | **+1332** | **+1344** | **+11,019** |
| 0.90-0.96 | +302 | +96 | +357 |
| 0.96-0.98 | -488 | -108 | -98 |
| 0.98-1.00 | -403 | **+255** | +271 |
| 1.00-1.05 | -345 | -301 | -753 |
| >1.05 | -1331 | -1092 | -13,680 |

`<=0.90` is the only band that is both large and OOS-stable; it carries
**$11,019 of the $11,549** total net.

**A correction to a reading I had started to form.** The 0.96-1.00 band is NOT
reliably harmful — its sign flips (train -488/-403, test -108/+255) and the
band nets **+88 bp** on test. Total net dollars by ceiling are near-flat:
C=0.96 $11,376, C=0.98 $11,278, C=1.00 **$11,549**. The destruction is above
1.05, which the live hard ceiling of 1.00 **already excludes**. So *no
pair-ceiling change is supported*, and "the live ceiling is looser than the
evidence supports" was premature.

## 3. PMUS is closer to L5's habitat than the cohort rows suggested

Broad observation (72 PMUS sports series, 903 two-sided observations) gives the
maker-attainable pair (1 - spread):

| venue / category | pair p50 | <=0.96 | <=0.90 |
|---|---|---|---|
| **PMUS sports** | 0.9900 | 29.1% | **18.8%** |
| GEMINI Commodities | 0.9500 | **77.1%** | **20.8%** |
| GEMINI Crypto | 0.9900 | 5.7% | 5.5% |
| GEMINI Weather | 0.9700 | 45.1% | 0.0% |

An earlier figure of 2.2% came from cohort rows conditioned on already holding
inventory; it is not the venue's supply. And production's own realised
completion pair (26 deduped matched sessions, 14 markets) is p25 0.9234,
p50 0.9640, with **<=0.90 on 18%** — against L5's ~33%. A factor of 1.8, not a
structural wall.

## 4. The candidate, and why it is INADMISSIBLE — the exact blocker

Candidate `NEW_L5_5M`: complete only when the attainable pair is <= 0.90;
otherwise WAIT. It is strictly TIGHTER than production (which completes inside
the active rung ceiling of 1.00, soft cap 0.98) and raises no limit.

Replayed offline against recorded production sessions it would keep 6 of 26
matched sessions (pair p50 0.7925) and refuse 20 (p50 0.9698).

**Refusing a completion does not remove risk, it converts it.** The matched
inventory is never formed, so what would have been matched stays DIRECTIONAL
until settlement — and residual direction has no demonstrated skill.

> **11 of the 20 refused sessions would breach the existing 6-unit residual
> cap**, several by 2-3x: 18.87, 16.57, 14.19, 14.19, 13.04, 11.38, 10.96,
> 10.96 units against a cap of 6.

So the <=0.90 target — the one large OOS-stable edge in L5's 5m history —
**cannot be run inside the current safety shell.** Implementing it would
require raising the residual bound, which is exactly the risk increase that is
forbidden. **No canary diff is proposed and no cutover is prepared.**

This is not an accident of our shell. L5's own books show absolute residual
*growing* 44.9 → 162.6 units while relative imbalance falls 0.61 → 0.06: he
finances cheap pairs by carrying a large directional book. Our shell caps that
at 6 units. **The <=0.90 edge and the residual cap are in direct tension**, and
that tension — not the entry rule, not the side rule — is what limits the 5m
engine on PMUS.

## 5. A second blocker in the evidence path

`econ_pair_cost` is present on only **6 of 25** production fills, and
`econ_held_avg_cost` is absent on the other 19, so the pair cannot be
reconstructed for them. The replay refuses those states (UNKNOWN is not
permission) rather than imputing a number the decision turns on. Any future
promotion test needs the pair recorded on every fill, not on a quarter of them.

## What would unblock this

Not a policy change. A habitat where cheap pairs are frequent enough that
completions happen at <=0.90 *without* holding residual to get them. Gemini
Commodities shows the best pair supply observed anywhere (<=0.96 on 77.1%,
<=0.90 on 20.8%) and **zero prints in 3,375 observations** — pair supply and
flow have so far never co-occurred. That pairing, not another rule, is the
thing to look for.
