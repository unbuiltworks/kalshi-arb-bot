# Can we produce repeated, net-profitable completed pairs?

**Answer: not with this policy family, at our size.** Across 522 BTC15m windows
and 63,631 L5 trade prints, every configuration tested is net negative:
3 opening bands x 4 residual caps x 2 quoting modes x 5 completion ceilings.

Status: RESEARCH. No live change. k stays 1, the 0.96 ceiling stays, clip stays
1, HALT is reasserted.

## 1. The sample, expanded as instructed

The BBO tape holds 19 usable windows at ~19s sampling; five traded windows per
half is noise, which is why the 0.45 floor is NOT promoted here.

`print_replay.py` instead replays L5's own 522 settled BTC15m windows
(63,631 prints, 20 days, 2026-03-26 -> 2026-08-31) -- a 27x larger sample.

**The fill rule is queue-aware from first principles.** Every row is a BUY by
L5 on outcome Up/Down at own price q, labelled maker or taker:

* L5 **taker** buy at q => a resting ASK sat at q; our bid at p >= q crosses it.
* L5 **maker** buy at q => a seller hit a BID at q. Our bid at p > q had
  **strict price priority** over L5's, so that seller trades with us first.
  Price priority dominates queue position, which is what makes this
  conservative without a queue model we cannot observe.

So our resting buy on outcome o at p fills on a print (o, q, size) when
`q < p`, or `q == p` and `size >= 5 x clip`. Conservative three further ways:
L5's prints are a subset of all market activity; a tie requires size; we never
take more than `clip` per print. Optimistic in one way that is **not**
modelled: filling would have taken liquidity L5 actually got.

Settlement uses average cost basis per side, validated against the venue's own
`L5_WINDOW_ACCOUNTING` for 0245z to the cent on all three lines
(+0.1450 / -0.4350 / -0.2900).

## 2. Every configuration loses

| arm | matched$ | resid$ | **NET$** |
|---|---|---|---|
| sequential, band 0.30-0.60 (live) | +44.08 | -81.82 | **-37.74** |
| sequential, band 0.40-0.60 | +37.07 | -72.49 | **-35.42** |
| sequential, band 0.45-0.60 | +31.72 | -66.60 | **-34.88** |
| simultaneous, band 0.30-0.60 | +47.23 | -109.94 | **-62.71** |
| simultaneous, band 0.40-0.60 | +32.14 | -84.01 | **-51.87** |
| simultaneous, band 0.45-0.60 | +24.28 | -80.61 | **-56.33** |

**Matched economics are positive in every arm.** Residual settlement losses
swamp them in every arm.

### The band finding does not survive sample expansion

On 19 BBO windows, raising the floor 0.30 -> 0.45 looked worth ~$2.4 and cut
stranding 33% -> 14%. On 522 windows it is worth **$2.86 in total**, about
**$0.005 per window**, and cuts stranding only 27% -> 24%. I recommended that
floor on the small sample; the large sample does not support promoting it. It
is not harmful, it is just not the answer.

### Simultaneous quoting is WORSE than sequential

It roughly doubles matched contracts (417 -> 611) but residual losses grow more
(-81.82 -> -109.94) and the average pair gets **more expensive** (0.8924 ->
0.9170). Quoting both sides continuously means always resting on the side about
to move against us.

### Raising the completion ceiling is also worse

| ceiling | matched | resid | m:r ratio | matched$ | resid$ | NET$ |
|---|---|---|---|---|---|---|
| 0.96 | 417 | 310 | 1.35 | +44.08 | -81.82 | **-37.74** |
| 0.98 | 469 | 307 | 1.53 | +41.45 | -82.73 | -41.28 |
| 1.00 | 536 | 298 | 1.80 | +36.38 | -80.96 | -44.59 |
| 1.02 | 598 | 284 | 2.11 | +29.08 | -82.90 | -53.82 |
| 1.05 | 673 | 260 | 2.59 | +20.74 | -76.66 | -55.92 |

It lifts the ratio exactly as predicted (1.35 -> 2.59) but pays for it: matched
profit collapses +44 -> +21 while residual barely improves. **Hypothesis
disproved.**

## 3. The mechanism: adverse selection, measured

Of our windows ending with residual, the residual sat on the **losing** side:

| | losing-side residual | per residual contract |
|---|---|---|
| ours, sequential | **81.0%** (251/310) | **-$0.2639** |
| ours, simultaneous | **90.0%** (279/310) | -$0.3546 |
| L5 August | 70.2% (240/342) | -$0.1963 |
| **L5 Mar-Apr (profitable)** | **57.0%** (122/214) | **-$0.0779** |
| unbiased fills would give | ~50% | - |

251/310 against an expected 155/310 is about **11 sigma**. This is structural,
not a parameter: a passive resting buy fills when someone sells into it, and
informed sellers sell what is going down.

