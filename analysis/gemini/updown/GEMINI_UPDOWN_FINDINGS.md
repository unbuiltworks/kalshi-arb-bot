# Gemini BTC UP/DOWN: executable expectancy, BTC5m vs BTC15m

**Lane: GEMINI ONLY.** No PMUS window, threshold, fee, settlement assumption or
validation sample enters this document. The two durations are never pooled.

**Historical / shadow research. No live orders. Nothing here is realized profit.**
Early-exit economics remain **SEALED** — no future bid path, MFE or exit timing was
inspected; every number below is hold-to-settlement.

---

## Decision table

| Mechanism | BTC5m result | BTC15m result | $/day | Drawdown | Capacity | Evidence quality | Next step |
|---|---|---|---|---|---|---|---|
| Underdog taker, hold to settlement | **5.04%** win vs **22.80%** breakeven, −17.75pp, CI [2.53, 8.25] entirely below breakeven | +11.80pp point estimate but CI [21.74, 65.26] **straddles** breakeven 31.99 | 5m: **−$1,504** measured; 15m: untestable (no depth) | 5m: **−$674**, 115 consecutive losses | 5m: depth median 178 but p10 = 5; 15m: unknown | 5m **strong** (128 windows); 15m **weak** (15 windows) | 5m **KILL** · 15m **COLLECT MORE** |
| Favorite taker (unconditional) | −13 to −35pp in every bucket, n=118 | +14.09pp at 50–60c only; negative 60c+ | not computed (negative) | — | — | 5m weak-n, 15m weak-n | both **KILL** as a standalone taker bet |
| Favorite taker, **state-conditioned** | −4.64pp pooled, CI low − breakeven = **−13.33** → not established | not testable (no reference price recorded) | — | — | only 138 of 1463 observations executable | 5m **moderate** (66 windows) | 5m **KILL** · 15m **COLLECT MORE** |
| Maker capture of the spread | **0.6%** fill rate joining the touch, **1.6%** improving a tick, adverse move **+0.12** on fill | not evidenced | — | — | — | 960 cycles, queue-respecting | **KILL** for 5m; 15m needs its own census |
| L5 sequential inventory | **4 prints / $10.80 notional** per window, book active 1.1% of life | **9 prints / $179.68** per window | — | — | — | ~650 windows each | 5m **KILL** · 15m **FREEZE FOR HOLDOUT** |

**Per-duration recommendation — exactly one each:**

- **BTC5m → KILL.** The two executable asks sum to a median **1.330** with a median
  touch spread of **0.33**. Directional taker expectancy is arithmetically excluded
  before any view is taken, the maker cannot capture that spread (0.6–1.6% fill,
  adversely selected), and there is no flow to work inventory against.
- **BTC15m → COLLECT MORE.** The structural numbers are an order of magnitude
  better (ask sum **1.080**, spread **0.08**, 17× the notional), and the underdog
  point estimate is positive — but on **15 windows** with the confidence interval
  straddling breakeven, and with **no recorded depth at all**, so neither the $5
  sizing test nor any capacity claim can be made. This is a data problem, not a
  negative result.

**No live probe.**

---

## 1. Current state, established before building anything

Branch `research/gemini-btc5m-information-edge` at `7fafa27c`. All existing Gemini
work was preserved; nothing was rebuilt. The collectors and replay/accounting that
already existed and were **reused rather than rewritten**: `metadata.outcome_book`
(the two-sided price surface and its complement-identity check),
`rebate_program` (the published fee schedule), `window_volume` (flow census),
`overlay_study` (queue-respecting maker fills), `ie_tte_ladder` (the checkpointed
ladder tape), `ie_usable_windows` (per-window usability and reference feed),
`p7_bulk_tape` (settled contracts with strike, settlement, prints), and the
BTC15m live-cohort `spread_placement` recorder.

**Exact coverage of the historical Gemini tape:**

| Tape | Duration | Windows | What it carries |
|---|---|---|---|
| `ie_tte_ladder.json` | BTC5m | **133** | 11 checkpoints × executable UP ask + size, UP bid + size, proxy reference, strike, settlement value, resolution |
| cohort `spread_placement` | BTC15m | **15** | touch bid/ask + tte per cycle — **no level sizes** |
| `p7_bulk_tape.json` | 5m / 15m / 1h | 672 / 655 / 1854 | strike, settlement, resolution, full print tape — **no book** |
| `info_edge/btc{5,15}m/capacity_tape` | both | 19 / 7 | per-size VWAP, but only 6 / 3 windows reach inside their own tradeable life |
| `continuous_v1.jsonl` | BTC5m | 1 | book + depth + tte |

