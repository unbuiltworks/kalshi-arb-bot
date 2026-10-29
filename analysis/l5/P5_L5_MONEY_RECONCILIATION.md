# Where L5's money actually came from — and what the corrected trade accounting changes

**Status: RECONCILIATION ONLY. No live orders, nothing armed.**
Sources: `analysis/l5/l5_ledger.json` (harvested from L5's own public
Polymarket activity: 742,964 TRADE, 15,243 REDEEM, 200 income rows) and
`analysis/l5/l5_windows_summary.csv` (2,216 reconstructed up/down windows).

---

## 0. The scope correction that has to come first

**Every P&L figure this programme has quoted for L5 came from a 13% slice of
his account.**

| | his full public ledger | our analysis corpus | coverage |
|---|---|---|---|
| markets | 16,501 | 2,216 | **13.4%** |
| trade events | 742,964 | 71,984 | **9.7%** |

The `−$1,051` in P4, and the `−$1,115` BTC15m figure that reframed the whole
programme, are properties of that slice. They are **not** his account result
and must stop being quoted as if they were. The lane ranking still holds
*within the sample*, and the sample is heavily concentrated in four August
days, but nothing about his account-level profitability follows from it.

---

## 1. The full-account reconciliation

Cash basis, from actual USDC movements in his public activity:

```
  bought                    -7,423,850.29
  sold                             +2.18      (he is buy-only; 2 dollars of sells)
  redeemed                  +7,437,534.55
  ------------------------------------------
  NET TRADING CASH                +13,686.44

  MAKER_REBATE                    +33,929.41   109 credits
  TAKER_REBATE                     +7,322.73    72 credits
  REWARD                          +26,956.26    19 credits
  ------------------------------------------
  INCOME TOTAL                    +68,208.40

  GRAND TOTAL CASH                +81,894.84
```

```
incentives as a share of total cash      83.3%
trading as a share                       16.7%
trading return on turnover               0.184%
incentive return on turnover             0.919%   -- 5x the trading return
```

**The prior finding that incentives dominate L5's lifetime economics is
REVALIDATED against the corrected trade accounting, and it survives.** It is
not an artifact of the old (wrong) trade numbers: it comes from a separate
source — his credited income rows — and those are unchanged by anything we
corrected.

What *did* change: trading is **positive** at the account level (+$13,686),
not negative. The negative figures we have been quoting are the 13% slice.

### Cadence and era structure (unchanged from the earlier study, re-checked)

All three streams pay once per day at 00:00 UTC — daily pool distributions,
not per-fill commissions.

| era | days | MAKER | TAKER | REWARD | total | **$/day** |
|---|---|---|---|---|---|---|
| A — MAKER only (Apr 21 – Jun 19) | 52 | 3,907 | 0 | 0 | 3,907 | 65 |
| B — +TAKER (Jun 20 – Aug 7) | 37 | 4,687 | 3,574 | 0 | 8,261 | 169 |
| C — +REWARD (Aug 8 – Aug 30) | 21 | 25,335 | 3,749 | 26,956 | **56,040** | **2,437** |

**82% of everything he was ever paid arrived in the last 21 days**, and the
MAKER stream itself stepped 16x ($75/day to $1,206/day) at the same boundary.
Recent daily credits run $3,000-$4,200.

### What cannot be observed publicly

- **Fees.** No fee row exists in the activity feed. Polymarket's published
  schedule for these markets has no explicit taker fee, so fees are assumed
  absent rather than measured. If they exist, trading is worse than stated.
- **Unredeemed contracts, and this one is material.** 7,890,189 contracts sit
  unredeemed. Against 7,437,535 redeemed that implies a 48.5% contract win
  rate, which is what a book bought near $0.50 should show — consistent with
  the unredeemed being losers. But it is an *inference*, and the figure is not
  robust to it:

```
  unclaimed winners   trading cash    incentive share of total
       0.0%              $13,686              83.3%
       0.1%              $21,577              76.0%
       0.5%              $53,137              56.2%
       1.0%              $92,588              42.4%
```

  Half a percent of unredeemed contracts being unclaimed winners would nearly
  quadruple the trading line. **The trading number is the weakest figure in
  this reconciliation; the incentive number is the strongest.**
- **Per-market reconciliation disagrees in sign.** On the 1,459 markets the
  ledger could match, `our_cash_plus_marks` is −$34,775 against their
  `cashPnl` of −$33,639 — both negative — while account-level cash is
  +$13,686. The 15,042 unmatched markets carry the difference. Stated rather
  than resolved.

---

## 2. What distinguishes a profitable window (P1)

The four August days differ far less than the windows inside them. Day level:

| day | windows | trades | matched $ | residual $ | net $ | r/m cost | median pair cost | median residual unit |
|---|---|---|---|---|---|---|---|---|
| 08-28 | 401 | 12,325 | +3,382 | −5,045 | **−1,662** | 17.2% | 0.9736 | 0.5950 |
| 08-29 | 567 | 20,026 | +5,471 | −4,917 | **+554** | 15.0% | 0.9692 | 0.5673 |
| 08-30 | 558 | 19,460 | +4,075 | −6,317 | **−2,242** | 16.9% | 0.9808 | 0.5878 |
| 08-31 | 474 | 14,904 | +4,959 | −3,061 | **+1,898** | 14.7% | 0.9662 | 0.5808 |

