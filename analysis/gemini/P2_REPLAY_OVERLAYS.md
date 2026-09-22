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

---
---

# P2 ADDENDUM — the lifecycle bug fixed, P2-A re-run, and where L5's high-price EXTENDs come from

## 9. The fix

`WorkingOrders.reconcile` now dates an order from three sources, **oldest
wins**, and an order can never get younger:

```
our own prior record    carried forward across every reconcile
the venue's createdAt   authoritative, and survives a restart
neither                 UNKNOWN -- and UNKNOWN IS NOT ZERO
```

`reconcile_surface` no longer collapses a missing `resting_since` into `now`:
an age it cannot evaluate does not protect the order, which is the direction
it already took when no clock was supplied at all. Two edits, both in
`ws_continuous.py`. No bound moved: `STALE_ORDER_S` is still 30.0, the
working-order cap, the envelope and the ledger are untouched.

`bundle_sha 6fe90a9f...`, `executable_sha 89fc560c...`. The tree is DISARMED
(`REQUIRED_AUTH_ID`, `AUTHORIZED_SHA`, `AUTHORIZED_BUNDLE` all `None`;
`EXPECTED_AUTH_ID` the sentinel), so **no authorization was invalidated**,
and none was installed.

12 regression tests in `tests/test_working_order_age.py`, one per property
the owner named. One existing test asserted the defect and has been corrected
in place rather than deleted: `test_recovered_orders_have_an_age_so_the_floor
_is_evaluable` required an undatable order to default to `now`, reasoning it
would become eligible "once the floor elapses" — true of a single recovery,
false of a loop that re-stamps every cycle. Its *goal* is kept and still
asserted.

`continuous_probe.stale_orders` carries the same `or now` pattern. It is
called nowhere in production (asserted by `live_readiness`), so it is left
alone under "smallest possible change" and noted here.

## 10. P2-A re-run — and the 30-second floor is the wrong number

**The earlier repricing arms were not contaminated** — `replay_engine` never
re-stamped `resting_since`, so it modelled the corrected lifecycle from the
start; the "no reprice" baseline modelled the defect's *effect*. What was
missing is the arm that matters: **the intended rule itself**, no threshold.

```
arm                                 matched  residual    net  mQty resid  muts blocked fills  domWon
DEFECT: as COHORT-2 actually ran       3.32     -7.10  -3.78    15    28    77       0    58    1/11
INTENDED RULE (both legs)              0.38     -0.71  -0.33     5     8   132      23    18     1/4
INTENDED RULE, complement only         0.90      1.08   1.98     8    18   132      21    34    5/11
```

Raising the ledger to 24 or 48 slots/market changes **nothing** (same 18
fills, same -0.33): at 160 mutations the rule saturates. The budget is not
the constraint — the rule is.

**Why: our maker fills need minutes, not 30 seconds.** From the 56 confirmed
orders' `createdAt` -> `updatedAt`:

```
time resting before the fill   min 2s   MEDIAN 64s   max 737s
  filled within  30s   17 of 56   (30%)
  filled within  60s   27 of 56   (48%)
  filled within 120s   35 of 56   (62%)
  filled within 240s   46 of 56   (82%)
  filled within 480s   54 of 56   (96%)
```

**Enforced as written, the 30s floor cancels 70% of the orders that actually
filled.** The defect was accidentally supplying the patience the strategy
needs. Sweeping the floor against that distribution:

```
floor  both legs                      complement only
        matched residual    net        matched residual    net  mQty fills domWon
  30s      0.38    -0.71  -0.33          0.84     1.37   2.21     9    35   5/11
  60s      0.80    -0.27   0.53          1.25    -0.44   0.81    12    40    3/8
 120s      1.20    -2.22  -1.02          1.73    -2.91  -1.18    14    51   2/10
 240s      2.10    -5.17  -3.07          2.52    -4.83  -2.30    18    59    1/9
 480s      3.01    -7.29  -4.28          3.10    -7.19  -4.09    15    58   1/11
```

