# The L5 Habitat Score, and the PMUS baseline

**Read-only and pre-action.** No production change, no admission change, no
sizing/concurrency change, no deployment. **The score gates nothing.** PMUS
remains bounded production + evidence collection and is **not disabled**.
COMPLETE microstructure work is **frozen at current evidence**; `ef5ef135` is
accepted as the PMUS habitat verdict.

---

## 0. What it answers, and what it refuses to

> **How much does this market environment resemble the environments in which
> L5 actually generated his inventory cycles?**

It does **not** predict profit and is not calibrated to profit. It is built
only from variables observable **before any strategy acts**: no settlement
outcome, no realized P&L, none of our own fills, no order state. Those fields
are on a `FORBIDDEN_INPUTS` list and the scorer **raises** if one reaches it —
refused, not ignored, with a test per field.

---

## 1. The thresholds are L5's

Every calibrated feature is scored against the **empirical distribution of
that feature across L5's own 1,893 canonical windows**. Reaching his median
scores 0.5; reaching his p90 scores 0.9. No cut-off is chosen by hand, and
`derive_reference()` regenerates the whole table from the canonical sequence
so it can be audited rather than trusted.

**The reference and the candidates go through the same function.** L5's
windows and every candidate window are both turned into features by
`l5_habitat_score.window_features`, so a difference in score cannot come from
a difference in how the features were computed.

| calibrated feature | kind | L5 p10 | **L5 p50** | L5 p90 |
|---|---|---:|---:|---:|
| `price_changes_per_min` | CAPACITY | 0.866 | **2.344** | 10.769 |
| `share_move_ge_1c` | CAPACITY | 0.572 | **1.000** | 1.000 |
| `share_move_ge_2c` | CAPACITY | 0.407 | **0.800** | 1.000 |
| `share_move_ge_5c` | CAPACITY | 0.286 | **0.625** | 1.000 |
| `price_span` | CAPACITY | 0.172 | **0.780** | 0.960 |
| `bands_visited` | CAPACITY | 2 | **4** | 7 |
| `tte_p50_s` | REGIME | 69 | **171** | 524 |

**CAPACITY** features saturate at 1.0 — a richer market is not penalised and
earns no bonus. **REGIME** features are two-sided: a TTE far from L5's is
*different*, not better, whichever way it differs.

---

## 2. The asymmetry that decides how this may be read

L5's canonical tape records **his fills**, not his market's order flow. His
fill cadence is therefore a **lower bound** on what his markets offered — he
traded at least that fast, so the market supplied at least that much.

The inference runs **one way only**:

| | |
|---|---|
| candidate **below** the reference | **conclusive habitat deficit** — the market cannot supply even what L5 consumed |
| candidate **above** the reference | **not** conclusive sufficiency — a market can move constantly and never present a completable opposite side |

**The score is a ceiling on habitat adequacy, never an estimate of it.** A
high score licenses further observation; it never licenses capital.

---

## 3. Three kinds of "we don't know", kept apart and never imputed

| label | meaning |
|---|---|
| **UNKNOWN** | the candidate did not supply the field, or supplied it saturated/unusable |
| **UNCALIBRATED** | L5's public tape cannot produce a reference at all — spread, depth, book-update rate, queue, trade-through are invisible in a fills-only record. **Reported for the candidate, excluded from the score** |
| **NOT_APPLICABLE** | meaningless for this venue class |

None is ever imputed to a favourable value. Missing calibrated features leave
the denominator, and `coverage` plus `coverage_discounted_score` make the loss
visible: a market scored on 2 of 7 features **cannot** reach a high discounted
score even with perfect values, and a test pins that.

**A saturated counter is UNKNOWN, never a floor read as a measurement.** PMUS's
`ws_trades_total` is a deque length capped at 500 and is **saturated in all 14
markets observed**, so trades/min is reported as UNKNOWN rather than derived
from a pinned counter.

### The demand side is reported beside the score, never inside it

`fills_per_min`, `switches_per_min` and `switch_rate` are what L5 **consumed**.
A candidate must supply at least that much **in trades** before it can be
called adequate — and a book-update rate is **not** a substitute, because
quotes are not trades. PMUS cannot supply these today: **UNKNOWN**.

---

## 4. Sampling bias, exposed because it decides the reading

Observation cadence biases the features in **opposite directions**:

| feature | bias from coarse sampling |
|---|---|
| `price_changes_per_min` | **DOWN** — a rate cannot exceed the observation cadence |
| `bands_visited` | DOWN |
| `share_move_ge_1c/2c/5c` | **UP** — more price action accumulates between observations |
| `price_span` | **UP** |

So a low score on a `sampling_limited` rate feature proves little. **A low
score on the UP-biased features is failing despite a bias in the candidate's
own favour, and that is the stronger reading.** The score exposes both.

---

## 5. The PMUS baseline

From 356 `PMUS_WS_XCHECK` rows, 18 markets seen, **14 scored** (4 skipped for
under 180 s of observation). The price series is the **BBO mid — quotes, not
trades** — and that label travels with every row.

