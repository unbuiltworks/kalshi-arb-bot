# Cell C capacity and execution economics — what it would take to reach $5

Execution-economics study, not strategy work. **Frozen Cell C is unchanged**
and re-runs identically (gross +$10.0346, net −$0.04948/window). Signals,
thresholds, cadence, trail and timestamps are untouched; only order size
varies. CF_MATCH, PM_V1 and every no-SELL matching variant are **closed as
structurally failed** — no PM_V2, no EXTEND/MATCH flow tuning.

## The fill model, and why it is not a linear extrapolation

Fills are not extrapolated. At each order the **displayed touch is filled at
the touch**, and anything beyond it is **charged an impact measured from
this tape's own prints** — the 1-second mid move in the aggressor's
direction — rather than quoted from a level the tape does not contain.

That impact was measured first, over 117,465 prints:

| print size (shares) | n | mean 1s impact | median | p90 |
|---|---|---|---|---|
| <10 | 49,379 | +0.31¢ | 0.0 | +2¢ |
| 10–50 | 38,276 | +0.18¢ | 0.0 | +2¢ |
| 50–200 | 23,440 | +0.20¢ | 0.0 | +2¢ |
| 200–1000 | 6,049 | +0.24¢ | 0.0 | +2¢ |
| ≥1000 | 321 | +0.45¢ | 0.0 | +2¢ |

**Impact barely scales with size on this book** — even 1000-share prints
move the mid less than half a cent on average, and the median move is zero
at every size. So the ladder is run twice: charging the **mean (0.25¢)** and
the **p90 (2¢)** for every beyond-touch share.

## Frozen Cell C at increasing size (28 windows, $ per window unless noted)

| envelope | alloc | impact charge | gross before costs | slippage/depth | taker fees | verified maker comp | **net/window** | median | LB1 | LB3 | worst | peak capital | peak residual | turnover | beyond-touch shares | orders >10% of touch | net ÷ peak capital |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| $8.03 | $1.0000 | 0.25c | +0.3604 | 0.0004 | 0.4078 | 0.0000 | **-0.0478** | -0.0462 | -0.0709 | -0.1086 | -0.7785 | 8.03 | 8.1 sh | 2.43 | 4.5 | 1.6% | -0.00596 |
| $8.03 | $1.0000 | 2c (p90) | +0.3604 | 0.0032 | 0.4079 | 0.0000 | **-0.0507** | -0.0489 | -0.0738 | -0.1107 | -0.7785 | 8.03 | 8.1 sh | 2.43 | 4.5 | 1.6% | -0.00631 |
| $25 | $3.1133 | 0.25c | +1.1030 | 0.0020 | 1.2606 | 0.0000 | **-0.1597** | -0.2434 | -0.2318 | -0.3477 | -2.4286 | 25.01 | 25.2 sh | 7.56 | 22.3 | 3.6% | -0.00638 |
| $25 | $3.1133 | 2c (p90) | +1.1030 | 0.0160 | 1.2608 | 0.0000 | **-0.1738** | -0.2739 | -0.2465 | -0.3624 | -2.4657 | 25.01 | 25.2 sh | 7.56 | 22.3 | 3.6% | -0.00695 |
| $50 | $6.2267 | 0.25c | +2.1825 | 0.0051 | 2.5108 | 0.0000 | **-0.3334** | -0.5431 | -0.4774 | -0.7091 | -4.9433 | 50.01 | 50.4 sh | 15.12 | 57.0 | 5.7% | -0.00667 |
| $50 | $6.2267 | 2c (p90) | +2.1825 | 0.0407 | 2.5116 | 0.0000 | **-0.3699** | -0.5626 | -0.5089 | -0.7421 | -5.0301 | 50.01 | 50.4 sh | 15.12 | 57.0 | 5.7% | -0.00740 |
| $75 | $9.3400 | 0.25c | +3.2342 | 0.0100 | 3.7595 | 0.0000 | **-0.5354** | -0.8775 | -0.7492 | -1.0990 | -7.4871 | 75.02 | 75.6 sh | 22.68 | 112.5 | 8.0% | -0.00714 |
| $75 | $9.3400 | 2c (p90) | +3.2342 | 0.0804 | 3.7611 | 0.0000 | **-0.6072** | -0.9655 | -0.8145 | -1.1684 | -7.7064 | 75.02 | 75.6 sh | 22.68 | 112.5 | 8.0% | -0.00809 |
| $100 | $12.4533 | 0.25c | +4.2812 | 0.0153 | 5.0024 | 0.0000 | **-0.7365** | -1.3202 | -1.0191 | -1.4864 | -9.7557 | 100.02 | 100.8 sh | 30.24 | 171.1 | 9.8% | -0.00736 |
| $100 | $12.4533 | 2c (p90) | +4.2812 | 0.1222 | 5.0049 | 0.0000 | **-0.8459** | -1.4192 | -1.1156 | -1.5877 | -9.9978 | 100.02 | 100.8 sh | 30.24 | 171.1 | 9.8% | -0.00846 |
| $150 | $18.6800 | 0.25c | +6.3131 | 0.0265 | 7.4899 | 0.0000 | **-1.2033** | -2.2641 | -1.6079 | -2.3116 | -14.3132 | 150.04 | 151.1 sh | 45.24 | 296.4 | 13.2% | -0.00802 |
| $150 | $18.6800 | 2c (p90) | +6.3131 | 0.2117 | 7.4952 | 0.0000 | **-1.3938** | -2.3434 | -1.7752 | -2.4762 | -14.5553 | 150.09 | 151.1 sh | 45.24 | 296.4 | 13.2% | -0.00929 |
| $200 | $24.9066 | 0.25c | +8.2933 | 0.0402 | 9.9750 | 0.0000 | **-1.7219** | -3.1947 | -2.2176 | -3.1475 | -18.8706 | 200.07 | 201.5 sh | 59.69 | 449.8 | 17.3% | -0.00861 |
| $200 | $24.9066 | 2c (p90) | +8.2933 | 0.3213 | 9.9842 | 0.0000 | **-2.0123** | -3.4392 | -2.4941 | -3.4104 | -19.1128 | 200.24 | 201.5 sh | 59.69 | 449.8 | 17.3% | -0.01005 |
| $8.03 | +0.3604 | 0.4082 | +0.3604 | -0.6396 | -2.6396 | -4.6396 | **impossible — gross < $5** |
| $25 | +1.1030 | 1.2626 | +1.1030 | +0.1030 | -1.8970 | -3.8970 | **impossible — gross < $5** |
| $50 | +2.1825 | 2.5159 | +2.1825 | +1.1825 | -0.8175 | -2.8175 | **impossible — gross < $5** |
| $75 | +3.2342 | 3.7696 | +3.2342 | +2.2342 | +0.2342 | -1.7658 | **impossible — gross < $5** |
| $100 | +4.2812 | 5.0177 | +4.2812 | +3.2812 | +1.2812 | -0.7188 | **impossible — gross < $5** |
| $150 | +6.3131 | 7.5164 | +6.3131 | +5.3131 | +3.3131 | +1.3131 | **83%** |
| $200 | +8.2933 | 10.0152 | +8.2933 | +7.2933 | +5.2933 | +3.2933 | **67%** |

