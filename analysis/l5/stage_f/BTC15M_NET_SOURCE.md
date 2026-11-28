# Where L5's +$16,436 BTC15m trading net comes from

**Date:** 2026-11-01
**Question:** decompose the canonical BTC15m trading-only net, separate ex-ante
from ex-post predictors, test prospective rules out of sample under realistic
PMUS execution, and determine whether any profitable subset survives without
L5's incentives or execution privileges.

**Answer: one condition survives — a bounded residual cap, which refuses
imbalance-INCREASING fills and never refuses a completion.** It is strictly
ex-ante, monotone in and out of sample, and trading-only. At our existing $12
envelope and existing 3-contract cap it is worth **+$0.0523/window raw**, or
**+$0.0176/window (≈+$1.69/day) after the replay-optimism haircut measured at
our own round count.** The binding constraint is **fills per window: the
condition needs ~9, we obtain 3.17, and it breaks even at 3.9.** The smallest
engine change is one constant: `ROUND_ALLOCATIONS_MAX` 4 → 8.

**Both ex-post sorters fail when converted into prospective rules**, and one of
them inverts violently. That is the main analytical result.

---

## 0. Baseline reproduced from source

`analysis/l5/redemption/audit.py` (`iter_activity`), cost basis `usdcSize` so
**fees are inside every figure**, per `fees/FEE_RECONSTRUCTION.md`:

| | |
|---|---|
| BTC15m TRADE fills | 693,916 |
| windows | 5,546 |
| gross acquisition (usdcSize) | $6,379,594 |
| SELL rows | **$0** — every acquisition is a BUY on one of the two outcomes |
| redemption | $6,396,030 |
| **net = redemption − acquisition** | **+$16,436** |

Exact match to canonical. The $27,550 of reconstructed fees is precisely the
gap to the doc's `price × size` acquisition of $6,352,044, confirming the
cost basis.

## 1. Decomposition (request item 1)

### The net is a thin residue of two large opposing flows

| | |
|---|---|
| 2,965 positive windows (53.5%) | **+$179,253** |
| 2,581 negative windows (46.5%) | **−$162,817** |
| net | +$16,436 = **9.2% of the gross positive flow** |
| top 25 windows (0.5%) | +$13,306 = **81% of the entire net** |
| top 50 windows (0.9%) | +$21,475 = **131%** of it |

### By component — matched earns, residual gives it back

| component | total |
|---|---|
| matched P&L `Σ m·(1 − eff_pair)` | **+$162,257** |
| residual P&L `Σ redeem − m − residual_cost` | **−$145,821** |
| sum | **+$16,436** (reconciles to $0.00) |

**Residual destroys 89.9% of matched profit.** The BTC15m book *is* a
matched-pair business; the residual is the leak. (This differs from the 5m
frontier in `FEE_RECONSTRUCTION.md`, where matched was ≈0 at every bounded cap —
that table is BTC5m+ETH5m, not BTC15m.)

### By month — the edge decayed to nothing before the tape ends

| month | windows | buy $ | matched | residual | net | **bp/$** |
|---|---|---|---|---|---|---|
| 2026-03 | 86 | 7,787 | +192 | −312 | −120 | −153.6 |
| 2026-04 | 548 | 312,978 | +12,965 | −9,921 | +3,045 | **+97.3** |
| 2026-05 | 919 | 712,327 | +17,366 | −15,796 | +1,569 | +22.0 |
| 2026-06 | 667 | 579,323 | +16,437 | −12,714 | +3,723 | +64.3 |
| 2026-07 | 1,170 | 1,001,691 | +11,242 | −4,632 | +6,610 | +66.0 |
| 2026-08 | 2,156 | 3,765,487 | +104,055 | −102,446 | +1,609 | **+4.3** |

**August carries 59% of all capital deployed and returns +4.3 bp.** Its residual
burden is **98.7%** of matched, against July's 41.6%. The most recent month of
L5's own BTC15m book is, on trading economics alone, approximately dead.

### By effective pair cost — monotone, and the single largest sorter

| band | windows | buy $ | matched | residual | net | **bp/$** |
|---|---|---|---|---|---|---|
| ≤ .90 | 497 | 364,410 | +48,384 | −27,507 | +20,878 | **+572.9** |
| .90–.96 | 1,653 | 1,828,801 | +110,765 | −60,845 | **+49,920** | +273.0 |
| .96–1.00 | 1,874 | 2,517,517 | +48,420 | −43,748 | +4,672 | +18.6 |
| **> 1.00** | 1,480 | 1,666,606 | −45,312 | −13,842 | **−59,154** | **−354.9** |
| one-sided | 42 | 2,259 | n/a | +120 | +120 | +533.2 |

