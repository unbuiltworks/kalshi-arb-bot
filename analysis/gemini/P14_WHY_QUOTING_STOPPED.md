# P14 — Why Probe A stopped quoting at 04:47:32 (analysis only)

The BTC15m authorized quote band is TTE **[60, 880]** — 820 s of quoting per
window. A quoted for **157 s (W01)** and **122 s (W02)**: 19% and 15% of the
band. This reconstructs why, from runner logic plus venue order rows.

Probe B is unaltered. No recommendation is made here.

## The charging rule, from source

`src/gemini_execution/ws_continuous.py`:

- `place()` — line 1716 `if self.budget_left() < 1: stop(...)`, line 1765
  `self.budget_consumed += 1`, **before the frame goes out**.
- `cancel()` — line 1823 same guard, line 1850 `self.budget_consumed += 1`,
  also before the frame.
- Plan-level gate — line 1514: `if plan["mutations"] > self.budget_left():`
  the **whole plan is refused**, not partially executed.
- Exempt: `CLEANUP_CANCEL` only (line 288) — "a SAFETY mutation… `consumed()`
  ignores it deliberately, and it carries its own hard bound instead"
  (`MAX_CLEANUP_MUTATIONS`).

**So yes: every cancel consumes one of the 12 authorized actions even without
a fill, and a reprice costs 2 — cancel + place.** Only the terminal withdrawal
is free.

## W01 `GEMI-BTC15M2609230445-UP` — chronological consumption

| time | action | leg | px | budget |
|---|---|---|---|---|
| 04:30:37.180 | PLACE | yes | 0.44 | 1/12 |
| 04:30:37.269 | PLACE | no | 0.47 | 2/12 |
| 04:31:01.885 | PLACE | no | 0.37 | 3/12 |
| 04:31:11.034 | CANCEL | yes | 0.44 | 4/12 |
| 04:31:14.070 | PLACE | yes | 0.57 | 5/12 |
| 04:31:42.828 | PLACE | no | 0.28 | 6/12 |
| 04:31:50.478 | CANCEL | yes | 0.57 | 7/12 |
| 04:31:51.892 | PLACE | yes | 0.60 | 8/12 |
| 04:32:33.015 | CANCEL | yes | 0.60 | 9/12 |
| 04:32:34.353 | PLACE | yes | 0.67 | 10/12 |
| 04:33:03.790 | PLACE | no | 0.19 | 11/12 |
| 04:33:12.808 | CANCEL | yes | 0.67 | *cleanup, exempt* |
| 04:33:14.542 | CANCEL | no | 0.19 | *cleanup, exempt* |

Stopped at **11/12** with one slot left. A two-leg reprice needs ≥2 mutations,
so the plan-level gate refused the whole plan. Lifetime 157 s; the contract ran
another 11.8 minutes.

## W02 `GEMI-BTC15M2609230500-UP` — the conclusive one

| time | action | leg | px | budget |
|---|---|---|---|---|
| 04:45:30.119 | PLACE | yes | 0.26 | 1/12 |
| 04:45:30.211 | PLACE | no | 0.57 | 2/12 |
| 04:46:08.828 | CANCEL | yes | 0.26 | 3/12 |
| 04:46:10.280 | PLACE | yes | 0.41 | 4/12 |
| 04:46:10.345 | PLACE | no | 0.50 | 5/12 |
| 04:46:44.600 | CANCEL | no | 0.50 | 6/12 |
| 04:46:44.822 | CANCEL | yes | 0.41 | 7/12 |
| 04:46:46.249 | PLACE | yes | 0.42 | 8/12 |
| 04:46:46.374 | PLACE | no | 0.50 | 9/12 |
| 04:47:11.771 | PLACE | no | 0.38 | 10/12 |
| 04:47:23.482 | CANCEL | yes | 0.42 | 11/12 |
| **04:47:24.872** | **PLACE** | **yes** | **0.54** | **12/12 — EXHAUSTED** |
| 04:47:32.460 | CANCEL | yes | 0.54 | *cleanup, exempt* |
| 04:47:32.504 | CANCEL | no | 0.38 | *cleanup, exempt* |

