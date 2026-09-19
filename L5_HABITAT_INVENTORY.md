# Habitat inventory — what we already have, and what can be replayed today

**Asked for first, before any new collection.** This is the inventory. The
matrix (`L5_HABITAT_MATRIX.md`) follows once the replays have run.

**Headline: four habitats are replayable immediately with zero new
infrastructure.** Both venues' external tapes and books fetch live right now.

**And one correction has to come first, because it changes how my last report
should be read.**

---

## 0. CORRECTION — the L5 "recycling gradient" is confounded with regime

`src/l5_shadow/cohorts.py` records a measurement I did not consult before
writing `HABITAT_SELECTION_1.md`. Chain-attributed roles, cross-checked against
the venue's own maker flag at **1,270 of 1,270**:

| cohort | windows | fills | **maker %** | fills/window |
|---|---|---|---|---|
| **BTC15m** | 527 | 63,569 | **95.5%** | 121 |
| BTC5m | 957 | 6,227 | **0.05%** | 6.5 |
| ETH15m | 29 | 117 | **0%** | 4.0 |
| ETH5m | 703 | 2,069 | **0.24%** | 2.9 |

**Only BTC15m is a maker operation. BTC5m and ETH5m are predominantly TAKER** —
a different strategy, not a thinner version of the same one. It is pinned by
`tests/test_l5_cohorts.py`, and that module's own docstring warns that
averaging the cohorts "produced every misleading cross-cohort figure this
programme has published".

**I published another one.** `HABITAT_SELECTION_1.md` §6 reported
75.7% → 50.2% → 20.7% par-constrained recycling across BTC15m → BTC5m → ETH5m
and read the five monotone features as a *habitat* gradient. At least part of
that gradient is a **maker-versus-taker regime difference**: a taker's
round-trip and a maker's recycling are not the same quantity and should never
have shared an axis.

**What survives:** L5 BTC15m at 75.7% par-constrained recycling is the *maker*
benchmark, and it is real and relevant. **What does not:** the claim that five
pre-entry features describe a recyclable *habitat*. Two of them —
`prior_two_sided` and `prior_side_balance` — are exactly what a maker produces
by definition, so on the L5 side they may be measuring "was L5 making here",
not "was this market recyclable".

**Nothing about the Gemini findings changes.** The frontier (82.1% crossing),
the single par-constrained positive in 31 windows, the oracle bound and
GEMINI_RECYCLE are all computed from Gemini's own external tape and are
untouched.

**This sharpens the P0 rather than blunting it.** The comparison that matters
is **maker-regime against maker-regime**: our Gemini quoting against L5's
BTC15m operation — not against L5's taker cohorts. And it says where to look
first: the habitats where a maker regime is *known* to exist.

`HABITAT_SELECTION_1.md` has been corrected in place.

---

## 1. What already exists — collectors

| asset | what it gives | state |
|---|---|---|
| **`src/l5_shadow/venues.py`** | Polymarket `pm_window` / `pm_book` / `pm_trades`, Gemini `gemini_book` / `gemini_trades`, `live_windows()` over **both 5m and 15m** | **working, verified live today** |
| `src/l5_shadow/cohorts.py` | the maker/taker regime split above | working, test-pinned |
| `src/l5_adapters/gemini_mapping.py` | arithmetic Polymarket↔Gemini symbol mapping, no name matching | working |
| `src/gemini_execution/tape_replay.py` | the sequential engine: flat → maker fill → Model-A action → tape → COMPLETE/EXTEND → matched → residual | working, 31 tests |
| `src/gemini_execution/habitat_selection.py` | pre-entry rows, leakage check, oracle bound | working, 27 tests |
| `src/gemini_execution/window_volume.py`, `bursts.py` | prints/min, notional/min, burst concentration, TTE histograms | working |
| `src/l5_postfill_crossvenue.py` | post-fill sequence census | working, needs the regime split applied |
| `src/pmus_ws_evidence_recorder.py` | the live PMUS trades tape | **running as a service** |

**Nothing new is needed to start.** The engine is venue-neutral already: it
takes cycles (book snapshots), prints (external tape), a resolution and an
expiry. It does not care which venue produced them.

## 2. What already exists — data

