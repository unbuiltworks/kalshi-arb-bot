# FAVORITE → EXTEND → FLIP → SWITCH → REPEAT — **rejected, and inverted**

Scored exactly as `PREREGISTRATION.md` fixed it before any number existed.
Nothing here was tuned, no threshold was swept on held-out data, no controller
was built, no order was placed, and the frozen `.96` control is untouched.

## Sample

| | |
|---|---|
| BTC15m L5 transactions with exogenous pre-action state | **59,605** |
| windows | **527** (512 with a defined favorite) |
| market-wide prints behind the state | **925,484** |
| prints dropped as L5's own (by transaction hash) | 60,136 |
| primary split | chronological, 358 train / 154 held-out windows |
| secondary split | within-August, hashed, 227 / 86 windows |

Momentum coverage, from genuine prints only: **1s 29.0%**, 5s 88.7%,
15s 98.8%, 30s 99.9%. The 1-second horizon is reported and then not used:
prints are bursty (in a sampled window, 542 distinct seconds carried a print
over a 5,936-second span), so a 1-second "momentum" would mostly be a
carried-forward price.

**Endogeneity, measured rather than assumed:** the contaminated series
(`p_up_all`, L5's own prints left in) implies a different favorite in only
**0.43%** of rows, and `P(buys favorite)` is 0.3979 exogenous against 0.3980
contaminated. The filter mattered for correctness of claim, not for the
number. That is worth recording because the opposite was plausible.

## 1. Favorite preference — **the sign is wrong**

`P(L5 buys the favorite) = 0.4024` [0.3985, 0.4064], n = 58,938.

The base rate is not 0.50 by assumption: a side-blind trader with L5's own
Up/Down mix (0.4976 Up) scored against this favorite series would hit
**0.5001**. So the lift is **−0.0977** — he buys the favorite about ten points
LESS often than chance.

It strengthens with conviction, in the wrong direction:

| \|p_up − 0.50\| | P(buys favorite) |
|---|---|
| < 0.05 | 0.4917 |
| 0.05–0.10 | 0.5035 |
| 0.10–0.20 | 0.4456 |
| 0.20–0.30 | 0.3868 |
| ≥ 0.30 | **0.3435** |

Only **15.2%** of 512 windows sit above the base rate; the per-window median
is 0.4152. Both eras agree in sign (spring 0.4419, August 0.3988).

**L5 is an underdog accumulator.** This is consistent with the prior
independent finding in `next_side/NEXT_SIDE_ANSWER.md` that he is most
persistent when accumulating cheap outcomes (0.772 below $0.30 against 0.601
above $0.70), reached there from his own fill prices and here from an
exogenous series.

## 2. Flip response — **absent**

| | n | P(switch) |
|---|---|---|
| favorite flipped since previous action | 1,536 | 0.3236 [0.3006, 0.3474] |
| favorite did not flip | 56,890 | 0.3174 [0.3136, 0.3212] |

A difference of **+0.006** with overlapping intervals. In spring the flip
cell is *lower* (0.4053 vs 0.4572). Switching is also flat in time since the
last flip: 0.297 (<5 s), 0.330 (5–15 s), 0.336 (15–60 s), 0.334 (60–180 s),
0.309 (≥180 s).

Latency from the last observable flip to a switch, for the 17,143 switches
with a defined flip: p10 10 s, p25 43 s, **p50 140 s**, p75 324 s, p90 568 s.
In a 900-second window, a median of 140 seconds with a flat hazard is not a
response to the flip; it is what the waiting time looks like when the two
events are independent and the median window carries 5 observed flips.

## 3. Momentum extension — **no, and slightly reversed**

Conditional on L5 already on the favorite, by tertile of momentum *toward*
the favorite (30 s; 5 s and 15 s agree):

| tertile | momentum toward favorite | P(same side next) | median next qty |
|---|---|---|---|
| weakening | −0.43 … −0.00 | **0.6404** [0.6297, 0.6509] | 10.49 |
| flat | −0.00 … 0.06 | 0.5812 | 12.48 |
| strengthening | 0.06 … 0.95 | **0.5901** [0.5792, 0.6009] | 12.13 |

Continuation is **highest when the favorite is weakening**. Size moves the
other way by about 1.6 contracts at the median — a real but small effect, and
it does not rescue a non-monotone probability response.

## 4. Inventory interaction — it is neither favorite nor rebalancing

The design that separates the two: compare rows where rebalancing points at
the cheap side against rows where it points at the favorite.

| cell | n | P(next = cheap side) | P(next = rebalance side) |
|---|---|---|---|
| rebalance points at the **favorite** | 28,597 | **0.6012** [0.5955, 0.6068] | 0.3988 |
| rebalance points at the **cheap side** | 29,770 | **0.5989** [0.5933, 0.6045] | 0.5989 |

**The cheap-side rate is identical (0.601 vs 0.599) whether or not inventory
agrees with it.** Inventory imbalance carries no influence on the next side
once price is known; "buy the side needed to rebalance" is ruled out by the
same table that rules out "buy the favorite". `P(next repairs imbalance)` is
0.49–0.51 in every imbalance stratum from <10 to ≥200 contracts.

What does condition the next side is **the previous side**: 0.6255 (<10) →
0.6938 (≥200) by imbalance stratum, and conditional on price,
P(next = cheap) is 0.7353 after a cheap-side buy against 0.3960 after a
favorite-side buy.

Fitted, held-out: favorite/momentum alone **0.6002**; inventory alone
**0.6950**; favorite/momentum + inventory **0.6947**. The favorite block adds
nothing to inventory-plus-last-side, and the `p_up` coefficient is
**negative** (−0.2953 standardized) in the combined model — the fit discovers
the underdog tilt on its own.

## 5. 50¢ — **continuous, no discontinuity** (TRAIN only)

Train log-loss: continuous `p_up` **0.6688**, step at 0.50 alone 0.6738, both
together **0.6688**. The step adds **0.0000**, and in the combined fit its
coefficient (−0.0408) is an order of magnitude below `p_up`'s (−0.4136). The
response curve is smooth and monotone over 20 bins, 0.6576 at p_up ≈ 0.08
down to 0.3190 at p_up ≈ 0.92, crossing 0.50 near p_up = 0.50 with no jump.
Replicated on the August-only train split (0.6649 / 0.6706 / 0.6649).

**0.50 is where a smooth negative price response happens to cross even odds,
not a trigger.** The tau grid rises with tau (0.4036 at 0.50 → 0.4763 at
0.70) only because a stricter favorite sends more rows to the persistence
fallback.

## 6. Sequential prediction — persistence is unbeaten

Held-out accuracy on L5's actual next side:

| model | chronological (154 windows) | within-August (86 windows) | margin vs persistence [95% window bootstrap] |
|---|---|---|---|
| **persistence** | **0.6950** | **0.6986** | — |
| inventory rebalance | 0.4991 | 0.4986 | −0.196 [−0.216, −0.178] |
| current favorite | 0.4053 | 0.3994 | −0.290 [−0.307, −0.273] |
| favorite + momentum | 0.4022 | 0.3966 | −0.293 [−0.309, −0.276] |
| cheap side (the inversion) | 0.6000 | — | −0.095 [−0.106, −0.084] |
| logit, inventory | 0.6950 | 0.6986 | 0.000 |
| logit, favorite + momentum | 0.6002 | 0.6063 | −0.095 |
| logit, favorite + momentum + inventory | 0.6947 | 0.6981 | −0.000 |

Both splits agree, so this is **not** the regime-transfer failure prior art
found across the spring→August boundary: it is the absence of the signal in
the hypothesized direction, inside a single era as well as across two.

AUC does improve (0.6954 → 0.7356 when the favorite block is added). As in
`NEXT_SIDE_ANSWER.md`, the extra state buys **calibration, not direction**.

Size and timing, held out: median next quantity 12.0 on-favorite against 10.0
off-favorite, median next inter-action gap **2 s in both**. No timing content.

**Survival criterion: FAILED.** The criterion fixed in advance was that a
favorite-bearing model must beat persistence by more than the window-level
bootstrap spread of that margin. Every favorite-bearing model is *worse* than
persistence, and the bootstrap interval excludes zero on the wrong side.

## 7. Economics — **not run, by preregistration**

The preregistration gated the shadow controller on behavioral survival, and
it did not survive. No controller was built, no `$3`/`$5` per-window test was
run, and no PMUS tape was replayed under this mechanism. Building a
favorite-chasing controller now would be fitting economics to a behavioral
hypothesis the behavior already rejected.

## What this changes for the portfolio-level reconstruction

1. **Replace FAVORITE with UNDERDOG in the thesis.** The direction is
   measured on 59,605 actions with an exogenous price series, agrees in both
   eras, strengthens with distance from 0.50, and is independently implied by
   two earlier studies reached from different data.
2. **FLIP is not an event in L5's policy.** Leadership changes carry +0.006
   of switch probability with a flat hazard. A controller that switches on
   flips is imitating something he does not do.
3. **Aggregate inventory does not steer the next side either** — the
   identical cheap-side rate across the two rebalance cells is the cleanest
   negative in this lane, and it closes the question the user asked to
   distinguish: it is *neither* "buy the favorite" *nor* "buy the side needed
   to rebalance".
4. **The one durable regularity is still persistence**, 0.695 held out and
   unbeaten by six models. Any L5-style sequential controller should be built
   on run continuation with a price-level tilt toward the cheap side, which is
   what survives.
5. What remains genuinely open is **when a run ends** — unexplained here and
   in prior art, where AUC conditional on run length *falls* as runs lengthen.

The frozen `trade_imbalance >= 0.75 @2s` diagnostic was not mixed into this
lane and is unchanged.
