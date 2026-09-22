# P1 — Terminal residual: when it accumulated, and whether it could have been undone

**Status: ANALYSIS ONLY. Nothing armed, nothing deployed, no strategy changed.**
Offline and read-only over artifacts already on disk, plus two public,
unauthenticated venue reads named below. COHORT-2 is complete and disarmed.

The owner's question:

> Can we preserve the +matched economics while preventing terminal residual
> from overwhelming them, using a rule supported outside these 11 markets?

**Short answer, in three parts.**

1. **The residual loss was not bad luck.** L5's own 2,193-window history,
   bucketed by a quantity the controller already computes every cycle,
   predicts **-$6.40** of the **-$8.00** COHORT-2 lost on residual.
2. **There is a control-plane defect underneath it, visible without any
   counterfactual.** The completion order was resting in 430 of 433 post-onset
   cycles, at a **median 30 ticks behind its own best bid**, while the planner
   recomputed the correct price every cycle, planned a mutation on 9.1% of
   them, and forfeited **58 of 132** action slots. The completion rule we
   shipped (`pair <= 0.96`) fires **zero times in eleven windows**.
3. **But fixing that is not obviously enough.** The mechanism that manufactures
   the residual is the same mechanism that makes the matched pairs cheap, so no
   truncation rule tested here keeps one without losing the other, and no arm
   tested — completion or prevention — turns these eleven markets positive.

---

## 0. Sources, and what is new

| source | what it gives | status |
|---|---|---|
| `analysis/gemini/cohort_window_20260922T1*.json` (11) | per-cycle inventory, YES/NO touch, depth, **public trade tape sample**, working orders | on disk |
| `after.orders_seen` + `after.trades` | 56 authoritative fills: price, leg, `aggressor`, `createdAt`, `updatedAt` | on disk (P0) |
| `cohort_run_20260922T113521Z.json` -> `cohort_state` | resolution side + payout, 10 of 11 windows | on disk |
| `GET /v1/prediction-markets/events/BTC15M2609221430` | **W11's resolution**, absent from every artifact | **public read, 2026-09-22** |
| `analysis/l5/l5_windows_summary.csv` | 2,216 L5 windows; residual side/qty/cost, winning outcome | on disk |

`src/gemini_execution/residual_timeline.py` (new, `GEMINI_RESIDUAL_TIMELINE_V1`)
and `tests/test_residual_timeline.py` (32 tests) are the work.

**W11 was never settled on disk.** The disarm scoreboard carried its -$1.14
as a derived figure. The public contract read resolves it properly:
`GEMI-BTC15M2609221430-UP` -> `resolutionSide: no`, settlement 85,974.08
against strike 86,340.69. UP lost; our 4 UP contracts paid $0; net -$1.14.
The same read reproduces W10's `yes`, which is how we know the endpoint and
the runner agree.

---

## 1. The fill model is calibrated, not assumed

Everything counterfactual below rests on one claim: that we can tell, from the
public tape, whether a maker order **would** have filled. That claim is tested
against 56 fills we know happened.

The venue publishes **one ladder** (the UP contract) and mirrors it:
`NO bid = 1 - YES ask`. `complement_identity` reports the deviation and it is
`0.00` on **every readable cycle of every window**: 720 of 750 cycles carried a
readable touch, and the deviation is zero on all 720 (asserted).
`/v1/trades/{symbol}` prints in UP price terms with an aggressor side, so our
own maker fills appear on the tape on the **opposite** side at the **mirrored**
price:

```
our YES buy @0.46  ->  public print  0.46 SELL   (a taker sold into our bid)
our NO  buy @0.44  ->  public print  0.56 BUY    (a taker lifted our offer)
```

```
CALIBRATION          fills checked            56
                     present on tape          56
                     aggressor side correct   56
                     mirrored price correct   56
                     verdict                  CONFIRMED,  0 misses
```

So the rule used from here on:

```
resting BUY YES @q fills when a SELL-aggressor print occurs at <= q
resting BUY NO  @p fills when a BUY-aggressor  print occurs at >= 1-p

THROUGH   strictly past our level. Under price priority a taker cannot
          reach a worse price while our better-priced order rests.
AT_LEVEL  exactly at our level -- queue decides, and queue is not observable.
QUOTE     the quote reached our level with no print. Weakest; contrast only.
```

Conclusions use **THROUGH**. A print that was one of our own fills is excluded
as evidence — it cannot also have filled a second, hypothetical order. The
tape is POLLED, so a burst between reads is lost; that can only **remove**
evidence of an opportunity, never invent one.