**Slippage is not the problem.** Even at $200 with the p90 charge, total
beyond-touch cost is **$0.32/window against $9.98 of taker fees** — 3% of
friction. The venue absorbs the size; the fee schedule does not.

**But expectancy degrades with size anyway.** Net ÷ peak capital worsens
monotonically, −0.00596 at $8.03 to **−0.00861** at $200 (−0.01005 at p90
impact). Scaling makes the loss *more* than proportional, because gross
grows sublinearly (24.9× the size buys 23.0× the gross) while fees grow
linearly.

## Maximum defensible capacity

The tape is top-of-book only, so the honest capacity limit is where orders
stop being priceable from evidence:

| envelope | $8 | $25 | $50 | $75 | **$100** | $150 | $200 |
|---|---|---|---|---|---|---|---|
| orders taking >10% of displayed touch | 1.6% | 3.6% | 5.7% | 8.0% | **9.8%** | 13.2% | 17.3% |
| shares filled beyond the touch | 4.5 | 22.3 | 57.0 | 112.5 | **171.1** | 296.4 | 449.8 |

**Maximum defensible capacity is about $100 of peak capital.** Above it,
one order in seven takes more than a tenth of the displayed touch and the
replay is pricing more impact than it can evidence. The $150 and $200 rows
are reported and marked not defensible.

## What Cell C would need — the friction solve

## Maximum all-in friction Cell C can tolerate, solved per size

Friction = taker fees + slippage. Gross before costs is taken from the run at that size, so the solve already contains any gross degradation from depth.

