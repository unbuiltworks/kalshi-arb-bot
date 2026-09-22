# P2 — Sequential replay: what actually moves the number, and what only looks like it

**Status: REPLAY / OFFLINE ONLY. Nothing armed, nothing deployed, no
production module changed.** COHORT-2 is complete and disarmed.

**Headline: no arm tested improves expectancy through a mechanism.** Four
arms go positive; all four do it by shrinking the matched engine and
re-rolling which side they end up long, on a residual book of 11-17
contracts. Every arm that preserves the matched engine stays negative. The
one adaptation that L5's history says is profitable — residual on the
*favored* side — is **structurally unreachable** by this policy: of 32
confirmed EXTEND fills, **zero** were priced at or above $0.80 and **one**
at or above $0.65.

---

## 0. The audit the owner asked for, and a correction

> Verify the statement that all 56 EXTEND fills were lower than the previous
> fill on that leg.

**The P1 claim was wrong twice and I withdraw it as stated.**

```
CLAIMED (P1)   "every one of the 56 fills labelled EXTEND filled at a lower
                price than the previous fill on that leg"
ACTUAL          56 authoritative fills, of which 32 are EXTEND
                29 of 32 strictly lower          (91%)
                 1 equal          1245 YES 0.40 == 0.40
                 2 HIGHER         1415 NO 0.56 > 0.54,  1415 NO 0.68 > 0.56
```

The count conflated total fills with EXTENDs, and the universal quantifier
was false. The direction survives at 29/32, and the exceptions are
informative rather than noise: both higher-priced EXTENDs are in **1415**,
the one window where the complement was bought into a *rising* price — and
1415 is also the window with the highest residual unit cost ($0.59) and the
cheapest available completion. It is the closest COHORT-2 came to the regime
L5 profits in.