| market | obs | span s | score | cov | discounted |
|---|---:|---:|---:|---:|---:|
| `asc-nfl-dal-nyg-pos-8pt5` | 10 | 229 | 0.2537 | 0.86 | 0.2175 |
| `aec-cs2-spo-war` | 252 | 3990 | 0.2082 | 0.86 | 0.1784 |
| `aec-setkameua-kryyev-dukole` | 22 | 289 | 0.1988 | 0.86 | 0.1704 |
| `atc-lmx-san-ju-ju` | 60 | 760 | 0.1678 | 0.86 | 0.1438 |
| `aec-setkamemd-salnic-meraur` | 30 | 782 | 0.1514 | 0.86 | 0.1298 |
| `asc-nfl-dal-nyg-pos-7pt5` | 53 | 1026 | 0.1336 | 0.86 | 0.1145 |
| `aec-itfwo-chezhu-yuhwan` | 156 | 3541 | 0.1249 | 0.86 | 0.1071 |
| `atc-lmx-san-ju-draw` | 79 | 1806 | 0.1080 | 0.86 | 0.0926 |
| `aec-itfwo-yinzha-xiluo` | 36 | 518 | 0.0875 | 0.86 | 0.0750 |
| `aec-itfwo-junzha-ruwu` | 29 | 719 | 0.0640 | 0.86 | 0.0549 |
| `asc-nfl-dal-nyg-pos-6pt5` | 150 | 2433 | 0.0621 | 0.86 | 0.0533 |
| `asc-nfl-dal-nyg-pos-3pt5` | 59 | 3173 | 0.0502 | 0.86 | 0.0430 |
| `atc-lmx-san-ju-san` | 98 | 1987 | 0.0470 | 0.86 | 0.0403 |
| `tsc-nfl-dal-nyg-total-48pt5` | 11 | 280 | 0.0402 | 0.86 | 0.0344 |

### **PMUS BASELINE: habitat score p50 = 0.108, coverage 0.857, discounted p50 = 0.0926**

Best market observed: **0.2537**. L5's own median window scores 0.5 by
construction.

**And the bias check makes it conclusive.** On the four features where coarse
sampling biases the measurement **in PMUS's favour**, PMUS's median scores are:

| UP-biased feature | PMUS p50 score |
|---|---:|
| `share_move_ge_1c` | **0.0561** |
| `share_move_ge_2c` | **0.0491** |
| `share_move_ge_5c` | **0.0121** |
| `price_span` | **0.0991** |

PMUS sits at roughly **L5's 1st–10th percentile on the features whose bias
runs its own way**. `price_changes_per_min` is flagged sampling-limited in at
least one market and its low score is discounted accordingly — it is not
needed, because the UP-biased features carry the finding.

`tte_p50_s` is UNKNOWN in every market (XCHECK carries no TTE), which is what
holds coverage at 0.857.

---

## 6. What this does and does not establish

**Establishes.** By the one-directional rule in §2, PMUS sports as observed is
a **conclusive habitat deficit** relative to L5's environments: it sits below
the reference on features whose measurement error favours it. This is
independent evidence for `ef5ef135`'s verdict, reached from market state
before any strategy acts rather than from our own trajectories.

**Does not establish.** Nothing about profit. Nothing about whether a *better*
PMUS market exists — 14 markets over ~1 hour is a baseline, not a census.
Nothing about spread, depth, queue or trade-through, which L5's tape cannot
calibrate at all. And nothing about Gemini, which is not scored here because
the workstream has not returned an observation stream.

**Changes nothing.** Admission, sizing, concurrency, band, cap, maker-only and
the probe are untouched. The score is not wired to any gate.

---

## 7. What any venue must stream to be scored

One row **per observation**, over **complete windows**, open to settlement —
samples will not do:

```
window_id                              str
venue, category, duration_minutes
t                                      float epoch seconds
bid, ask                               float
bid_qty, ask_qty                       float contracts at the touch
last_trade_price                       float or null
trades_since_last_observation          int   -- a COUNT, not a capped deque
contracts_since_last_observation       float
tte_s                                  float seconds to settlement AT the obs
book_updates_since_last_observation    int
```

Cadence ≤ 10 s; finer is better, and the cadence is reported with the score
because it bounds what can be measured.

**Send a delta, not a total.** PMUS's `ws_trades_total` is a capped deque
length, saturated in every market observed, and therefore cannot measure a
rate at all. A delta since the previous observation — or a monotonic uncapped
counter — is required.

**Must not be sent:** anything downstream of a decision (our fills, order
state, settlement outcome, realized P&L); a capped counter presented as a
rate; imputed values for fields the venue did not supply.

**Still required and not substitutable** — the per-fill trajectory schema from
`ef5ef135` §5: one row per fill with `episode_id`, `venue_duration`, `seq`,
`t`, `side`, `price`, `qty`, `tte_s`, `maker_taker`, `sampled`, `acted`,
`refusal`; per episode `settlement_outcome`, `terminal_up`, `terminal_dn` and
venue-confirmed `realized_usd` (never a proxy). Minimum to be usable: **≥30
matched campaigns and ≥30 complement fills**. Campaign summaries are not a
substitute.

Reproduce: `python3 analysis/l5/habitat/run.py <railway-xcheck-dump.json>`
