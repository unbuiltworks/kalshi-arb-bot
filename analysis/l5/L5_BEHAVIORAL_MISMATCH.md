# L5's actual policy vs ours — and a correction to my own breakeven

**You were right. My "negative expectancy" conclusion was computed from a
16-market cohort containing ZERO BTC15m markets. It describes an esports/sports
lane, not this strategy, and it does not establish anything about L5.**

**L5's BTC15m book is profitable and clears his own breakeven by 8.69 points.
The largest behavioral mismatch is not pair pricing — it is unmatched inventory:
our cap is 3 contracts, his median is 128.**

Reuses `residual_exit/forensics.py`, `l5_window_sequences.csv`,
`l5_windows_summary.csv`, the heartbeat census and the live shadow. Nothing
rebuilt, no new service, shadow lock untouched.

---

## 0. The correction

`src/l5_capacity_curve.py` and my `PROFITABILITY_DIAGNOSIS_20261023.md` used
`PAIR_PROFIT_PER_MATCHED_PAIR = 0.057230` and
`RESIDUAL_LOSS_PER_RESIDUAL_UNIT = -0.331449` from `src/l5_breakeven.py`, whose
cohort is `analysis/l5/l5_r5f_cohort.json`:

| slug prefix | markets | what it is |
|---|---|---|
| `aec` | 13 | esports |
| `atc` | 2 | sports |
| `tsc` | 1 | MLB baseball |
| **BTC15m** | **0** | — |

One of those rows carries `matched_pair_cost: 1.110705` — a pair cost of 1.11,
impossible under our 0.96 BTC15m ceiling. **The 84.52% breakeven I reported is a
sports-lane number applied to a crypto strategy.** It is withdrawn.

**And the replacement is not trustworthy either.** The BTC15m-native census (269
windows, our venue) gives residual loss −0.150160/unit but pair profit
+0.257707/pair — and a pair capped at 0.96 can earn at most 0.04–0.05, so that
term is 5–6× its arithmetic maximum and is mixing in something else. So:

> **There is no reconciled BTC15m-native expectancy in this repo.** Both figures
> I have used are unfit. What the ceiling alone bounds: with pair profit at its
> maximum 0.05, breakeven is 75.0% at a −0.150 residual and 86.9% at −0.331.

## 1. L5's own economics — profitable, and not by a hair

5,546 BTC15m windows, 693,916 fills, `residual_exit/forensics.py`:

| | L5 | ours (sports cohort, withdrawn) |
|---|---|---|
| matched P&L | +$162,256.93 | |
| residual P&L | −$144,065.00 | |
| **net** | **+$18,191.93** | |
| pair profit / matched pair | **+0.026520** | +0.057230 |
| residual loss / residual unit | **−0.129033** | −0.331449 |
| units paired `2m/(2m+r)` | **91.64%** | 83.42% |
| his breakeven | **82.95%** | 85.28% |
| margin | **+8.69 pts → PROFITABLE** | −1.86 pts |

**He earns half our pair profit and loses a third of our residual.** Our pair is
*better* than his — the 0.96 ceiling forces cheap pairs. His residual is 2.57×
better, and that is the whole difference.

Decomposition — **either fix alone flips us positive**:

| | EV/unit |
|---|---|
| his economics, our completion | **+0.000728** |
| our economics, his completion | **+0.024732** |
| his economics, his completion | +0.013514 |
| our economics, our completion | −0.007215 |

## 2. The four hypotheses — all four confirmed

| hypothesis | evidence | verdict |
|---|---|---|
| continuous inventory management | **120.6 fills/window** (63,569 over 527 BTC15m windows) vs our **4.52** — 26.7× | **YES** |
| asymmetric accumulation | `EXTEND` is **66.2%** of next actions; `COMPLETED_FLAT` **1.2%**; exactly 1 of 5,546 windows ended flat | **YES** |
| dynamic repricing | running pair VWAP declines **0.9968 → 0.9740** across a window's own fill deciles | **YES** |
| directional exposure | **84.3%** of episodes end by FLIPPING past zero, **14.5%** carried to settlement | **YES** |

He is not completing isolated pairs. He runs a permanently two-sided book whose
average cost improves as he adds, and the unpaired tail is a by-product.

## 3. Our engine at each of his decisions

For every one of his 63,569 BTC15m fills, applying our production gates to the
state he was actually in:

| what our engine would do | fills | share |
|---|---|---|
| **REFUSED — unmatched cap (3 contracts)** | **61,972** | **97.5%** |
| REFUSED — running pair cost > 0.96 | 764 | 1.2% |
| permitted (the window's opening fill) | 527 | 0.8% |
| permitted (a later fill) | 306 | 0.5% |
| **total permitted** | **833** | **1.3%** |

**Our engine would decline 98.7% of L5's trades, and 97.5% of that is one
control: the unmatched-inventory cap.** The 0.96 pair ceiling — the thing I have
spent days analysing — accounts for 1.2%.

### Why they differ

His unmatched inventory at each fill:

| | contracts |
|---|---|
| p10 | 18.51 |
| p25 | 51.14 |
| **median** | **127.98** |
| p75 | 226.82 |
| p90 | 332.17 |
| max | 1,180.33 |

**Only 1.7% of his fills occur at ≤3 contracts unmatched.** Our cap is 3. He
spends essentially his entire operating life in a state our risk envelope
forbids by construction.

Permit rate against the cap: 3 → 1.7%, 10 → 5.8%, 50 → 24.5%, 100 → 42.0%,
250 → 79.6%, 500 → 96.9%.

And his running pair cost: median **0.9870**, p75 1.0119, p90 1.0450 — only
**23.7%** at or under our 0.96.

## 4. The realized consequence — his residual is cheaper, not luckier

527 BTC15m windows with a resolved residual:

| | L5 | ours |
|---|---|---|
| residual cost basis / unit | **0.3478** | 0.5087 |
| residual win rate | **0.3245** | 0.2360 |

He pays **1.46× less** per residual unit *and* wins more often, because the
basis is averaged down over ~121 fills. Ours is a single leg at its entry price
with nothing to average against. That is the mechanism behind the 2.57×
residual-loss gap in §1 — it is a direct consequence of §3's inventory cap.

## 5. Chronological holdout — the mismatch replicates

TRAIN 368 windows (03-19→08-30), OOS 159 (08-30→08-31):

| | fills/win | median unmatched | ≤3 cap | residual basis | residual win |
|---|---|---|---|---|---|
| TRAIN | 94.2 | 108.74 | 2.2% | 0.3664 | 0.3478 |
| **OOS** | **181.7** | **148.85** | **1.2%** | **0.3287** | 0.2704 |
| ours | 4.52 | ≤3 by rule | 100% | 0.5087 | 0.2360 |

Every quantity moves *further* from us out of sample. Nothing here depends on a
fitted parameter.

## 6. Observable vs inferred

**Observable:** every fill's side, price, size, timestamp, running inventory,
running pair VWAP, matched/residual split, and settlement. All of §1–§5.

**Not observable, and not claimed:** L5's resting or cancelled orders, so
*"he waited"* and *"he quoted and was not hit"* are the same record; his queue
position; maker/taker per fill; his rebate credits; and any price at an instant
he did not trade. BTC distance from strike is **not computable** for this corpus
— spot coverage is 2026-08-31/09-01 against a corpus starting 03-19.

## 7. Largest behavioral mismatch, and the smallest changes

> **THE MISMATCH: unmatched inventory. Cap 3 contracts against a median of
> 127.98 — a 43× gap that refuses 97.5% of his trades.** Everything else,
> including the 0.96 ceiling, is second-order by two orders of magnitude.

Smallest concrete changes, in order of measured effect:

1. **Raise `max_unmatched_contracts`.** This is a **RISK CONTROL**, it is the
   binding one, and I am not touching it. At his median it implies ~128
   contracts × ~0.35 basis ≈ **$45 of unmatched exposure per window**, against
   a $20 authorized envelope and $1.7964 of campaign headroom. It needs
   explicit authorization naming a number, and the capital to back it.
2. **Allow the complement to be re-attempted** within the already-permitted
   `accumulation_max_rounds` (8). The campaign placed **1** per window. This
   needs no limit change — the budget already exists and goes unused.
3. **Let the pair ceiling apply to the BOOK's running VWAP, not to each new
   pair in isolation.** His median running pair cost is 0.9870 while his VWAP
   improves to 0.9740; our per-pair 0.96 test rejects the path that gets there.
   This is a semantic change to an existing gate, not a loosening of its value.

## 8. The next test that resolves this

**Compute reconciled BTC15m-native unit economics by pointing the existing
`src/l5_breakeven.py` at a BTC15m cohort instead of `l5_r5f_cohort.json`.**

It is the smallest, zero-risk, highest-value next step: one existing module, one
cohort argument, no new infrastructure, no capital, no live order. Until it runs
we do not know our own breakeven on this instrument — §0 shows both available
figures are unfit — and every expectancy claim about BTC15m, including the ones
I made, is unsupported.

The second test, once that number exists: replay the L5-scale inventory path in
the **read-only shadow** at our fee schedule and our executable prices, to price
what change 1 would actually buy before any limit is proposed. The shadow
already sweeps thresholds 0.96–0.99 at depths 0 and 2 and can carry the
inventory path; it needs sample, not new code.

## 9. What this does not establish

* It does not show our engine would be profitable at L5's inventory scale. It
  shows our engine never reaches that state, and that the state is where his
  economics come from.
* It does not price change 1. Nobody has replayed an L5-scale inventory path at
  PMUS fees and executable prices; §8 is how.
* L5's fill economics, rebates and queue position do not transfer, and no figure
  of his is used here as a prediction for us — only as the target to explain.