The sub-par bands earn **+$75,470**; above-par completion gives back
**−$59,154**. Reproduces the canonical BTC15m band pattern (+247 / +171 / +3 /
−328 bp) on an independent construction.

### What changed over time: the cheap band dried up

Share of each month's capital by effective band:

| month | ≤.90 | .90–.96 | .96–1.00 | >1.00 | bp/$ |
|---|---|---|---|---|---|
| 2026-04 | **13.9%** | 35.5% | 28.3% | 22.3% | +102.4 |
| 2026-07 | 3.6% | 19.4% | 37.6% | **39.4%** | +65.6 |
| 2026-08 | 4.6% | 30.4% | **43.5%** | 21.5% | **+3.6** |

August is **not** above-par drift — that share *fell* to 21.5%, its lowest since
April. The collapse is the `≤.90` band shrinking **13.9% → 4.6%** while capital
piled into `.96–1.00` (28.3% → 43.5%), a band worth +18.6 bp. Cheap pairs became
unavailable and he deployed more capital for almost no margin.

### By time of entry and dispersion

Intra-window price range (`up_range + dn_range`) is the sharpest continuous
sorter after pair cost:

| dispersion quintile | windows | med range | med fills | buy $ | net | bp/$ |
|---|---|---|---|---|---|---|
| Q1 (quiet) | 1,100 | 0.68 | 63 | 744,277 | +9,460 | **+127.1** |
| Q2 | 1,100 | 0.93 | 94 | 1,106,836 | +10,127 | +91.5 |
| Q3 | 1,100 | 1.09 | 109 | 1,225,408 | +2,721 | +22.2 |
| Q4 | 1,100 | 1.29 | 123 | 1,427,101 | −5,751 | −40.3 |
| Q5 (volatile) | 1,104 | 1.58 | 159 | 1,873,714 | −241 | −1.3 |

Quiet windows pay; volatile windows do not. Time of entry does **not** sort:
median first-fill offset is 14–28s and median mid-fill 485–516s in *every* pair
band. **Averaging-down is not the mechanism** — intra-side price range is
essentially identical across bands (0.52–0.59), and wider ranges are *worse*.

## 2. What contributes and what destroys (request item 2)

**Contributes:** matched pairs at effective ≤0.96 (+273 to +573 bp); quiet
windows (+127 bp); bounded residual (§4).

**Destroys:** residual inventory (−$145,821, 89.9% of matched); completion above
par (−$59,154); high dispersion; and the August regime, where 59% of capital
earned +4.3 bp.

## 3. Ex-ante versus ex-post (request item 3) — the central result

| quantity | available before the order? |
|---|---|
| effective pair band (pooled over the whole window) | **NO** — known only at window end |
| intra-window dispersion | **NO** — the range includes fills not yet made |
| settlement outcome, residual/matched ratio | **NO** |
| clock / seconds into window | YES |
| running inventory imbalance | YES |
| running average cost per side | YES |
| **marginal pair cost of the order being placed** | **YES** |
| prior window's dispersion | YES |

Both ex-post sorters were converted into the strictest honest prospective form
and tested. **Both fail.**

### 3a. Dispersion → prior-window dispersion: no signal

`corr(prev_disp, disp) = 0.069`. Dispersion is **not** autocorrelated, so the
prior window carries almost no forecast. Quintiles on it are non-monotone
(−21.4, +57.0, +34.2, +55.1, −4.9 bp). Chronological OOS, threshold chosen on
the early half only:

| rule | IS bp/$ | **OOS bp/$** |
|---|---|---|
| trade all | +58.9 | **+6.8** |
| prev_disp ≤ 0.6 *(IS-best, +96.0)* | +96.0 | **−79.5** |

The IS-best threshold loses 86 bp against trading everything. Pure overfit.

### 3b. Marginal pair ceiling: **every ceiling destroys value**

At each completing fill, prospective pair = `own price` + `running average cost
of the unmatched opposite side` — fully ex-ante. Refuse above a ceiling:

| ceiling | IS bp/$ | **OOS bp/$** |
|---|---|---|
| none | +61.2 | **+11.8** |
| 1.00 *(IS-best)* | +97.3 | **−124.5** |
| 0.98 | +74.4 | −151.8 |
| 0.96 | +56.8 | −111.8 |
| 0.90 | +26.7 | −225.2 |

**Not one ceiling beats no ceiling out of sample.** The IS-best is −136.3 bp/$
worse. The reason is economic, not statistical: refusing a completion does not
return you to cash — it leaves a **stranded leg that settles directionally**, and
residual is adversely selected (−0.2315/unit over 384 windows, measured
previously). A ceiling converts a small certain loss into a large uncertain one.