**Source of the claim, as requested.** It does **not** come from the WS
`order_events` stream that had the 54/56 gap. It comes from
`after.orders_seen` (the venue's own filled-order rows) joined to
`after.trades` (the venue's own trade record) on `order_id` — the
authoritative record `fill_reconciliation` proves COMPLETE on all 11 windows
(56 authoritative = 56 controller, 56 maker via `aggressor`, spend
reconciling to the cent). The ordering test is therefore over a complete
source, and no observation in it is unverifiable.

**Confirmed vs counterfactual, kept apart everywhere below.** Confirmed
fills are the 56 in that record. Every fill produced by the replay is
stamped `counterfactual: True` and priced by the public-tape model that
`residual_timeline.calibrate` validated against those 56. The two are never
summed.

---

## 1. Root cause of the stale complement: a one-line execution defect

P1 found the complement resting a median 30 ticks behind its own bid while
the planner recomputed the right price every cycle and planned
`mutations: 0`. P2 found why, and it is not a policy choice.

`ws_continuous.WorkingOrders.reconcile` rebuilds the working set from the
venue's open-order list every cycle:

```python
"resting_since": float(o.get("resting_since") or now),
```

`o` is a **venue row**. `resting_since` is our own bookkeeping key and no
venue row carries it, so the fallback is taken every single time and every
resting order is re-stamped as brand new. From the artifacts:

```
order l5cp-owner-2026-no-53fb2948
  truly rested   473s   (createdAt 12:50:09.881 -> updatedAt 12:58:02.681)
  age as computed by the controller, at ~60 consecutive cycles:
    0.16  0.22  0.19  0.34  0.19  0.56  0.23  0.24  0.18  0.21 ... seconds
```

`reconcile_surface` retains any valid order younger than `STALE_ORDER_S`
(30.0) and marks its leg slot occupied, so the moved desired price is HELD.
With the age permanently near zero, **the 30s rest floor became an infinite
rest floor**: no resting order was ever cancelled for price movement, in any
window, and a moved quote could never go on. That is the whole of P1's
"68 of 750 cycles planned a mutation" and "58 of 132 slots forfeited".

The venue already publishes the correct value — `createdAt` on each open
order. **This has not been fixed.** `ws_continuous.py` is an authorized
bundle member; editing it moves `bundle_sha`, which is an
authorization-affecting change and not P2's to make. It is written up here
as a defect awaiting the owner's decision.

---

## 2. The fidelity gate

`replay_engine` drives **`CP.plan` itself** — the same function
`WindowGuard.step` calls in the live driver, not a reimplementation. Only
the *reconciliation* is modelled separately, because varying it is P2-A.

```
FED each recorded cycle's recorded inventory and recorded book,
the production policy emits the RECORDED actions:

    720 of 720 readable cycles reproduced exactly   (action, leg, price, qty)
      0 mismatches
     30 cycles unreadable, and the unreadable branch is exercised
```

Asserted as a test. A replay that cannot reproduce the run it replays may
not price an alternative to it.

**Fill-model fidelity is weaker, and the bound is stated rather than
buried.** Running the production-default arm against the confirmed record:

```
                replay      confirmed
fills               58             56
gross spend     $20.78         $19.96      (+4.1%)
terminal position exact in 10 of 11 windows
net             -$3.78         -$4.96      (+$1.18 optimistic)
```

Every replayed fill matches a real print **by tid**. The overshoot is in
1245, where the replay fills two prints production did not, and two fills
land 1c and 4c from the confirmed price. The cause is queue position, which
is not observable: the arms run at the `AT_LEVEL` tier because a maker fill
prints *at* the resting price by definition — a print *through* it means the
order already filled — so the strict `THROUGH` tier used in P1 is
structurally unable to reproduce a real maker fill (asserted).

**Consequence for everything below: absolute levels carry a ~$1.2 optimistic
bias, and arm-to-arm comparison inherits it. Read the deltas, not the
levels.**

---

## 3. P2-A — bounded cancel/replace on the stale complement

Scoped exactly as instructed: **the reducing leg only**, EXTEND untouched,
maker/post-only, q=1, max 2 working orders, 30s floor intact, every
cancel and every place counted against the 12-slot ledger (a replace costs
**two** slots).

```
arm                        matched  residual     net  maxDD mQty resid  muts fills  pairC  domWon
BASELINE production           3.32     -7.10   -3.78   5.21   15    28    77    58  0.778    1/11
complement reprice >= 1t      0.90      1.08    1.98   1.59    8    18   132    34  0.888    5/11
complement reprice >= 2t      0.70      1.76    2.46   1.59    7    18   132    32  0.900    4/11
complement reprice >= 3t      0.64      2.05    2.69   1.36    8    17   132    33  0.921    6/11
complement reprice >= 5t      0.59      1.80    2.39   1.35    8    18   132    34  0.927    5/11
complement reprice >= 8t      1.60     -1.62   -0.02   1.99   12    20   127    44  0.862    2/9
```

**These are not improvements and I will not report them as such.** Read the
last two columns:

- `muts` hits **132 of 132** — the entire cohort ledger — in every arm.
  Repricing and placing compete for the same budget, so chasing the
  complement *spends the inventory engine*. Fills fall 58 -> 32, matched
  quantity 15 -> 8, matched P&L **+3.32 -> +0.64**.
- `domWon` goes from **1/11** to **5-6/11**. The residual book is barely
  smaller (28 -> 17 contracts); what changed is which side it is on.
  Repricing alters which orders rest and therefore which leg accumulates, so
  it **re-rolls the directional bet** rather than removing it.

The entire +$2.05 residual swing is 17 contracts landing well. On 11 windows
that is one or two coin flips. The mechanism it was supposed to demonstrate
— completing pairs — went the other way: matched collapsed.

**Of the 398 COMPLETE decisions P1 counted, repricing at the 3-tick
threshold causes 55 cancel/replace pairs across 11 markets and exhausts the
ledger in every one.** That is the honest answer to "how many would actually
cause repricing": more than the envelope can pay for.

---

## 4. P2-B and P2-C — residual-price gate, and the cap, replayed

Both replayed sequentially: a refused EXTEND changes inventory, which
changes the next decision, which changes what rests. COMPLETE is never
gated.

```
arm                        matched  residual     net  maxDD mQty resid  muts fills  pairC  domWon
BASELINE                      3.32     -7.10   -3.78   5.21   15    28    77    58  0.778    1/11
no EXTEND below $0.20         3.08     -6.42   -3.34   4.90   15    23    69    53  0.801
no EXTEND below $0.35         2.70     -5.40   -2.70   4.18   15    18    63    48  0.827
no EXTEND below $0.50         1.92     -4.77   -2.85   3.82   14    15    57    43  0.865    1/11
no EXTEND below $0.65         1.71     -3.69   -1.98   2.51   14    11    52    39  0.876    1/11
no EXTEND below $0.80         1.50     -3.18   -1.68   2.21   14    10    52    38  0.888    1/10
CAP1 (replayed)               1.50     -3.18   -1.68   2.21   14    10    52    38  0.888    1/10
CAP2 (replayed)               2.82     -5.67   -2.85   3.88   16    20    66    52  0.822    1/11
CAP3 (replayed)               3.16     -6.44   -3.28   4.93   15    27    73    57  0.787
CAP4 (replayed) = baseline    3.32     -7.10   -3.78   5.21   15    28    77    58  0.778    1/11
```

These behave like a mechanism: `domWon` stays at 1/10-1/11 throughout — the
adverse selection is untouched, which is what should happen when you only
change *how much* of the losing side you buy. Residual damage falls
monotonically with the gate; so does matched P&L; net improves but **never
reaches zero**.

**CAP1 and the $0.80 floor are byte-identical across all 11 windows**
(verified per window, not inferred). That is a convergence, not a bug, and
it is the finding:

```
confirmed EXTEND fills priced >= $0.20   22 of 32   (69%)
                                >= $0.35   12 of 32   (38%)
                                >= $0.50    3 of 32   ( 9%)
                                >= $0.65    1 of 32   ( 3%)
                                >= $0.80    0 of 32   ( 0%)
```

An $0.80 floor removes **every** EXTEND, and a controller that only ever
extends downward and never past imbalance 1 does the same thing. So on this
data the "residual-price rule" is not really a price rule — above $0.50 it
is just "stop extending".

---

## 5. P2-E — favored-residual / asymmetric accumulation

### E.1 The asymmetry is real, and it is L5's data that says so

37,649 residual-*increasing* EXTENDs across 2,216 L5 windows, bucketed by
the leg's contemporaneous price — information available at the decision
timestamp and nothing else:

| leg price at the decision | n | contracts | cost | won% | implied | gap | **return on cost** |
|---|---|---|---|---|---|---|---|
| <0.35 | 16,360 | 313,470 | $42,960 | 11.6% | 13.7% | −2.1 | **−15.2%** |
| 0.35–0.50 | 4,289 | 85,158 | $36,183 | 39.4% | 42.5% | −3.1 | **−7.3%** |
| 0.50–0.65 | 5,861 | 134,364 | $76,979 | 55.2% | 57.3% | −2.1 | **−3.7%** |
| 0.65–0.80 | 5,829 | 135,544 | $97,407 | 72.5% | 71.9% | +0.6 | **+0.8%** |
| >0.80 | 5,291 | 140,407 | $124,013 | 90.4% | 88.3% | +2.1 | **+2.4%** |

Monotone across all five bands, crossing zero at about **$0.65**. The
adverse selection is roughly **constant in probability points** (−2 to −3),
but its cost as a fraction of capital deployed explodes as price falls — a
2-point miss on a 13% implied probability is −15% of the money. That single
observation explains P1's entire −$8.00.

**The COMPLETE control group is positive almost everywhere**, which is why
reducing actions are never gated in any arm here:

| leg price | return on cost (COMPLETE) |
|---|---|
| <0.35 | −3.8% |
| 0.35–0.50 | **+7.6%** |
| 0.50–0.65 | **+5.6%** |
| 0.65–0.80 | **+3.8%** |
| >0.80 | **+3.5%** |

### E.2 Falling vs rising adds little, and its sign is not stable

Same EXTENDs split by the sign of Δp on that leg since its previous trade:

```
band        FALLING    FLAT   RISING
<0.35        -11.4%  -20.2%   -17.6%     falling is the LEAST bad
0.35-0.50     -6.8%   -9.1%    -8.7%
0.50-0.65     -2.1%   -3.1%    -5.5%     falling better
0.65-0.80     -0.5%   -1.1%    +1.9%     rising better -- sign FLIPS
>0.80         +2.3%   +4.3%    +1.9%     flat best
```

**Price level dominates; direction of travel does not survive as a rule.**
A 70c side falling from 80c and a 70c side rising from 60c differ by about
2.4 points of return, against a 17-point spread between the <0.35 and >0.80
bands — and the sign of the falling/rising effect reverses between bands.
This is consistent with the earlier falsification of simple directional
momentum for canonical L5, and it is a negative result for E.2 as a
standalone signal.

### E.5 More competitive maker placement makes it worse

```
arm                        matched  residual     net   fills  resid  pairC
BASELINE                      3.32     -7.10   -3.78      58     28  0.778
all rungs +1 tick             3.02     -7.38   -4.36      58     28  0.798
all rungs +2 ticks            2.72     -7.66   -4.94      58     28  0.818
COMPLETE rungs only +1t       3.30     -7.11   -3.81      58     28  0.779
EXTEND rungs only +1t         3.22     -7.25   -4.03      58     28  0.784
```

**Identical fills, identical terminal residual, identical 1/11 dominant-leg
win rate — at a strictly worse price.** Quoting closer to the touch bought
*the same losing inventory* for more money. It did not add a single fill,
because which leg fills is decided by which leg is falling toward us, not by
one tick of aggression.

### E.3 / E.4 — what is and is not answerable yet

**E.3 asymmetric residual permission is measurable only in one direction on
this data.** The restrictive half (suppress underdog EXTEND) is section 4.
The permissive half (allow more favored-side EXTEND) has **no events to
permit**: 0 of 32 EXTENDs at ≥$0.80, 1 at ≥$0.65. Loosening a gate that
never binds changes nothing, and E.5 shows aggression does not move the
fills into that band either.

**E.4 rebate-aware expectancy is not computable today.** The 21:15Z
reconciliation against Gemini's `maker-rebate/payouts` and
`summary/total` endpoints has not fired. The rebate term is therefore a
parameter defaulting to `None`, which propagates as **UNKNOWN — never zero,
never estimated**. It is not carried into any net figure in this document.
When a PAID amount is credited, the per-fill term drops in and the arms
re-run unchanged. For scale: no plausible rebate closes a −$1.68 gap on
$19.96 of turnover — the published formula caps at 5% of notional and the
eligible band is $0.20–$0.80.

---

## 6. P2-D — combinations

```
arm                             matched  residual     net  maxDD mQty resid  muts fills  pairC  domWon
1. complement repricing only       0.64      2.05    2.69   1.36    8    17   132    33  0.921    6/11
2. residual-price rule only        1.92     -4.77   -2.85   3.82   14    15    57    43  0.865    1/11
3. dynamic cap only                1.50     -3.18   -1.68   2.21   14    10    52    38  0.888    1/10
4. repricing + residual-price      0.42      0.48    0.90   1.16    9    11   130    29  0.935    5/11
5. repricing + best cap            0.31      0.37    0.68   1.33    9    11   130    29  0.949    5/11
6. cap + residual-price            1.98     -4.15   -2.17   3.14   14    14    56    42  0.861    1/11
7. 6 + COMPLETE quoted +1 tick     1.98     -4.15   -2.17   3.14   14    14    56    42  0.861    1/11
8. all four                        0.33      0.46    0.79   1.19    9    11   130    29  0.945    5/11
```

The split is clean and it is the point of the whole exercise:

```
POSITIVE net:  arms 1, 4, 5, 8    domWon 5-6/11   matched 0.31-0.64   muts 130-132
NEGATIVE net:  arms 2, 3, 6, 7    domWon 1/10-1/11 matched 1.50-1.98   muts 52-57
```

**Every arm that goes positive has destroyed the matched engine and flipped
the coin differently. Every arm that keeps the matched engine intact stays
negative.** Selecting arm 1 or arm 8 on this evidence would be selecting a
lucky draw on 11 windows — exactly the overfitting the owner forbade. No
winner is selected.

---

## 7. What P2 establishes

**Established, and supported outside COHORT-2:**

1. The residual-unit-cost gradient is per-**action**, not just per-window:
   37,649 L5 EXTENDs, monotone across five price bands, crossing zero at
   ~$0.65, using only decision-time information.
2. Reducing actions (COMPLETE) are positive in four of five bands. Gating
   them is unsupported.
3. Falling-vs-rising is a weak and sign-unstable effect next to price level.

**Established on COHORT-2, as mechanism rather than outcome:**

4. `resting_since` is re-stamped every cycle, so the 30s rest floor is an
   infinite floor. One line, in a bundle member, not yet fixed.
5. Repricing and placing compete for one 12-slot ledger; complement
   repricing exhausts it in every market and halves the fill count.
6. The controller cannot reach L5's profitable residual band: 0 of 32
   EXTENDs at ≥$0.80, 1 at ≥$0.65, and +1/+2 tick aggression does not change
   that.
7. Restrictive gates improve net monotonically and never reach zero.

**Not established, and not to be read into the tables:** that any arm is
profitable. The best mechanistically-sound arm is **−$1.68** against a
confirmed baseline of **−$4.96**, on a replay whose baseline is already
~$1.2 optimistic.

**Not modelled:** queue position; our own added size's effect on the
counterparties who traded; rebates (UNKNOWN until credited); and the
behaviour of a window that completed early and went on to OPEN again under a
changed inventory path beyond what the sequential replay itself produces.

---

## 8. The question, answered as far as P2 goes

> Can we retain the matched-inventory edge while materially reducing
> low-quality directional residual?

**On this evidence: only by trading less, and not to profitability.** The
matched edge and the low-quality residual are produced by the same maker-rung
ladder; every gate that removes the second removes a proportional share of
the first, and the ratio improves without crossing one.

The one adaptation with a positive sign in a large out-of-sample corpus —
residual on the favored side — is **not reachable by execution changes to
this policy**. A maker rung resting below the touch fills when its leg falls;
a leg that is falling is, by construction, not the favored leg. Reaching the
≥$0.65 band is not a repricing, cap, or aggression problem. It requires a
different entry mechanism, and that is a behavioural question, not an
execution one.

**Behavioural divergence from canonical L5**, for the record: L5 opens on
either side, extends across all five price bands (16,360 EXTENDs below $0.35
*and* 5,291 above $0.80), and takes 32,117 COMPLETEs — a two-sided book that
lands in the profitable band a third of the time. COHORT-2's frozen
controller extended 32 times, all but one below $0.65, and never once in the
band where L5 makes money on residual. **We are not replicating the part of
L5 that pays.**

Nothing here recommends a live change. P3 (aggressive-maker) is largely
pre-empted by E.5 and should be re-scoped before it is run.