| dataset | size | what it is | usable for the matrix? |
|---|---|---|---|
| `analysis/l5/` | 2.1 GB | L5 research artefacts, `l5_all_trades.csv` (71,984 L5 fills, 2,216 windows) | **L5's OWN fills only** — not an external tape |
| `analysis/gemini/` | 46 MB | 31 reconstructed BTC5m windows, tapes, replays, habitat rows | **yes — the reference habitat** |
| `data/gemini_surface_shadow/` | 28 MB | BTC05M surface-shadow sessions, `VOLUME_AHEAD_CLEARED` evidence rungs | yes, as corroboration |
| `data/l5_book_context/` | 48 MB | book context around L5 activity | partially — needs schema check |
| `data/pmus_book_tape/` | 14 MB | PMUS book snapshots, 2026-09-17 | yes, for a historical PMUS replay |
| `data/traders/`, `analysis/traders/` | 543 MB | other traders' activity | not needed yet |
| `data/l5_window_forensics/` | 7.1 MB | per-window forensics | corroboration |

**The important gap:** `l5_all_trades.csv` is **L5's own fill history**, which
the owner explicitly asked us to avoid for cross-habitat comparison. For
apples-to-apples we need each venue's **external tape**, and §3 shows we can
fetch it.

## 3. Which habitats can be replayed immediately

Verified live at 14:35Z today, no new collector:

| habitat | external tape | book | terms | replayable now |
|---|---|---|---|---|
| **Gemini BTC5m** | `gemini_trades` ✓ | `gemini_book` ✓ | ✓ | **yes — 31 windows already done** |
| **Gemini BTC15m** | `gemini_trades` ✓ (3 prints in the live window) | `gemini_book` ✓ (0.74/202, 0.73/500 …) | ✓ | **yes** |
| **Polymarket BTC5m** | `pm_trades` ✓ (100 prints) | `pm_book` ✓ | ✓ | **yes** |
| **Polymarket BTC15m** | `pm_trades` ✓ (48 prints) | `pm_book` ✓ full ladder | ✓ | **yes** |
| PMUS sports | live service tape (1,461 markets, 4,678 prints/heartbeat) | via recorder | needs check | **probably — needs one schema pass** |
| Gemini non-BTC families | unknown — `metadata.py` is hard-wired to `BTC05M` | — | — | **needs a discovery pass** |

**Polymarket BTC15m is the priority.** It is the one habitat where a maker
regime is *known* to exist (95.5% maker, 121 fills/window), and its external
tape gives us what L5's own fill history cannot: what **everyone else** did,
so our hypothetical maker quote can be tested against flow that is not L5's.

### Executability facts already available for Polymarket

Straight from `pm_window`, so the matrix's fee/rebate/reward columns are
sourced rather than assumed:

| | value |
|---|---|
| tick | 0.01 |
| taker fee rate | 0.07 |
| **fee rebate rate** | **0.2** |
| **minimum order size** | **5** |
| rewards max spread | 4.5 |

**The minimum order size of 5 matters.** Our Gemini size is 0.01 contracts;
Polymarket will not accept anything below 5. That is a real executability
difference and belongs in the matrix as its own column, not buried — a habitat
we cannot enter at our size is not a habitat for us, whatever its flow looks
like.

## 4. What I propose to run next, in order

1. **Gemini BTC15m** — same venue, same code path, only the duration changes.
   Cheapest possible test of "is it the venue or the timeframe?"
2. **Polymarket BTC15m** — the known maker regime, external tape,
   apples-to-apples against our Gemini quoting.
3. **Polymarket BTC5m** — completes the 2×2 and separates venue from timeframe
   cleanly.
4. **PMUS sports** — one schema pass on the live recorder's tape first.
5. **Gemini non-BTC discovery** — only after the 2×2, since it needs new
   metadata work and the 2×2 may already answer the question.

Each runs the same sequential question with **Model A frozen**: flat →
hypothetical maker fill → Model-A action → subsequent external tape →
COMPLETE/EXTEND opportunities → matched inventory → terminal residual.

`L5_HABITAT_MATRIX.md` will report N windows, initial-fill rate,
opposite-flow-after-fill rate, COMPLETE rate, par-constrained pair rate,
matched/gross, median pair basis, terminal residual/gross, capital at risk,
maker-accessible executed notional and TTE/runway — **evidence and sample
sizes, no composite score.**

## 5. Gemini BTC5m continues unchanged

Collection continues (pre-entry pairs 18 of 20, loop logging per-run exit
codes). The canonical sequential replay, frontier and habitat report refresh as
independent N grows. **No further optimisation** — GEMINI_RECYCLE / the EXTEND
veto stays as the adapted control, Model A, the TTE rules and the live
authorization state are untouched, and no execution arms were added.

**Crypto is not assumed to win.** The 2×2 above tests venue and timeframe
separately, and PMUS sports is in the queue precisely because the answer may
not be crypto at all.

---

**Labels.** Nothing here is live, no authorization is requested, and every
replayed execution in this programme is SHADOW/THEORETICAL. Matched-pair
economics, directional-residual exposure, fees, rebates and rewards are
separate lines and are never summed.