**This is a genuine correction to how the pair-cost band table can be read.**
`FEE_RECONSTRUCTION.md` is internally careful — it labels the bands *effective*
and warns that quoted and effective must never be compared — but its line that
"the pair-cost gate is cohort-universal" invites use as a gate. **As a gate it
loses 136 bp/$ on L5's own tape.** The bands sort *outcomes*; they do not sort
*decisions*.

## 4. The one condition that survives (items 4, 5, 6)

**Refuse imbalance-INCREASING fills beyond a cap; never refuse a completion.**
The exact inverse intervention to §3b, and strictly ex-ante.

Chronological OOS, IS = 2026-03-19..07-21, OOS = 2026-07-21..08-31:

| cap (contracts) | IS net | IS bp/$ | OOS net | **OOS bp/$** |
|---|---|---|---|---|
| **3 (ours)** | +188 | +94.9 | +476 | **+111.9** |
| 6 | +689 | +94.8 | +1,245 | +91.4 |
| 10 *(IS-best)* | +1,835 | +112.7 | +2,349 | +85.9 |
| 25 | +7,510 | +88.6 | +7,346 | +80.4 |
| 50 | +12,182 | +86.5 | +12,844 | +60.0 |
| 100 | +14,990 | +82.9 | +13,545 | +42.2 |
| none | +13,053 | +61.2 | +5,022 | **+11.8** |

Monotone in both halves, and the tightest cap tested — **exactly our deployed
`ABS_RESIDUAL_CAP_CONTRACTS = 3`** — gives the best OOS rate, ~10× uncapped.

### At our actual envelope

OOS, 2,773 windows, cap 3, varying the cash envelope:

| envelope | net/window | capital/win | fills/win | bp/$ | /day @96 |
|---|---|---|---|---|---|
| unlimited | +0.1717 | 15.34 | 19.0 | +111.9 | +16.49 |
| $20 | +0.1470 | 12.69 | 16.9 | +115.8 | +14.11 |
| **$12 (ours)** | **+0.1110** | 9.38 | **14.2** | +118.3 | +10.65 |
| $6 | +0.0749 | 5.22 | 10.6 | +143.3 | +7.19 |

mean +0.1110, sd 0.9420, **t = 6.20** on n = 2,773; 1,403/2,773 windows positive.

**The condition needs ~14 fills/window, not L5's 161.** At cap 3 most of his
fills are refused anyway. This is materially new against the earlier fill-count
finding, which asked what it costs to reach 161 — the answer here is that 161 is
not the target.

### Degraded by our measured execution reality (item 5)

`q` = share of L5's fills we actually obtain. ADVERSE = we preferentially miss
the *completing* fills, which is what adverse selection means (measured:
`fill_rate` 0.8053, joint-fill 53.3%, `adverse_rate` 0.89, `swept_share` 0.98,
queue-ahead 73×–537× of our need).

| scenario | net/win | fills/win | bp/$ | t | /day @96 |
|---|---|---|---|---|---|
| q=1.00 (upper bound) | +0.1110 | 14.2 | +118.3 | +15.20 | +10.65 |
| q=0.80 neutral | +0.0847 | 12.6 | +97.8 | +11.94 | +8.13 |
| q=0.53 joint-fill | +0.0628 | 9.7 | +88.0 | +9.08 | +6.03 |
| q=0.53 **ADVERSE** | **+0.0428** | 9.1 | +62.0 | **+5.25** | +4.11 |
| q=0.30 ADVERSE | −0.0010 | **3.9** | −3.6 | −0.10 | −0.10 |
| q=0.20 ADVERSE | −0.0068 | 3.8 | −24.7 | −0.70 | −0.65 |
| **our realized** | **−0.0108** | **3.17** | — | — | — |

**The condition survives to q=0.53 adverse and dies at q=0.30 — and q=0.30 is
3.9 fills/window, essentially our realized 3.17.** We are sitting almost exactly
on the break-even fill rate. The edge is real and the gap is fills.

### Without incentives or execution privilege (item 6) — **yes**

- the +$16,436 is trading-only by construction: *"Incentives are kept out of
  every cell above and are never netted in"*;
- fees are inside every number (cost basis `usdcSize`, never `price × size`);
- no maker rebate, taker rebate, liquidity reward or taker crossing is assumed
  anywhere above — consistent with `$0.00` venue-credited incentives to us and
  `UNVERIFIED_NO_VENUE_CREDIT_RECORD`;
- prices are L5's own **executed fill** prices, not quoted mids.