Two things that cannot be tested from this corpus, stated rather than fabricated:

1. **BTC5m checkpoints 240s and 180s were never collected.** The recorded grid is
   1/2/5/10/15/20/30/45/60/90/120s, so 6 of the 8 requested checkpoints are
   available and two are not. They are reported as NOT COLLECTED, never
   interpolated.
2. **BTC15m has no recorded depth.** The `$5` whole-contract test requires
   displayed depth for the entire quantity; running it against an assumed size
   would fabricate the fill, so it returns `testable: false` and moves to forward
   shadow collection.

---

## 2. Settlement semantics, established first

From Gemini's own OpenAPI contract (`developer.gemini.com/specs/openapi/
prediction-markets.yaml`, sha256 `53caea56…69d165`, preserved in-repo) and from our
own recorded venue objects:

| Question | Answer | Source |
|---|---|---|
| Market definition | one contract per window, `GEMI-BTC{05,15}M<expiry>-UP`, outcomes yes/no | venue symbols |
| Strike construction | `StrikeType.reference` — *"Crypto Up/Down reference strike price captured at `availableAt` time"*; **null until `availableAt`** (= expiry − 300s for 5m) | spec `Strike`/`StrikeType` |
| UP/DOWN condition | settlement value vs that captured reference | spec + 1,322 resolved `reference`-type contracts |
| Reference index | **`GRR-KAIKO_RFR_BTCUSD_60S`** (agency Kaiko) through the **2026-10-02T17:45Z** expiry; **`LS-LRRR-XBTUSD`** from **17:50Z** onward | `sourceDetails` in our own recorded contracts; the switch is visible inside one day in `ie_usable_windows.json` |
| Instantaneous or averaged? | **Averaged.** Settlement values carry long non-decimal fractional tails (e.g. `75917.2651351351`), impossible for a quoted price. Empirically, over 133 resolved windows, `\|settlement − proxy mean of last 60s\|` median **$0.99** beats `\|settlement − instantaneous at tte=1s\|` median **$3.09** and the 30s mean's **$1.94** | our own tape |
| Boundary/tie | **0 exact ties in 1,322** resolved `reference`-type contracts, so tie behaviour is undocumented AND unobserved | measurement |
| Fees | published **taker 7%**, **maker 1.75%** of `q·p·(1−p)` | gemini.com/fees/predictions, recorded 2026-09-19 |
| Min quantity / tick | per-instrument `quantityMinimum`/`quantityIncrement`, `priceMinimum`/`priceIncrement`; the spec says explicitly *"Do not assume a fixed quantity or price grid across instruments"* | spec |
| Executable book | `prices.buy.{yes,no}` / `prices.sell.{yes,no}`; a resting BUY joins that outcome's SELL side | spec + `metadata.outcome_book` |

**The 60-second window is a measurement here, not an imported assumption.** It was
established from Gemini's own index identifier (whose name ends `_60S`) and from a
fit against Gemini's own resolved settlements. The provider is **Kaiko, then
LS-LRRR** — a different vendor, and the feed changed mid-study, which is itself a
finding: calibration from before 2026-10-02T17:45Z does not transfer unchanged.

**Fee reconciliation gap, stated plainly.** The published schedule is sourced but is
**not** reconciled against our own authenticated fills: every q=1 maker fill returns
`fee_amount "0"` (consistent with rounding, since 0.0175·p·(1−p) < $0.005), but
several larger rows match neither rate (`fee 0.03` at q=10, p=0.53 against a 0.1744
taker / 0.0436 maker prediction). Every cell below therefore reports the **zero-fee
breakeven alongside the taker-fee breakeven**. The gap does not change any verdict:
the fee moves breakeven by ~1.2 points and the effects measured are 12–18 points.

---

## 3. Execution discipline

Executable prices only. On this venue one instrument carries both outcomes, so:

```
buy UP   (outcome yes) aggressively -> pays the YES ASK
buy DOWN (outcome no)  aggressively -> pays 1 - the YES BID, consuming the bid ladder
```

The second is not a convenience: buying NO and selling YES are the same trade. The
tape satisfies `down_price == 1 − bid` in **669 of 669** rows where both are present,
with **zero** crossed books — which makes that reading a measurement.

Not used as execution: midpoint, UI probability, last trade, touch beyond the
recorded size, any queue position, any future quote, any post-settlement reference
as an entry feature. **A side with no recorded ask is NOT EXECUTABLE** and is counted
as such — 410 of 1463 BTC5m observations have neither side executable, and only
**118** have both.

**The statistical unit is the WINDOW.** Eleven checkpoints of one window are eleven
views of one settlement, so every interval comes from a bootstrap that resamples
windows, not ticks.

---

## 4. The decisive structural finding

| | BTC5m | BTC15m |
|---|---|---|
| `ask_UP + ask_DOWN`, median | **1.330** | **1.080** |
| fraction above 1.00 | **100%** | **100%** |
| min / max | 1.01 / 1.92 | 1.01 / 1.39 |
| UP touch spread, median (p10–p90) | **0.330** (0.21–0.44) | **0.080** (0.03–0.09) |
| two-sided observations | 118 | 537 |

A taker pays half of that spread on entry. When the two executable asks sum to 1.33,
**no directional taker bet on either side can be positive on average** — the 33-cent
excess is charged before any view is expressed. This single number explains both the
underdog result and the favorite result on BTC5m, and it is why the honest answer is
"the mechanism isn't mispricing, it's spread."

BTC15m's 8-cent book is a different market. That is the whole reason its verdict is
COLLECT MORE rather than KILL.

---

## 5. Underdog matrix (hold to settlement)

### BTC5m — 133 windows, 813 underdog observations

Pooled: avg executable ask **0.2161**, actual win **5.04%**, breakeven **21.61%**
(zero fees) / **22.80%** (taker), edge **−17.75pp**, window-bootstrap CI
**[2.53, 8.25]** — entirely below breakeven. **−$0.867 per dollar risked.**

By checkpoint (all buckets):

| tte | windows | obs | avg ask | win % | breakeven % | edge pp | CI95 | $/\$ risked |
|---|---|---|---|---|---|---|---|---|
| 120s | 109 | 109 | 0.269 | 10.09 | 28.26 | −18.16 | [4.59, 15.60] | −0.703 |
| 90s | 114 | 114 | 0.232 | 8.77 | 24.45 | −15.68 | [3.51, 14.04] | −0.699 |
| 60s | 117 | 117 | 0.200 | 5.98 | 21.13 | −15.15 | [1.71, 10.26] | −0.800 |
| 45s | 119 | 119 | 0.181 | 6.72 | 19.18 | −12.46 | [2.52, 11.76] | −0.822 |
| 30s | 79 | 79 | 0.195 | 3.80 | 20.65 | −16.85 | [0.00, 8.86] | −0.916 |
| 15s | 51 | 51 | 0.209 | 1.96 | 22.06 | −20.10 | [0.00, 5.88] | −1.005 |
| ≤20s (pooled) | 57 | 275 | 0.216 | **0.73** | 22.84 | −22.11 | — | ~−1.05 |

By price bucket, every cell is negative, and **no bucket's CI reaches its breakeven**:

| bucket | windows | obs | avg ask | win % | breakeven % | edge pp | depth median |
|---|---|---|---|---|---|---|---|
| <10c | 23 | 137 | 0.081 | 0.00 | 8.61 | −8.61 | 5 |
| 10–15c | 9 | 22 | 0.127 | 0.00 | 13.50 | −13.50 | 5 |
| 15–20c | 104 | 289 | 0.165 | 3.11 | 17.47 | −14.36 | 178 |
| 20–25c | 66 | 122 | 0.218 | 4.92 | 23.02 | −18.10 | 187 |
| 25–30c | 38 | 64 | 0.268 | 7.81 | 28.14 | −20.33 | 200 |
| 30–35c | 32 | 48 | 0.321 | 10.42 | 33.63 | −23.21 | 217 |
| 35–40c | 31 | 53 | 0.374 | 15.09 | 39.05 | −23.96 | 82 |
| 40–45c | 19 | 32 | 0.425 | 9.38 | 44.24 | −34.87 | 25 |
| 45–50c | 25 | 46 | 0.467 | 10.87 | 48.48 | −37.61 | 9 |

States counted rather than dropped: **UNDERDOG 813**, **NO_UNDERDOG (cheapest ≥50c)
240**, **NEITHER_SIDE_EXECUTABLE 410**. Opportunities/day at the pooled rate: 1,829 —
which is the point: the opportunity is abundant and uniformly bad.

### BTC15m — 15 windows, 459 underdog observations

Pooled: avg ask **0.3051**, win **43.79%**, breakeven **30.51% / 31.99%**, edge
**+11.80pp**, **+$0.650 per dollar risked** — but CI **[21.74, 65.26]** straddles
breakeven. All ten requested checkpoints are present (840s…60s). The cell pattern is
not monotone in either time or price (+46.77pp at 10–15c, −8.70pp at 45–50c, +37.29pp
at 120s, −0.29pp at 720s), which is what 15 windows looks like. **No cell is selected
and no threshold is fitted**; the matrix is reported as produced.

---

## 6. $5 net-win sizing test

Deterministic in entry price alone — `q = ceil(5 / ((1−p)·(1−0.07p)))`. No
martingale, no size increase after a loss. Depth must cover the whole quantity or the
opportunity is DEPTH-BLOCKED and never partially filled.

**BTC5m:** 517 trades over 128 windows, required contracts median 7 (range 6–11),
capital at risk median $1.41 (max $5.58). **39 wins / 478 losses.** Total net
**−$668.29**; −$1.293/trade; **−$5.221/window**; **−$1,503.65/day**;
1,163 opportunities/day; **max 115 consecutive losses**; max drawdown **−$673.67**;
max concurrent capital $26.90; **296 depth-blocked** opportunities. Ratio to the
$480/day target: **−3.13**.

**BTC15m: not testable.** No level sizes were recorded, and the rule requires
displayed depth for the whole quantity.

---

## 7. Does the executable price reflect the settlement state?

Causal features only (at checkpoint *t*, from observations at tte ≥ *t*);
`settlement_value` / `resolution_side` / `up_won` are labels, never features. The
reference series is a **Coinbase spot proxy** — Gemini's own index is
`LS-LRRR-XBTUSD` in this era and no historical series of it is held, so a
price/state disagreement could be the proxy. Stated as a limitation, not a result.

**The state is nearly deterministic, and the price knows it:**

| proxy distance from strike | obs | windows | UP settled % | median UP ask | median UP bid |
|---|---|---|---|---|---|
| −20…−10 bps | 7 | 1 | 0.00 | 0.08 | — |
| −10…−5 bps | 75 | 13 | 1.33 | 0.08 | 0.36 |
| −5…0 bps | 562 | 76 | **7.30** | 0.26 | 0.12 |
| 0…+5 bps | 709 | 85 | **94.08** | 0.85 | 0.74 |
| +5…+10 bps | 94 | 14 | 100.00 | — (no ask) | 0.91 |
| +10…+20 bps | 11 | 3 | 100.00 | — (no ask) | 0.91 |

Volatility-normalised required move is cleanly monotone: ≤ −4σ → **100%** UP;
−2…−1σ → 96.1%; 0…+0.5σ → 37.0%; +1…+2σ → 4.05%; ≥ +4σ → **0.36%**.

So: **direction yes, level no.** The executable price tracks the causal state, and
the residual uncertainty (±5 bps ≈ a few dollars on $85k) is far smaller than the
33-cent spread charged to trade it.

### Favorite test, state-conditioned

Buy the side the causal proxy state favors, at its executable ask, hold to
settlement. **Only 138 of 1463 observations have an executable ask on the favored
side** — usually the favorite has a bid and no ask at all.

Pooled: avg ask 0.788, win **75.36%**, breakeven 80.00%, edge **−4.64pp**, CI
[66.67, 83.72], **CI low − breakeven = −13.33** → not established and
point-negative. The positive cells are tiny and thin: tte 30s (n=4, depth median 291)
and the 0.00–0.50 ask bucket (n=14, 9 windows, edge +34.11pp, CI low − breakeven
**−0.96**). Cheap-favorite observations at 15s/10s/5s have depth of **0.6–2.0
contracts**. Nothing here is a candidate; it is a reason to collect.

**This is not permission to buy 90–99c favorites.** The 0.95–1.01 bucket measures
79.31% settlement against a 97.46% breakeven: **−18.15pp**.

---

## 8. Maker economics

Reported only as defensibly capturable fills under conservative queue assumptions,
from the existing 960-cycle queue-respecting census (`overlay_study.json`):

| overlay | queue-readable cycles | queue-respecting fills | fill share | median adverse move after fill |
|---|---|---|---|---|
| join the touch | 499 | **3** | **0.6%** | +0.125 |
| native (frozen) | 461 | 2 | 0.4% | +0.125 |
| improve one tick | 499 | **8** | **1.6%** | +0.115 |

Our own quote would have been best liquidity in **100%** of cycles and still filled
~1% of the time, and the fills that did arrive were adversely selected by ~12 cents.
**The 33-cent BTC5m spread is wide because nothing trades, and a maker cannot
harvest it.** BTC15m has no equivalent census — its 1,580 recorded placement intents
are all `filled: false`, `from_bid: 0.00`, `inside_spread: false`, i.e. hypothetical
joins in readiness mode with no `rest_s` at all. That is a gap, not a negative.

---

## 9. BTC5m vs BTC15m

| Metric | BTC5m | BTC15m |
|---|---|---|
| Windows/day | 288 | 96 |
| Usable historical windows (book + settlement) | **133** | **15** |
| Settled windows with print tape (no book) | 672 | 655 |
| Checkpoints available / requested | 6 / 8 (240s, 180s never collected) | **10 / 10** |
| Executable depth | median 178, **p10 = 5** contracts | **not recorded** |
| `ask_UP + ask_DOWN` median | **1.330** | **1.080** |
| Touch spread median | **0.330** | **0.080** |
| Taker cost at the touch | ~16.5c per side | ~4c per side |
| Median notional/window | **$10.80** | **$179.68** |
| Median prints/window | ~4 | ~9 |
| Zero-print windows | 66 / 672 (9.8%) | 74 / 655 (11.3%) |
| Maker economics | 0.6–1.6% fill, +0.12 adverse | **no census** |
| Underdog calibration | 5.04% actual vs 22.80% breakeven | 43.79% vs 31.99%, CI straddles |
| Best broad region | **none** — every cell negative, no CI reaches breakeven | none established on 15 windows |
| $5-net sizing P&L/day | **−$1,503.65** | **untestable** (no depth) |
| Max drawdown | **−$673.67** | n/a |
| Longest loss streak | **115** | n/a |
| Estimated capacity | irrelevant (negative) | **unknown** |
| Settlement-state usefulness | state is ~deterministic beyond ±5bps, but unexecutable where it matters | untested (no reference recorded) |
| Evidence quality | **strong negative** | **insufficient** |

---

## 10. L5 sequential-inventory extension

**BTC5m: no.** A first-fill → residual → COMPLETE/SELL/WAIT/HOLD sequence needs flow
to work against. BTC5m has a median of **4 prints and $10.80 of notional per window**,
the book is active **1.1%** of the window's life, the median dead gap is **195s of a
300s tradeable life**, and **5 of 30** censused windows had zero prints in the whole
quoting region. On top of that, maker capture is 0.6–1.6% with 12 cents of adverse
selection. There is no inventory to sequence.

**BTC15m: possibly, and the data is worth preserving.** 17× the notional
($179.68 median), ~9 prints/window, and an 8-cent book. But three prerequisites are
missing and must be collected, not assumed: level **sizes** (so complement
reachability and residual capacity are measurable), a **maker fill census** with real
`rest_s` and queue observation, and more than 15 windows of book. The BTC15m tapes
and the settled-contract join used here are preserved for that follow-up.

---

## 11. What was not done, deliberately

- **Early exit stays sealed.** No future bid path, MFE or exit-timing optimisation
  was inspected. Hold-to-settlement is frozen first, and no defensible candidate
  emerged on BTC5m to unseal it for.
- **No threshold was optimised.** The checkpoints and price buckets were fixed before
  any outcome was read; the state surfaces are descriptive and no rule is fitted.
- **No new strategy was invented.** The two formulations tested are the two that were
  asked for.
- **No failed Gemini experiment was rerun.** The underdog/favorite executable-price
  question had not been asked on this corpus before.

## Artifacts

`analysis/gemini/updown/underdog_matrix.json` ·
`settlement_state_calibration.json` · `state_conditioned_favorite.json` ·
scripts `gemini_underdog_matrix.py`, `gemini_settlement_state.py`.
