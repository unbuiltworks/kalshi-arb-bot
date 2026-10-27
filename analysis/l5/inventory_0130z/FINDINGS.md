# L5 inventory reconstruction — and the 0130z comparison

Research and replay only. Nothing deployed, no capital change, no new service.
`b382884f` stays undeployed pending review.

## 0. Reused, not rebuilt

The brief's steps 2–5 are substantially already implemented. Nothing below
re-derives them.

| asked for | already in the repo |
|---|---|
| per-trade inventory reconstruction | `analysis/l5/l5_window_sequences.csv` — 71,984 fills with running `cum_up_qty/spend`, `cum_down_qty/spend`, `wavg_*_price`, `matched_qty`, `matched_pair_unit_cost`, `excess_qty`, `excess_cost`, `dominant_side`, `seconds_to_expiry` |
| five-way action classification | `residual_exit/forensics.py`, summarised in `L5_STRANDED_INVENTORY_BEHAVIOUR.md` — 38,180 episodes |
| variation with time, price, imbalance, settlement distance | `L5_RESIDUAL_STATE_REPLAY.md` |
| does refusing an adverse EXTEND help | `ADVERSE_EXTEND_RESULT.md` — falsified, n=158,294 |
| is 0.96 acquired or emergent | `inventory_trajectory/TRAJECTORY_ANSWER.md` — emergent |
| did post-imbalance management cause our losses | `RESIDUAL_REPAIR_FINDINGS.md` — no, 0 of 359 states missed a repair |

New here: `analysis/l5/inventory_0130z/reconstruct.py`.

## 1. What L5 demonstrably does

BTC15m, 5,546 windows, 693,916 fills, **zero sells**, 38,180 residual
episodes, chronological TRAIN/OOS split.

| action | n | share |
|---|---|---|
| **EXTEND** (adds to the stranded side) | 25,264 | **66.2%** |
| SWITCH (complement large enough to cross zero) | 6,976 | 18.3% |
| COMPLETE_PARTIAL | 5,296 | 13.9% |
| NONE_CARRIED | 343 | 0.9% |
| COMPLETE_FLAT | 301 | 0.8% |

Near settlement he **extends more the smaller the imbalance**, and only
begins complementing once the position is large:

| \|residual\| | P(extend) TRAIN→OOS | P(complement) TRAIN→OOS |
|---|---|---|
| ≤1 | 0.689 → 0.749 | 0.004 → 0.004 |
| 1–5 | 0.709 → 0.740 | 0.023 → 0.040 |
| 5–20 | 0.662 → 0.698 | 0.119 → 0.127 |
| 20–100 | 0.621 → 0.606 | 0.251 → 0.280 |

This replicates out of sample as a predictor of his **action** (run length
ρ = 1.00).

## 2. What is hypothetical — and the decisive negative

His behaviour predicts his **money** not at all. Window net against each
behavioural feature, 3,882 TRAIN / 1,664 OOS windows:

| feature | ρ(train,oos) |
|---|---|
| **overweight_extend_share** | **−1.0** |
| max_run_len | 0.4 |
| switch_share | 0.4 |
| extend_share / complement_share | 0.1 |
| median_episode_dur_s | −0.6 |

Every OOS spread exceeds its TRAIN spread — the signature of noise. And his
own edge decayed **45×** inside the corpus: net/fill +0.04883 TRAIN →
+0.00108 OOS, with 98.0% of all BTC15m net earned before the holdout.

Carrying stranded legs to settlement — 14.5% of episodes — cost
**−$144,065**, his single largest loss line.

## 3. 0130z, reconstructed fill by fill

Venue lifecycle rows. DOWN `avg_px` is quoted in WIRE terms, so own cost is
`1 − wire`; UP's wire and own price coincide.