## 5. Smallest engine modification, and what it is worth

The condition is already our policy (`ABS_RESIDUAL_CAP_CONTRACTS = 3`, $12
envelope, maker-only, `ENTRY_CUTOFF_S = 600`). What blocks it is **fill count**,
and one constant caps that:

`src/l5_two_sided_probe.py:171`
```
ROUND_ALLOCATIONS_MAX = 4      # -> 8
```

With `L5_TS_ROUND_ALLOCATIONS = 8`, at the **unchanged** $12 envelope:

| rounds | clip (contracts) | placements | exp fills | net/window | t | /day @96 |
|---|---|---|---|---|---|---|
| **4 (today)** | 2.00 | 8 | 3 | +0.0239 | +3.46 | +2.29 |
| 6 | 1.33 | 12 | 5 | +0.0301 | +4.27 | +2.89 |
| **8** | **1.00** | **16** | **6** | **+0.0523** | **+7.30** | **+5.02** |
| 10 | 0.80 | 20 | 8 | +0.0494 | +6.92 | +4.75 |
| 12 | 0.67 | 24 | 10 | +0.0538 | +7.46 | +5.16 |

**8 is the right stop**: it is the largest round count whose clip stays at or
above 1 contract. Polymarket's `orderMinSize: 5` does **not** apply here — that
is the native-Polymarket adapter (`src/l5_native_venue_live.py:73`); our PMUS
campaign placed and filled 2.04-contract clips, so the PMUS floor is ≤2.

### Honest expected value

The replay gives **+$0.0239/window at our own 4-round configuration**, against a
realized **−$0.0108**. That is **$0.0347/window of replay optimism** — it hands
us L5's fills at L5's prices. Applying the same haircut to the 8-round figure:

| | per window | per day @96 |
|---|---|---|
| raw replay, 8 rounds | +$0.0523 | +$5.02 |
| **minus measured replay optimism** | **+$0.0176** | **+$1.69** |

**+$1.69/day is the number to plan against.** It is 0.35% of the $480/day
target. It is also the first positive expected value this program has produced
from a strictly ex-ante, fee-inclusive, out-of-sample-validated rule.

Unchanged by this proposal: $12 envelope, $20 aggregate exposure, $1.50
worst-case deficit, $5 daily loss halt, 3-contract residual cap, 2 live orders,
maker-only, `ENTRY_CUTOFF_S = 600`, `COMPLETE_CUTOFF_S = 720`.
Changed: clip size falls $3.00 → $1.50 per round (1.00 contract), and
placements per window rise 8 → 16.

**This requires fresh owner authorization**, on two grounds: it is a code change
to a sizing constant, and doubling placements per window exceeds the
"existing order budgets unchanged" term of the last campaign. I have not made
the change, not deployed, and not armed anything.

## 6. Limitations

1. **The replay hands us L5's fills.** The `q` degradation is a model of our
   queue reality, not a measurement of it. The one measurement — our own 7
   windows — is what sets the $0.0347 haircut.
2. **Refusals do not propagate.** When the replay refuses a fill, L5's later
   fills are unchanged; he decided in his state, not the counterfactual's.
3. **Settlement winner is inferred** (redemption vs final inventory) for the
   counterfactuals, validated at **98.10%** against the independent
   `winning_outcome` field on the 527 checkable windows. The +$16,436 baseline
   does **not** use the inference — it is `redemption − acquisition` from source.
4. **The edge decayed.** August is +4.3 bp. The OOS half is Jul 21–Aug 31, so
   the surviving result is measured in the weakest regime on the tape — which
   makes it conservative, not optimistic.
5. Only four cap values and nine ceiling values were searched, with one
   chronological split. No alpha-spending correction is applied; per the power
   recheck in `RESIDUAL_MATCHED_RATIO.md`, detecting +$0.0176/window live would
   need ~38,000 windows (≈400 days) and is **not** live-measurable.

## 7. If this does not clear the bar: the missing observation

The condition survives but is worth +$1.69/day, so it does not reach the
objective on its own. The single observation that would change the answer is
**our own achievable fill count per window at a 1-contract clip** — equivalently,
our maker queue conversion at 16 placements rather than 8.

Everything else needed is in hand. That one number is not, and it **cannot be
obtained from shadow**: the shadow's fill model is `QUEUE_AWARE_THREE_SCENARIO`,
i.e. modelled, not observed. It also cannot be read off L5's tape, because his
queue position at 2.39× average fill size is not ours. It requires placing
1-contract maker quotes at 16 placements/window and counting fills — a bounded
live measurement, which is precisely what fresh authorization would be for.