**The 12th and final authorized action was placing the YES 0.54 bid** — the
one order the tape shows was plausibly fillable, against the 277-contract sell
that printed at exactly 0.54 at 04:50:28. It was withdrawn 7.6 s later because
no slot remained to keep it alive.

## Cause, and every alternative ruled out

| candidate | verdict |
|---|---|
| **12-action budget exhaustion** | **CAUSE.** W02 hit 12/12 exactly; W01 stopped at 11/12 on the plan-level gate. |
| controller returned no desired action | **No** — W02's 12th action was a *fresh PLACE*; it still wanted to quote. |
| window/cohort accounting | **No** — W01 ended 04:33:14 while its contract ran to 04:45. `WINDOWS_EXHAUSTED` ended the *cohort* after W02, not the windows. |
| CAP | **No** — CAP4 binds at 4 fills; max depth reached was 3. |
| risk gate | **No** — committed 1.12 / 1.07 against a $4.00 cap; `max_working_orders` 2 respected throughout. |
| entry/window binding | **No** — both bound inside 840:880 as declared. |
| market eligibility / discovery | **No** — both contracts live until 04:45 / 05:00. |
| order replacement accounting | Contributing mechanism, not a separate cause: a reprice legitimately costs 2. |

**Caveat.** W02 is conclusive from arithmetic (12/12). W01's stop at 11/12 is
strongly indicated by the plan-gate semantics, but its recorded `stop_why`
died with the lost `cohort_run_A_*.json`.

## What the budget buys at the observed cadence

Pooled reprice interval, 12 observed gaps: **mean 40.0 s** per leg
(W01 yes 39.1 s, W01 no 48.8 s, W02 yes 38.3 s, W02 no 33.9 s).

To quote both legs across the full 820 s band at 40 s per refresh:

- per leg: 1 initial place + (820 / 40 = 20.5 refreshes × 2 actions) = **42**
- two legs = **≈ 84 actions per window**, terminal cleanup exempt

Against **12 authorized** — a shortfall of about **7×**. Twelve actions fund
2 initial places plus ~5 reprices, i.e. ~2.5 refreshes per leg ≈ 100 s of
additional quoting. Observed 122–157 s. The arithmetic and the tape agree.

*No recommendation is made about changing this.*

## Against L5's first-complement latency (BTC15m)

L5: p50 **26 s**, p90 **109 s**, P(opposite ≤180 s) = **0.965**.

Our quoting lifetimes — 157 s and 122 s — **do span past L5's p90**. So budget
exhaustion alone does not explain 0 complements in 2 windows: had this venue
carried L5-like flow, 85–90% of complements would have arrived while we were
still quoting. That points at flow, not budget, for the general case.

**But for the one complement the tape shows was actually available, budget was
binding.** The 0.54 sell printed at 04:50:28 — 298 s after W02's first action,
in the 81% of the band we had already left.

## Gemini BTC15m flow, our own prints excluded

96 consecutive contracts, 24 h to 2026-09-23 07:30Z. Our 5 identifiable prints
removed by (symbol, timestampms).

| metric | median | mean | min | max |
|---|---|---|---|---|
| prints / window | **4.0** | 5.01 | 0 | 22 |
| $ volume / window | **$116.69** | $213.13 | $0.00 | $1,048.73 |

5 of 96 windows (5%) had **zero** prints. Total 24 h volume: **$20,460**.

Probe A's own contracts, our prints excluded:

- `…0445-UP` (W01): **2** prints, **$50.58** — below median on both.
- `…0500-UP` (W02): **4** prints, **$404.56** — median count, ~3.5× median volume.

For scale: L5's median BTC15m window is **110 fills for L5 alone**.