| envelope | gross before costs /window | actual friction /window | **friction allowed for break-even** | **for +$1** | **for +$3** | **for +$5** | required friction cut for $5 |
|---|---|---|---|---|---|---|---|
| $8.03 | +0.3604 | 0.4082 | +0.3604 | -0.6396 | -2.6396 | -4.6396 | **impossible — gross < $5** |
| $25 | +1.1030 | 1.2626 | +1.1030 | +0.1030 | -1.8970 | -3.8970 | **impossible — gross < $5** |
| $50 | +2.1825 | 2.5159 | +2.1825 | +1.1825 | -0.8175 | -2.8175 | **impossible — gross < $5** |
| $75 | +3.2342 | 3.7696 | +3.2342 | +2.2342 | +0.2342 | -1.7658 | **impossible — gross < $5** |
| $100 | +4.2812 | 5.0177 | +4.2812 | +3.2812 | +1.2812 | -0.7188 | **impossible — gross < $5** |
| $150 | +6.3131 | 7.5164 | +6.3131 | +5.3131 | +3.3131 | +1.3131 | **83%** |
| $200 | +8.2933 | 10.0152 | +8.2933 | +7.2933 | +5.2933 | +3.2933 | **67%** |

Reading the four targets asked for:

1. **Break-even** — needs friction cut from $0.408 to $0.360 per window at
   base size: a **12% cut**, and the same 12% at every envelope, because
   both scale together.
2. **+$1/window** — first reachable at **$25** (allows $0.103 of friction
   against $1.263 actual: a **92% cut**), comfortably at $50 (allows $1.183
   against $2.516: a **53% cut**).
3. **+$3/window** — first reachable at **$75** (needs a 94% cut), workable
   at **$100** (allows $1.281 against $5.018: a **74% cut**).
4. **+$5/window** — **impossible at or below $100 at any friction**, because
   Cell C's gross before costs never reaches $5: $4.28/window at $100 even
   with costs set to zero. It becomes arithmetically possible only at $150
   (83% friction cut) and $200 (67% cut) — both beyond the defensible
   liquidity ceiling.

**So the answer to the question as posed: $5/window requires simultaneously
(a) 1.5–2× more capital than this tape can defensibly price, and (b) a
two-thirds reduction in all-in friction.** Neither alone suffices.

## Translation to Gemini — the economics do not transfer

Gemini's measured structure, from this repository's own evidence:

| | |
|---|---|
| taker rate | **0.07** — functionally identical to PMUS's 0.0695 |
| maker rebate | 0.30 × the matched taker fee, **crypto rate from 2026-06-11** |
| rebate eligibility | execution price **$0.20–$0.80** only, capped at 5% of notional, paid daily |
| **verified maker compensation ever credited** | **$0.51**, 7 `AdminCredit` rows |

Three reasons the translation yields nothing, none of which is a modelling
choice:

1. **Cell C is 100% taker.** Gemini's only economic advantage is a maker
   rebate, and Cell C has no maker fill to earn it on. Its taker rate is
   *the same as PMUS's*, so the identical action stream would carry the
   identical friction — the ladder above would simply repeat.
2. **The instrument does not exist.** Gemini's lane is BTC**5m**; Cell C's
   action stream is 14 one-minute marks inside a 900-second window. Moving
   it to a 300-second window is a strategy change, not a translation, and
   this study does not make strategy changes.
3. **Settlement and position semantics are unresolved.** The documented open
   P0 is that Gemini represents a holding as one signed scalar per
   instrument. Cell C happens to be the *least* affected variant, since it
   never carries matched two-sided inventory — but the question is open, not
   answered, and nothing here assumes it away.

**Status: NOT TRANSLATABLE on present evidence.** No Gemini row is reported
with numbers, because producing one would require assuming maker fills,
rebate eligibility or settlement equivalence — all three of which this study
was told not to assume. Verified maker compensation stays **$0.0000** in
every PMUS row above and **$0.51 lifetime** on Gemini.

No other venue is reachable: the Kalshi lane's own authoritative ledger is
−$176/day and its deployment is removed.

## Evidence status

| | |
|---|---|
| PMUS ladder, $8–$100 | **replay**, authenticated tape, measured impact, defensible |
| PMUS ladder, $150–$200 | **replay, not defensible** — beyond top-of-book evidence |
| Gemini translation | **not attempted** — venue semantics unresolved, instrument mismatch, no maker leg |
| adverse selection | not separately identifiable in a 100%-taker stream; it is inside the gross, which is measured per size |

No strategy changes, no live orders, no $200 deployment implied. Frozen Cell
C remains the canonical bounded-residual benchmark: buy/extend, liquidate on
reversal, 8-share peak residual, +$10.03 gross, −$0.049/window.