**The same trap as before, and it must be read the same way.** Every arm
whose net goes positive has `domWon` at 3-5 of 8-11 — the coin landed
differently — while `matched` collapses. The arms with `domWon` at 1/9-1/11
are the ones where the directional hit rate is *unchanged*, i.e. where the
only thing that moved is the mechanism.

One arm is mechanistically clean, and it is the first in this programme:

```
complement repricing, 240s floor, at the AUTHORIZED 12-slot ledger
  matched  +2.57   (qty 18, up from 15 -- the most matched inventory of any arm)
  residual -4.19   (qty 22, down from 28)
  net      -1.62   (from -3.78)
  fills        58  (all of them kept)
  mutations   109  (inside 132)
  domWon      1/8  (UNCHANGED -- this is not a coin flip)
```

More completion -> more matched pairs -> less residual, with the adverse
selection rate untouched. The 240s figure is **not fitted to P&L**: it is
where 82% of confirmed fills survive, chosen from the fill-time distribution
before the arms were run, and the replay independently agrees. It still does
not cross zero, on 11 windows, against a replay baseline already ~$1.2
optimistic.

**Recommendation on the floor: do not ship `30s`.** It is a parameter
inherited from a lane whose fills arrive fast, and this market's do not.
What the evidence supports is a floor derived from measured time-to-fill,
re-measured per family — not a constant.

## 11. Where L5's high-price EXTENDs come from

The owner's primary question. 37,649 residual-increasing L5 EXTENDs, split
by the leg's price at the fill:

```
band          n      addHeavy   TTE   qty   dp>0   gapSame  gapOpp  matchedBefore  |resid|  run
>=0.65    11,120         95%   368s  20.0    64%      4.0s    9.0s           499       99    2
.35-.65   10,150         94%   572s  13.1    50%      3.0s   12.0s           240       69    2
<0.35     16,360         96%   291s  10.0    35%      2.0s   14.0s           879      145    3
```

**It is not a different placement rule.** Every structural marker is the
same: he adds to the *heavy* side 94-96% of the time in all three bands, the
median same-side gap is 2-4 seconds in all three, and the median same-side
run is 2-3 in all three. Nor is heavy-vs-light the driver — within the high
band, adding to heavy wins 81% and adding to light wins 82%; within the low
band, 12% and 15%. **Price is the driver; the side-relative-to-inventory is
not.**

What does differ is regime: high-price EXTENDs happen **earlier** (368s vs
291s TTE), at **twice the size** (20 vs 10), on a **smaller existing
residual** (99 vs 145), and while the leg is **drifting up** (64% dp>0 vs
35%). 100% of the high band is on a favored leg (>0.50) by construction, and
89% already sit on matched inventory.

**So L5 does not "go and buy favorites". He is quoting both sides
continuously, at size, with a median 2-4 seconds between same-side fills, and
roughly a third of what comes back to him is priced above $0.65.** Our
controller rests one rung per leg below the touch and waits a median of 64
seconds; what comes back to us is, by construction, the leg that fell.

**This does not yet license a favored-side placement rule**, and I am not
proposing one. The evidence says the difference is *fill frequency and
book participation*, not a second desired-surface. Whether a passive
placement can be constructed that participates on the rising leg without
crossing is the open question, and E.5 already falsified the naive version
(+1/+2 ticks bought identical inventory at worse prices). Item 4 therefore
remains **open, with a negative result on the obvious approach** rather than
a proposal.

## 12. Still open

- **Rebate (item 6)** — the 21:15Z reconciliation has not fired
  (`trig_01YUSyE8mwwzdHAmKb61oKZH`, armed, precondition satisfied). No PAID
  figure exists, so no rebate enters any number here.
- **Item 4** — a market-state-dependent desired surface. Not proposed:
  the source-accounted L5 evidence above says the mechanism is participation
  rate, not a second placement rule, and that is not something a
  below-touch rung can be re-parameterised into.