## 4. One identity explains all three books

> net > 0  <=>  matched:residual ratio  >  (residual loss/contract) / (matched gain/contract)

| | matched $/ctr | residual $/ctr | ratio required | ratio achieved | result |
|---|---|---|---|---|---|
| **L5 Mar-Apr** | +0.0374 | -0.0779 | 2.08 | **3.43** | **+$402.40** |
| L5 August | +0.0331 | -0.1963 | 5.93 | 5.46 | -$1,507.49 |
| **ours** | **+0.1057** | -0.2639 | 2.50 | **1.35** | -$37.74 |

Three things follow, and the first is the surprise:

1. **Our per-pair edge is 2.8x better than profitable-era L5's** (+$0.1057 vs
   +$0.0374). Our *selection* is good -- the 0.96 ceiling makes us picky.
2. **Our adverse selection is 3.4x worse** (-$0.2639 vs -$0.0779/contract).
3. **Our ratio is less than half what we need** (1.35 vs 2.50).

### Why L5's early era achieves 3.43:1 with only 57% adverse selection: volume

L5 early ran **128 matched + 37 residual contracts per window**. We run
**0.8 matched + 0.6 residual**. At L5's flow the residual is the random
leftover of a large two-sided book; at ours every residual is a single
adversely-selected fill with nothing to average against.

### Neither era has directional skill

Contracts bought of the eventual winner: **48% early, 47% August**. The edge is
pair capture, never direction. This confirms the standing instruction not to
build a favoured-side directional model.

### What actually separates the profitable era from the loss-making one

| | Mar-Apr (+$402) | August (-$1,507) |
|---|---|---|
| trades/window | 24 | 170 |
| capital/window | $140 | $1,593 |
| residual $/contract | **-$0.0779** | **-$0.1963** |
| losing-side residual | 57% | 70% |
| matched $/contract | +0.0374 | +0.0331 |
| pairs above $1.00 | 33% | 18% |
| first trade | t=55s | t=9s |

August is not "more of the same". It is **2.5x worse adverse selection** at
**11x the capital**. Its per-pair edge is slightly *lower* and its residual
cost 2.5x higher. Replicating the highest-volume behaviour replicates the
losses; the profitable era is the smaller, later-starting, more patient one.

## 5. Net turns positive at >= 2 completed pairs per window

With k=1 the residual is bounded at 1 per window, so the ratio rises with
completed pairs:

| completed pairs | windows | NET$ | **net/window** |
|---|---|---|---|
| 0 pairs | 251 | -48.83 | **-$0.1945** |
| 1 pair | 174 | -0.73 | -$0.0042 |
| **2 pairs** | 63 | +5.95 | **+$0.0944** |
| **3+ pairs** | 34 | +5.87 | **+$0.1726** |

Monotone, and it crosses zero between 1 and 2. **48% of windows complete no
pair at all and carry the entire loss.**

### But 0-pair windows are not selectable at open time

Early activity predicts *pairs* (mean 0.29 -> 1.22 across print-count buckets)
but **not profit** -- every bucket is net negative, and a gate requiring both
sides plus >=10 prints by t=120s splits the loss almost evenly
(-$0.0699/window pass vs -$0.0746/window refuse). So "open only in windows
that will complete" is **not executable** on any signal I could find.

## 6. The single best executable improvement

**There is no parameter change that makes this profitable**, and I am not
recommending one. The evidence supports exactly one structural statement:

> The whole loss is the 310 stranded contracts. If every opened leg paired, the
> same 522 windows would make roughly +$88 at our measured +$0.1057 per matched
> contract. Nothing we control changes the 81% adverse-selection rate, and
> paying up to complete (tested to 1.05) destroys more edge than it saves.

The one mechanism not yet testable is **exiting an unpaired leg rather than
carrying it to settlement**. Carrying costs -$0.2639/contract. Selling out at a
small loss would cost far less. I could not test it: the canonical dataset
contains **BUY prints only**, so there is no sell-side liquidity evidence, and
inventing one would be modelling the answer.

**Recommended next step, and it is data collection, not a deploy:** record
two-sided book and sell-side executable depth for BTC15m over >=40 windows,
then test residual exit against carrying. That is the only remaining lever with
a plausible path from -$0.26 to breakeven.

## 7. What is explicitly NOT supported

- Raising k (negative on both samples).
- Raising the 0.96 ceiling (monotonically worse to 1.05).
- Simultaneous two-sided quoting (worse than sequential at every band).
- Promoting the 0.45 opening floor (worth $0.005/window on 522 windows).
- A favoured-side directional model (L5 has 47-48% winner share; no skill).
- Any claim that $480/day is reachable: the profitable era's edge needs
  ~$373/window across all 96 daily windows, 31x the authorized $12 cash.