Aug 31 versus Aug 30 differ by 1.5 points of pair cost and 2.2 points of r/m
ratio. **The day is not the unit of explanation.** The window is:

| pooled across the four days | n | r/m cost | median pair cost | median residual unit | median trades |
|---|---|---|---|---|---|
| **PROFITABLE windows** | 1,141 | 18.2% | **0.9353** | **0.7088** | 5 |
| **LOSING windows** | 859 | 13.4% | **1.0343** | **0.3240** | 6 |

Two separations, both large, both in the direction every earlier study
predicted:

1. **Losing windows bought the matched pair for more than $1.** Median pair
   cost 1.0343 against 0.9353. A pair above $1 is a guaranteed loss on the
   matched leg whatever the outcome — the one part of this strategy that is
   supposed to be safe.
2. **Losing windows carried cheap residual** (0.324, deep in the destructive
   band) while profitable windows carried expensive residual (0.709, inside
   the +23% band from P2-E).

Across the whole corpus, **38.8% of windows (625) bought the pair above $1**,
costing **−$8,598** on $136,494 of matched cost, against +$27,511 on the 61.2%
bought below $1.

Neither separator requires knowing the outcome. Pair cost and residual unit
cost are both observable **while the position is being built**, which makes
them candidate gates rather than post-hoc descriptions. Note also that trade
count does *not* separate them (5 vs 6): **more participation is not what
distinguishes a profitable window.**

---

## 3. BTC5m versus BTC15m residual structure (P2)

| lane | windows | r/m cost | median pair cost | median residual unit | unit <$0.35 | unit >$0.65 | residual qty/win | matched qty/win |
|---|---|---|---|---|---|---|---|---|
| BTC 15m | 527 | **6.7%** | 0.9634 | 0.3800 | **46.1%** | 19.5% | 197.7 | 1,055.1 |
| BTC 5m | 957 | 57.1% | 0.9763 | 0.5782 | 37.5% | **43.5%** | 96.4 | 86.4 |
| ETH 5m | 703 | 172.6% | 1.0263 | 0.6463 | 22.6% | 49.2% | 39.4 | 12.6 |
| ETH 15m | 29 | 131.9% | 1.2334 | 0.7641 | 10.3% | 65.5% | 26.4 | 13.8 |

**They fail differently, and that is the useful part.**

- **BTC15m has by far the best matched:residual RATIO** (6.7% — twelve times
  better than BTC5m) but the **worst residual price distribution**: 46.1% of
  its residual sits below $0.35, only 19.5% above $0.65.
- **BTC5m has the best residual price distribution of the two** (43.5% above
  $0.65) but carries **8.5x more residual per unit of matched inventory**.

So BTC5m does structurally avoid part of BTC15m's cheap-residual tail — the
answer to the question as posed is **yes, partially** — but it pays for it
with a far worse ratio. Neither dominates, and that is precisely the case
where selecting between durations could beat committing to one.

**The sample caveat stands:** BTC5m's +$58 over 957 windows establishes
nothing about expectancy, and this table is structure, not P&L.

---

## 4. The central question

> A. positive trading expectancy, incentives amplify it
> B. trading approximately breakeven, incentives create the profit
> C. negative trading expectancy accepted because incentives more than compensate
> D. public data insufficient to distinguish

**Best fit: B, with D applying to the precise trading figure.**

- Trading is **+$13,686 on $7.42M of turnover — +0.18%**. That is positive but
  it is not a margin that survives its own measurement uncertainty: a 0.5%
  unclaimed-winner rate among unredeemed contracts would quadruple it, and
  fees are unobserved. "Approximately breakeven" describes it honestly;
  "positive expectancy" over-claims it.
- Incentives are **+$68,208, 5x the trading return on the same turnover**, and
  they are measured from credited rows rather than inferred. **The profit is
  the incentives.**
- **C is not supported**: trading is not negative at the account level, and
  the negative figures we were quoting come from a 13% slice.
- **A is not supported**: 0.18% of turnover, with the uncertainty above, is
  not a demonstrated positive edge.

**What this means for the programme.** The mechanism we have spent this work
replicating is, on his own numbers, a way to *hold a large two-sided maker
book without losing money on it* — and the money is then paid by the venue for
having held it. That is a coherent and rational strategy. It is also a
completely different target from "reproduce his fills because his fills are
profitable", which the evidence does not support.

Two consequences follow directly and both are already in flight:

1. **The minimum trading expectancy the duration selector must clear is
   "approximately zero, after fees"** — not "positive enough to fund the
   operation". What funds the operation is the rebate line.
2. **Gemini's credited rebate rate is therefore the gating number**, not a
   secondary line item. Tonight's 21:15Z reconciliation determines whether a
   Gemini version of this strategy has any economic basis at all.

---

## 5. Provenance

| figure | source | strength |
|---|---|---|
| income $68,208.40 | 200 credited income rows, L5's own activity | **strong** — credited amounts, not modelled |
| era structure / cadence | timestamps on those rows | strong |
| net trading cash +$13,686 | buy/sell/redeem USDC movements | **weak** — sensitive to unredeemed contracts |
| pair cost / residual unit splits | 2,216-window reconstruction, reconciles to his `pnl_derived_resolution` lane by lane | strong **within the 13% slice** |
| lane ranking | same | strong within the slice, **not extrapolable** |
| fees | not present in the feed | **unobserved** |