| utc | side | qty | wire | own | UP | DOWN | matched | residual | cum cost |
|---|---|---|---|---|---|---|---|---|---|
| 01:31:35 | UP | 1.78 | 0.68 | 0.68 | 1.78 | 0 | 0 | **+1.78 UP** | 1.2104 |
| 01:37:05 | DOWN | 1.78 | 0.73 | 0.27 | 1.78 | 1.78 | 1.78 | flat | 1.6910 |
| 01:38:40 | UP | 1.80 | 0.66 | 0.66 | 3.58 | 1.78 | 1.78 | **+1.80 UP** | 2.8790 |
| 01:38:54 | DOWN | 1.80 | 0.71 | 0.29 | 3.58 | 3.58 | 3.58 | flat | 3.4010 |

Two rounds, each at pair **exactly 0.9500**. Reconstruction reconciles to the
venue settlement to the cent: `up_cost 2.398400`, `dn_cost 1.002600`,
`total 3.401000`, net **+0.179000**.

## 4. The one real decision, three policies

01:32:00, holding UP 1.78 @ own 0.68, residual +1.78, book 0.50/0.51. The
engine logged `COMPLEMENT_PAIR_ABOVE_CAP` — pair 1.1500 against the 0.96
ceiling, complement at own 0.47 — refused, and waited 330 s.

| policy | pair | round-1 net |
|---|---|---|
| complete immediately at 0.47 (ceiling loosened) | 1.1500 | **−0.2670** |
| **strict 0.96, wait** (what we did) | 0.9500 | **+0.0890** |
| L5-style EXTEND then complete | 0.8768 | **+0.3696** |

**The ceiling plus patience was worth +$0.3560 in this window.** The
complement fell from own 0.47 to own 0.27 while we waited; completing on
sight would have booked a loss.

An L5-style extend would have done better still, by +$0.2806 — and it was
executable: the per-round cash envelope allowed 2.00 contracts and the
3-contract residual cap allowed 1.22, so **the residual cap binds, not cash**,
and a 1.22-contract extend at own 0.50 was within every limit.

## 5. Does that justify copying it

**No, on this evidence.** The 0130z extend is scored knowing the UP price
fell after our fill; at 01:32 that was not knowable. One favourable window is
not evidence, and the corpus that is evidence says the opposite:

- `overweight_extend_share` inverts perfectly OOS (ρ = −1.0);
- the adverse-extend refusal hypothesis is already falsified at n=158,294;
- in our actual residual bucket (1–5 shares) L5 extends 0.713 and complements
  0.027 — he adds and essentially never pairs, because he reaches 20–100
  shares at 32.48 fills/window where he complements 28%. We get 4.52. His
  small-residual behaviour is the opening phase of a trajectory our size
  cannot finish;
- our eight-window campaign stranded **nothing** and returned **+$1.4220**;
  the prior campaign stranded and returned **−$1.4140**. One ordinary strand
  costs 5.21 matched contracts — about two whole windows — at our realized
  $0.0800/contract.

## 6. The strongest demonstrated rule

**Patience at the 0.96 ceiling**, which is what we already run. It is the only
inventory rule in this campaign with direct executable evidence: it was worth
+$0.3560 on 0130z round 1, and across eight windows it produced 100% pair
completion and zero strands.

No L5-derived inventory rule survives out-of-sample validation as a profit
predictor. **I am not recommending a bounded live test of any of them.**

## 7. Smallest safe change worth testing next

Not an inventory-policy change. The measured bottleneck is throughput, not
inventory: 0100z converted 3 submissions from 42 decisions while a
two-tick-behind pair priced 0.95 at every book it refused on. `b382884f`
addresses exactly that and changes no limit.

If an extend rule is ever tested, the pre-registered form this evidence
supports is narrow: **extend only when the complement is already inside the
0.96 ceiling at the extended average** — i.e. an extend that improves a pair
we could already complete, never one that enlarges a pair we cannot. That is
the opposite of what L5 does, and it should be replayed OOS before any live
test.
