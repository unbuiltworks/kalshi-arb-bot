# Audit: the +$18,192 was an artifact, and L5's out-of-sample edge is 1 bp

**Item 1 verdict: not realized net. The reconciled figure is +$16,436.09, and
out of sample L5 earned +$355 on $3,288,826 of spend — one basis point.**

Reuses `analysis/l5/btc15m/decompose.py` and `residual_exit/forensics.py`.
New: `analysis/l5/btc15m/native_baseline.py` → `native_baseline.json`,
16 tests. No new service, no live order, no limit touched, shadow lock on.

---

## 1. The fill counts, and the artifact

**693,916 vs 63,569 — two different sources, and I mixed them.**

| source | reads | windows | fills | fills/window |
|---|---|---|---|---|
| `forensics.py` / `decompose.py` | full raw activity cache, `TRADE` + `REDEEM` rows | **5,546** | **693,916** | 125.1 |
| `l5_window_sequences.csv` | a normalized export (71,985 rows, 2,216 windows all assets) | **527** | **63,569** | 120.6 |

The per-window rates agree within 4%, so the CSV is representative on that
axis — but my "120.6 fills/window" and "median unmatched 127.98" came from the
**527-window subset** while "+$18,192" came from the **5,546-window full set**.
Presenting them as one dataset was wrong and is corrected here.

**And the +$18,192 is not cash.** The only figure that reconciles is the
cashflow identity, with cost taken from `usdcSize` (which already contains the
fee):

```
  redemption cash IN          +6,396,029.79
  acquisition cash OUT        -6,379,593.70
  = REALIZED NET CASH            +16,436.09     <- reconciles exactly
  matched_pnl + residual_pnl     +18,191.93     <- what I quoted
  OVERSTATEMENT                   +1,755.84
```

`forensics.py`'s own docstring warned of this: *"Matched P&L does not reconcile
and is not expected to."* I used the partition anyway. **Artifact confirmed.**

## 2. The BTC15m-native baseline

`analysis/l5/btc15m/native_baseline.py`, every split reconciling to the identity:

| split | n | net cash | bp/$ | fee paid | fee-bearing fills | **fee-free bp/$** |
|---|---|---|---|---|---|---|
| ALL | 5,546 | +$16,436 | +25.76 | $27,550 | 15.36% | +68.95 |
| TRAIN 03-19→08-10 | 3,882 | +$16,081 | **+52.03** | $22,307 | 24.73% | +124.20 |
| **OOS 08-10→08-31** | 1,664 | **+$355** | **+1.08** | $5,242 | 4.94% | **+17.02** |

**Half the spend is out of sample and returns one basis point.**

### The contamination is fixed at source

`src/l5_capacity_curve.py` now declares `INSTRUMENT_CLASS =
"SPORTS_ESPORTS_R5F"` and points at the native baseline. Its constants come
from `l5_r5f_cohort.json` — 13 `aec` esports, 2 `atc`, 1 `tsc` MLB, **zero
BTC15m**, with a row carrying `matched_pair_cost 1.110705` that the 0.96 BTC15m
ceiling forbids. The 84.52% breakeven built on them is withdrawn.

### And the breakeven framework itself fails validation

Using the native partition, the completion-rate model says L5 clears his own
breakeven in every split:

| split | completion | breakeven | margin | **realized** |
|---|---|---|---|---|
| TRAIN | 0.9079 | 0.7860 | **+0.1219** | **+52.03 bp** |
| OOS | 0.9243 | 0.8588 | **+0.0655** | **+1.08 bp** |

A **48× swing in realized cash against a 2× change in "margin"**, in the right
direction but nowhere near the right magnitude. The per-unit terms it is built
from come from a partition that does not sum to cash (§1). **Completion-rate
breakeven does not predict cash and should not be used as a decision metric** —
including in my own earlier reports.

## 3–4. Behaviour at identical states, with virtual limits

Virtual caps only; no real-money limit changed. Our gates against all 63,569
fills of the 527-window subset, in the state he was actually in:

| | fills | share |
|---|---|---|
| refused — unmatched cap (3) | 61,972 | **97.5%** |
| refused — running pair cost > 0.96 | 764 | 1.2% |
| permitted | 833 | **1.3%** |

Virtual-cap sweep: 3 → 1.7% of his fills inside it, 10 → 5.8%, 50 → 24.5%,
100 → 42.0%, 250 → 79.6%, 500 → 96.9%. His unmatched inventory is median
**127.98** contracts (p10 18.51, p90 332.17, max 1,180.33).

The four mechanisms, quantified: continuous accumulation **120.6 fills/window
vs our 4.52**; average cost — running pair VWAP declines **0.9968 → 0.9740**
across a window's own fill deciles; repricing — `EXTEND` is 66.2% of next
actions and `COMPLETED_FLAT` 1.2%; directional inventory — 84.3% of episodes
flip past zero and 14.5% carry to settlement.

## 5. Holdout, with settlement and the right fee regime

The holdout is the same chronological 08-10→08-31 split, untouched: **+1.08 bp
as observed, +17.02 bp fee-free**, residual settlement included in the cash
identity (a missing `REDEEM` row on a long-settled window is scored as the loss
it is).

**Which fee regime is ours?** Our three Oct 23 venue fills carry
`commission: 0.0000` — no fee *and* no credit. So we sit in L5's **fee-free**
regime, not a maker-credit regime, and `CLAUDE.md` forbids assuming an
uncredited rebate. **The reproducible out-of-sample target is therefore
≈ +17 bp per dollar deployed** — about **$0.034** per full cycle at the $20
envelope.

### Why +17 bp is not yet a business

| | fee-free net | top 1% of windows | top 10% |
|---|---|---|---|
| ALL | +$43,986 | 53% of it | 223% |
| **OOS** | **+$5,597** | **157%** | **664%** |

Out of sample the best 1% of windows exceeds the entire net, and the win rate
is 51.0%. The edge is a thin tail, not a rate.

## The smallest policy change that is actually supported

**None of the candidates clears the bar, and the reason is now a measurement
rather than an opinion.**

* **Raising the unmatched cap** is the only change that would move decision
  similarity materially (1.3% → 42% at a cap of 100). It is a risk control, it
  is not touched, and at his median it implies ~$45 of unmatched exposure per
  window against $1.7964 of campaign headroom.
* **Re-attempting the complement** inside the already-permitted 8 rounds needs
  no limit change and is the one free change available — but §2 shows the
  completion-rate framework that motivated it does not predict cash, so its
  expected benefit is unquantified rather than positive.
* **Reproducing L5's behaviour exactly** targets +17 bp out of sample,
  concentrated in 1% of windows, at a 51% win rate.

So the honest answer to *"the smallest policy change that improves OOS net
expectancy"* is: **we cannot yet name one, because we do not have a cash-
reconciled OOS expectancy for our own policy** — only for L5's. §2 built that
for him; the equivalent for us does not exist.

## The next test

**Run `decompose.py`'s cashflow identity against OUR OWN BTC15m fills.** It is
the same module, pointed at our activity instead of L5's: cost from `usdcSize`,
a missing `REDEEM` scored as a loss, net cash = redemption − acquisition. Our
three Oct 23 fills plus the settled history give a small but *cash-reconciled*
`bp_per_dollar` for our policy — the first number on this program that would be
comparable to L5's +1.08 and +17.02 on the same basis.

Everything before that comparison exists is a partition, not a cashflow, and
this audit is the demonstration of why that distinction decides the answer.

## What this does not claim

* It does not show L5's behaviour is unprofitable — ALL is +25.76 bp and
  fee-free TRAIN is +124 bp.
* It does not show it is reproducible — his OOS is 1 bp, and his fee-free
  advantage is a venue fact about his account, not a behaviour we can adopt.
* It does not price raising the unmatched cap. Nobody has replayed an L5-scale
  inventory path at our executable prices, and no limit moved here.