---

## 2. When the residual accumulated

**All eleven windows ended unmatched.** Terminal residual is not an edge case
of this policy, it is its normal terminal state (asserted as an invariant).

| window | r | dominant | onset TTE | carried | cycles | final unit cost |
|---|---|---|---|---|---|---|
| 1200 | 3 | YES | 541s | 515s | 62 | $0.1625 |
| 1215 | 2 | NO | 854s | 772s | 81 | $0.3725 |
| 1230 | 2 | NO | 474s | 210s | 27 | $0.5275 |
| 1245 | 4 | YES | 678s | 598s | 74 | $0.4500 |
| 1300 | 2 | YES | 444s | 417s | 51 | $0.3450 |
| 1315 | 4 | NO | 821s | 409s | 45 | $0.2525 |
| 1330 | 1 | YES | 314s | 49s | 6 | $0.1633 |
| 1345 | 3 | NO | 802s | 121s | 10 | $0.2900 |
| 1400 | 3 | NO | 670s | 134s | 13 | $0.2850 |
| 1415 | 2 | NO | 181s | 12s | 2 | $0.5900 |
| 1430 | 4 | YES | 828s | 584s | 62 | $0.2850 |

Onset is the earliest cycle from which the dominant leg never changed and the
residual never returned to zero. Median carry: **~7 minutes of a 15-minute
contract.** There was no shortage of *time* to complete.

### The mechanism, from the chronological fill record

56 fills in time order, with the running imbalance. The pattern is exact:

> **Every single EXTEND filled at a lower price than the previous fill on that
> leg. Without exception, in all eleven windows.**

```
1200   NO 0.58  ->  YES 0.33  0.19  0.11  0.02        (EXTEND x4, falling)
1315   NO 0.37      0.29      0.22  0.13              (EXTEND x3, falling)
1345   NO 0.43      0.31      0.24  0.18              (EXTEND x3, falling)
1430  YES 0.39      0.35      0.25  0.15              (EXTEND x3, falling)
1245  YES 0.54      0.46      0.40  0.40              (EXTEND x3, falling)
```

This is mechanically forced, not coincidental. A maker rung resting **below**
the touch can only fill when the price comes **down** to it. So on this policy:

```
EXTEND == "this leg is falling" == "this leg is losing"
```

and the ladder is therefore a machine for manufacturing residual on the
**cheap** side. That is the finding the rest of the study turns on.

---

## 3. The out-of-sample test: L5's 2,193 windows

If cheap residual is structurally a losing residual, it should show up in a
corpus that has nothing to do with our eleven markets. Bucketing **L5's own
terminal residual by its unit cost** (his cost basis, his venue, his 22 days):

| residual unit cost | windows | cost basis | contract-wtd win rate | cost-implied | gap | **return on cost** |
|---|---|---|---|---|---|---|
| < $0.20 | 423 | $6,377 | 2.7% | 13.1% | **-10.4** | **-79.4%** |
| $0.20-0.35 | 333 | $15,246 | 2.6% | 27.1% | **-24.5** | **-90.3%** |
| $0.35-0.50 | 251 | $13,954 | 18.7% | 42.3% | **-23.6** | **-55.5%** |
| $0.50-0.65 | 312 | $13,773 | 49.4% | 58.7% | -9.4 | -15.5% |
| $0.65-0.80 | 441 | $23,768 | 90.2% | 73.6% | +16.7 | **+23.4%** |
| $0.80+ | 433 | $26,273 | 96.5% | 86.5% | +10.0 | **+12.2%** |

The gradient is **monotone and it survives every split available**:

| bucket | BTC | ETH | 15m | 5m |
|---|---|---|---|---|
| <0.20 | -79.5% | -77.7% | -85.1% | -69.5% |
| 0.20-0.35 | -92.0% | -39.2% | -91.7% | -86.9% |
| 0.35-0.50 | -58.8% | -26.4% | -72.8% | -23.6% |
| 0.50-0.65 | -17.6% | -7.4% | -32.6% | -6.9% |
| 0.65-0.80 | +26.4% | +14.3% | +25.3% | +22.9% |
| 0.80+ | +13.0% | +8.9% | +17.0% | +11.6% |

Two assets and two horizons, same sign pattern, same ordering. Note also that
at the **window** level L5's dominant leg wins 49.9% — a coin flip. The damage
is entirely **size- and price-weighted**: he wins the small, dear residuals and
loses the big, cheap ones.

### The prediction

Apply those six L5 returns — fitted on a different trader, a different venue
and 2,193 different windows — to COHORT-2's residual book:

| window | r | unit cost | cost | L5 bucket | L5 return | predicted |
|---|---|---|---|---|---|---|
| 1200 | 3 | 0.1625 | 0.49 | <0.20 | -79.4% | -0.39 |
| 1215 | 2 | 0.3725 | 0.74 | 0.35-0.50 | -55.5% | -0.41 |
| 1230 | 2 | 0.5275 | 1.05 | 0.50-0.65 | -15.5% | -0.16 |
| 1245 | 4 | 0.4500 | 1.80 | 0.35-0.50 | -55.5% | -1.00 |
| 1300 | 2 | 0.3450 | 0.69 | 0.20-0.35 | -90.3% | -0.62 |
| 1315 | 4 | 0.2525 | 1.01 | 0.20-0.35 | -90.3% | -0.91 |
| 1330 | 1 | 0.1633 | 0.16 | <0.20 | -79.4% | -0.13 |
| 1345 | 3 | 0.2900 | 0.87 | 0.20-0.35 | -90.3% | -0.79 |
| 1400 | 3 | 0.2850 | 0.85 | 0.20-0.35 | -90.3% | -0.77 |
| 1415 | 2 | 0.5900 | 1.18 | 0.50-0.65 | -15.5% | -0.18 |
| 1430 | 4 | 0.2850 | 1.14 | 0.20-0.35 | -90.3% | -1.03 |
| **total** | **30** | | **10.00** | | | **-6.40** |

```
L5-PREDICTED   -6.40
OBSERVED       -8.00        the prediction accounts for 80% of the loss
```

**78% of COHORT-2's residual cost basis ($7.76 of $10.00) sat in buckets
where L5 loses 55-90%.**
The -$8.00 was not a draw from a fair coin. It was the expected cost of the
position we were holding, and it was knowable at the time from a number the
controller already computes every cycle (`avg_yes_cost` / `avg_no_cost`).

---

## 4. Could it have been completed? Causally, no

`complement_opportunity` scans the whole post-onset tape with hindsight and is
an explicit **upper bound**, not a rule: at best $6.70 of the $8.00 was
theoretically reachable, at pair costs of 0.88-1.06.

The causal simulator is the real test. At each cycle a policy prices a
complement using only that cycle's inventory and book; the order rests to the
next cycle; the tape decides. **A mutation is a price change, not a cycle** —
the controller keeps an order resting.

```
tier = THROUGH, 11 windows, frozen baseline net -4.96

arm                         units  muts   added$   delta   cohort net
frozen (no completion)          0     0     0.00    0.00        -4.96
pair <= 0.96  (SPRING RULE)     0    37     0.00    0.00        -4.96
pair <= 1.00                    0    34     0.00    0.00        -4.96
join touch                      4   406     2.50   +1.50        -3.46
join touch +1 tick              8   363     5.11   +2.89        -2.07
join touch +2 ticks             9   343     6.05   +2.95        -2.01
cost-aware $0.50 / +1 tick      7   332     4.82   +2.18        -2.78
```

### Finding 4a — the shipped completion rule never fires

**`pair <= 0.96` completes zero units across all eleven windows** (asserted as
a test). So does `pair <= 1.00`. By the time a residual exists, its complement
is the leg that has been *rising*, and it costs more than the target allows.
The discipline declines precisely when completion is worth most. This is a
defect in the rule, not in its implementation.

It is also economically backwards. With the dominant leg averaging $0.33 and —
per section 3 — winning roughly 3% of the time in that bucket, the position's
expected cost is ~$0.30/contract. Completing at a pair cost of $1.05 costs
$0.05/contract. **Refusing to complete above $1.00 pays 30c to avoid a 5c
fee.** "Do not optimize for avoiding `$0 Paid out`" cuts both ways: the rule
that optimizes pair cost is optimizing the wrong number.

### Finding 4b — the complement WAS resting, wanted, and 30 cents stale

Before blaming the market, look at what our own orders were doing. For every
cycle after residual onset, with the complement leg's own best bid beside the
price we actually had working:

| window | cycles after onset | complement resting | COMPLETE decided | median ticks behind touch | worst |
|---|---|---|---|---|---|
| 1200 | 62 | 62 | 59 | 44 | 46 |
| 1215 | 81 | 79 | 80 | 10 | 29 |
| 1230 | 27 | 27 | 27 | 41 | 52 |
| 1245 | 74 | 74 | 67 | 30 | 59 |
| 1300 | 51 | 50 | 47 | 29 | 58 |
| 1315 | 45 | 45 | 39 | 27 | 39 |
| 1330 | 6 | 6 | 0 | — | 0 |
| 1345 | 10 | 10 | 10 | 44 | 48 |
| 1400 | 13 | 13 | 13 | 40 | 45 |
| 1415 | 2 | 2 | 2 | 23 | 24 |
| 1430 | 62 | 62 | 54 | 29 | 38 |

```
pooled   complement order resting in 430 of 433 post-onset cycles
         COMPLETE decided in 398 of 433
         MEDIAN 30 TICKS BEHIND THE COMPLEMENT'S OWN BEST BID
         mean 27.9, worst 59, n = 400 cycle-observations
```

**The completion order was there, it was wanted, and it was priced 30 cents
away from where trading was happening.** It could only have filled if the
market moved 30c in the direction that would have meant the dominant leg was
winning — that is, in exactly the scenario where completion was unnecessary.

### Finding 4c — the planner recomputes the price and then does not use it

This is not the ladder's fault and not the budget's. W05, cycles 41-46, is
representative:

```
cycle  decided NO price   working NO   plan
  41        0.35            0.29       {keep:[], place:[], cancel:[], mutations:0}
  42        0.50            0.29       {... mutations:0}
  43        0.49            0.29       {... mutations:0}
  44        0.48            0.29       {... mutations:0}
  45        0.45            0.29       {... mutations:0}
  46        0.36            0.29       {... mutations:0}
```

The controller **tracks the touch correctly every cycle** and then plans zero
mutations, because an order is already working and the planner treats
"working" as satisfying the intent regardless of where the market has gone.
`holds` and `held_by` are empty throughout: nothing blocked it.

Across all eleven windows:

```
68 of 750 cycles (9.1%) planned ANY mutation
distinct working prices per leg, per window: 1 to 4
ledger        132 slots granted, 74 consumed, 58 FORFEITED (44%)
              every window ended with 4-6 unspent slots
```

**So the action budget was never the binding constraint.** Every market
forfeited a third to a half of its ledger while holding a residual it had
decided to complete. The binding constraint is a planner that does not
re-price a resting order.

### Finding 4d — only after 4c is fixed does the budget bind

Once the complement does chase the touch, the touch moves nearly every cycle
and chasing it costs ~33 mutations per market against a 12-slot ledger:

```
extra mutations/market   units   added$   delta    cohort net
                     0       0     0.00    0.00         -4.96
                     4       2     1.59   +0.41         -4.55
                     8       3     2.26   +0.74         -4.22
                    12       6     4.29   +1.71         -3.25
                    16       7     4.98   +2.02         -2.94
                    24       8     5.59   +2.41         -2.55
                  none       8     5.11   +2.89         -2.07
```

The 58 forfeited slots alone buy roughly the `b=4` row per market. Getting the
full +$2.89 needs a budget the current envelope does not grant. Worth stating
plainly: raising it adds **no** dollars at risk. It does not touch size, `q`,
exposure, concurrency or the number of markets — it re-prices one order that
is already inside the envelope. "Favor increasing frequency before size" is
exactly this lever.

---

## 5. Could it have been prevented? Partly — but matched pays for it

Purely mechanical counterfactual: walk the authoritative fills in time order
and refuse any that would breach a cap. No fill model needed; it only removes
our own 1-contract orders.

```
rule                           fills  spend  payout    NET  matched  residual  r ct
FROZEN CAP4 (actual)              56  19.96   15.00  -4.96    +3.04     -8.00    30
CAP3                              53  19.28   15.00  -4.28    +3.04     -7.32    27
CAP2                              46  17.69   14.00  -3.69    +2.90     -6.59    20
CAP1                              34  13.56   12.00  -1.56    +2.40     -3.96    10
no EXTEND below $0.30             39  16.84   13.00  -3.84    +1.90     -5.74    17
no EXTEND below $0.40             29  13.42    9.00  -4.42    +1.08     -5.50    11
no EXTEND below $0.50             25  11.69    9.00  -2.69    +1.02     -3.71     7
CAP2 + no EXTEND below $0.30      34  14.64   12.00  -2.64    +1.86     -4.50    12
no EXTEND at all                  19   8.50    8.00  -0.50    +0.80     -1.30     3
```

**Every arm is still net-negative**, and the reason is the coupling:

```
              matched   residual   matched per pair   ratio
CAP4            +3.04      -8.00          $0.234       0.38
CAP1            +2.40      -3.96          $0.200       0.61
no EXTEND       +0.80      -1.30          $0.100       0.62
```

Extending down is what makes the matched pairs cheap **and** what makes the
residual cheap. They are the same trade seen from two sides. Truncation
improves the matched:residual ratio from 0.38 to ~0.62 but never past 1.0 in
this sample, and it does so by making the matched book smaller and worse.

**So the honest answer to "preserve matched while preventing residual" is:
not by capping, and not by completing under the current budget.** Nothing
tested here turns these eleven markets positive.

---

## 6. What this does and does not license

**Established, and supported outside the eleven markets:**

1. Residual unit cost predicts residual return, monotonically, across 2,193 L5
   windows, both assets and both horizons. Below ~$0.50 it is heavily
   negative; above ~$0.65 it is positive.
2. Applied blind to COHORT-2 it predicts -$6.40 of the observed -$8.00.
3. The maker-rung EXTEND ladder can only fill on a falling leg, so it lands in
   the worst bucket by construction.
4. `pair <= 0.96` completes nothing once a residual exists (11/11 windows).
5. All eleven windows ended unmatched.
6. The complement order rested in 430 of 433 post-onset cycles at a median 30
   ticks behind its own best bid, while the planner recomputed the correct
   price every cycle and planned a mutation on 9.1% of them. 58 of 132 action
   slots were forfeited. This is a control-plane defect, observable without
   any counterfactual, and it is independent of the eleven markets' outcomes.

**Established on eleven markets only, and therefore a hypothesis:**

7. Aggressive complement quoting recovers +$2.89 of $8.00 with an unbounded
   mutation budget, +$0.41-0.74 within the slots already being forfeited.
8. In 10 of 11 windows the dominant leg lost. At n=11 that is weak against a
   coin (Poisson-binomial on each window's own unit cost: 3.72 wins expected,
   1 observed, P(X<=1) = 0.062) — suggestive, not proof, and section 3 is the
   reason to believe it
   anyway.

**Not modelled, and stated rather than buried:** queue position; the
working-order slot an extra complement would occupy; the effect our own added
size would have had on the counterparties who traded; and the fact that a
completed window would have gone on to OPEN again, which this marginal overlay
does not simulate. That full re-simulation is P2.

**No strategy change is recommended here.** P1 is evidence. The comparison
table and the next-authorization proposal are P9, after P2's overlays run.

---

## 7. What P2 should run, given this

- **Overlay A (late EXTEND suppression)** should be re-specified as a *price*
  rule, not a *time* rule: section 2 shows the damage is priced, not timed.
- **Overlay B (CAP4 -> CAP1)** — measured above as a floor: -4.96 -> -1.56,
  but matched falls 3.04 -> 2.40. Needs full re-simulation, not truncation.
- **Overlay C (complement priority)** — the one arm with headroom. Fix the
  re-pricing defect (4c) first; only then sweep the budget (4d), because until
  the planner re-prices at all, a larger ledger changes nothing.
- **Overlay D (residual-aware asymmetry)** — section 3 gives it a threshold
  that was not fitted on COHORT-2: pay up to complete residual whose unit cost
  is under ~$0.50; hold out above ~$0.65.

## 8. The final question, answered as far as this evidence goes

> Can we preserve the +matched economics while preventing terminal residual
> from overwhelming them, using a rule supported outside these 11 markets?

**Not with any rule tested here.** The rule that *is* supported outside the
eleven markets — "cheap residual is losing residual" — is an indictment of the
EXTEND ladder itself, and the ladder is also what produces the cheap matched
pairs. The two are not separable by capping or by completing.

What the evidence does support testing next, in order:

1. **Fix the planner so a resting order re-prices.** It already computes the
   right price every cycle and discards it. This is a defect, not a strategy
   change, and 58 forfeited slots per eleven markets say it is affordable.
   Then quote the complement at or inside the touch from the moment an EXTEND
   fills. Costs no additional dollars at risk.
2. **Make the completion decision on residual unit cost, not pair cost.** The
   threshold comes from L5's history, not from these eleven markets.
3. **Only then** consider capping, and measure matched loss against residual
   gain rather than assuming a tighter cap is safer.

Whether (1)+(2) together clear zero is unknown and this study does not claim
it. Eleven markets cannot answer it; a fresh q=1 cohort under an adapted
policy can, and that proposal belongs to P9.
