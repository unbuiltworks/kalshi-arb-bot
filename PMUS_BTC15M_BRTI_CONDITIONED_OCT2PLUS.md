# PMUS BTC15m — BRTI-conditioned descriptive study (Oct 2+)

**Status: descriptive. No strategy selected, no threshold searched, no P&L optimized.**
Run 37303123459 · tape code pinned at `ae80e9d5` · archive run 37253257157 · 74 parts

A **separate** study. It does not read, modify or re-tune the frozen underdog
matrix (`src/pmus/btc15m_spec.py`, `PMUS_BTC15M_ECONOMICS.md`,
`PMUS_BTC15M_PARTIAL_DISCOVERY_OCT2PLUS.md`). It reuses the same ten frozen
checkpoints verbatim.

## Prior art — read this before treating anything here as new

This programme has already asked the lag question and answered it negatively,
on the **178-window generating study** and the 2026-09-13 live tape (not Oct 1,
so no holdout contamination):

- `scripts/pmus_brti_lag.py` — BRTI→PMUS latency with the information-lead vs
  timestamp-illusion distinction. Its publication discipline is reused here
  rather than re-derived.
- `analysis/l5/path_economics/SETTLEMENT_LOCK_DIAGNOSTIC.md` — *"the hurdle is
  correct, causal, and too late to use."*
- `analysis/l5/path_economics/ADDENDUM_LAG_AND_CAPTURE.md` — *"BRTI lag is real
  but worthless."*

What follows is an independent second measurement on Oct 2+ **executable** tape,
conditioned on settlement state rather than on generic BRTI moves. It agrees
with the prior conclusion.

## Settlement mechanics, and why nothing is ever "locked"

From the venue's own rules via the canonical `src/pmus_brti.py`: the close
statistic is the mean of the **60** one-second BRTI closes on bucket starts
`[T−60, T−1]`, half-even to the cent; the strike (`priceToBeat`) is the same
statistic at the window open; **ties settle UP**.

A bucket stamped `s` covers `[s, s+1)` and cannot be known before `s+1`.
Counting by availability rather than by stamp, at `t = T−1` only **59** of the
60 observations are published. **Inside the final minute at least one
settlement observation is always unpublished, so no outcome is ever
arithmetically determined before `T`** — confirmed on all 17,100 rows. There is
no `locked` field anywhere; the tables report the required remaining average and
the size of the move it needs, in dollars, basis points and sigmas.

## Sample

| | |
|---|---|
| Windows with book rows / with settlement | 296 / 1,167 |
| Eligible (whole index span `[T−960, T+5]` at or after Oct 2 00:00Z) | 291 |
| **Both published statistics re-derived EXACTLY** | **285** |
| Excluded: statistics did not reproduce | 6 |
| Excluded: window would need a pre-Oct-2 second | 1 |
| Windows per day | Oct 2: 95 · Oct 3: 92 · Oct 4: 94 · Oct 5: 4 |
| Book rows | 2,264,918 kept, 6 dropped unusable |

**The 6 verification failures are all one-cent disagreements** (e.g.
reconstructed 84846.14 vs published 84846.15), one on the open and five on the
close — a half-even rounding-boundary artifact, not a wrong model. They are
excluded rather than waved through, and they are worth a separate look at the
venue's rounding of exact half-cents.

## A. Checkpoint state join — 2,850 rows (285 × 10), 0 excluded

Every checkpoint had a causal two-sided book.

| quantity | p10 | median | p90 | max |
|---|---:|---:|---:|---:|
| cheaper executable ask | $0.02 | $0.21 | $0.46 | $0.51 |
| \|distance from strike\| (bp) | 0.47 | 3.53 | 16.90 | 141.76 |
| trailing 60s realized vol (bp per second) | 0.059 | 0.220 | 0.708 | 6.27 |
| BRTI change over prior 30s (bp) | −2.14 | +0.02 | +2.27 | ±38 |
| BRTI change over prior 60s (bp) | −3.08 | +0.04 | +3.28 | ±40 |
| quote age (s) | 0.011 | 0.057 | 0.136 | 6.02 |

Cheaper side: DOWN 1,578 / UP 1,272. Full per-row table in
`dumps/checkpoint_states.jsonl.gz`; calibration by checkpoint × distance-sigma
band is in the JSON.

The scale that matters: the median checkpoint sits **3.5 bp** from the strike
while one second of index vol is **0.22 bp**. Distance from the strike is
typically many seconds of volatility even 14 minutes out.

## B. Every second of the final 60 — 17,100 rows

Per-second fields (`dumps/final_60s_states.jsonl.gz`): observations known,
their cumulative sum and mean, observations remaining, the required remaining
average for the opposite outcome to cross the strike, the gap from the current
causal BRTI in dollars / bp / sigmas, the projected settlement and side, and the
executable bid / ask / depth / quote age.

**Required opposite-outcome move, by seconds remaining (|sigmas|):**

| secs left | n | p10 | median | p90 | max |
|---|---:|---:|---:|---:|---:|
| 46–60 | 4,275 | 0.51 | 3.44 | 11.29 | 53.5 |
| 31–45 | 4,275 | 0.72 | 4.77 | 15.28 | 71.7 |
| 16–30 | 4,275 | 1.17 | 8.15 | 26.76 | 101.9 |
| 6–15 | 2,850 | 2.63 | 18.72 | 65.54 | 263.2 |
| 1–5 | 1,425 | 8.74 | 71.49 | 307.79 | 1565.1 |

The required average runs away exactly as the arithmetic demands: each consumed
observation forces the remainder to carry more.

**The calibration that matters — and the 1-cent floor that answers the money question:**

| secs left | σ band | n | projected side correct | Wilson 95% | losing side's ask (median / p90) |
|---|---|---:|---:|---|---|
| 46–60 | 0–1 | 814 | 80.34% | [77.5, 82.9] | **$0.22 / $0.43** |
| 46–60 | 1–2 | 553 | 94.03% | [91.7, 95.7] | $0.03 / $0.08 |
| 46–60 | 2–3 | 512 | 99.80% | [98.9, 100.0] | $0.01 / $0.07 |
| 46–60 | ≥3 | 2,396 | 99.9–100% | — | $0.01 / $0.01–0.02 |
| 31–45 | 0–1 | 604 | 93.71% | [91.5, 95.4] | $0.13 / $0.36 |
| 31–45 | 1–2 | 466 | 97.42% | [95.6, 98.5] | $0.02 / $0.07 |
| 31–45 | ≥2 | 3,205 | 99.7–100% | — | $0.01 |
| 16–30 | 0–1 | 362 | 93.37% | [90.3, 95.5] | $0.06 / $0.43 |
| 16–30 | ≥1 | 3,913 | 100% | — | $0.01 |
| 6–15 | all | 2,850 | 99.0–100% | — | $0.01–0.03 |
| 1–5 | all | 1,425 | 100% | — | $0.01–0.02 |

**PMUS has already priced the determined states to the tick floor.** Wherever
the settlement state is effectively settled (≥2σ required, or any band inside
30 seconds), the losing side is executable at **$0.01** — the minimum tick. At
that price the buyer pays 1c for something that happened 0–0.2% of the time, so
the floor removes the value rather than leaving it on the table. There is no
stale cheap side to buy in the determined states.

The genuinely uncertain cells are priced roughly fairly: at 46–60s / 0–1σ the
projected side won 80.3% and the losing side cost ~22c.

**One cell is worth a pre-registered follow-up, and I am NOT selecting it:**
46–60s / 1–2σ shows 94.03% accuracy (losing ~6.0%, CI 4.3–8.3%) with the losing
side at a 3c median ask. That gap is in the buyer's favour on its face. It is
**not** a candidate: these 553 rows come from far fewer than 553 windows, the
medians are not matched row-by-row to the asks that accompanied them, and
testing it here would be exactly the threshold search this pass forbids. It
belongs in its own freeze, evaluated row-wise with fees.

## C. Does PMUS repricing lag material BRTI settlement-state changes?

16,815 second-to-second state changes; bands fixed in advance; availability is
bucket close + 1s, a **lower bound** on when the index could be acted on, so
every lead below **overstates** what was reachable. Only **68 events (0.4%)**
flipped the projected side at all.

| Δstate band (σ) | events | PMUS made a ≥1c move | median delay | p90 | **already moved BEFORE availability** |
|---|---:|---:|---:|---:|---:|
| 0–1 | 15,754 | 12.3% | 0.88 s | 7.97 s | 5.2% |
| 1–2 | 753 | 18.7% | 0.97 s | 11.46 s | 9.3% |
| 2–3 | 177 | 18.6% | 1.05 s | 6.02 s | 9.6% |
| 3–5 | 102 | 27.5% | 0.68 s | 5.15 s | 16.7% |
| 5–10 | 29 | 44.8% | 2.03 s | 16.75 s | **31.0%** |

**Answer: the lag is real but sub-second at the median, and it shrinks as a
share of what is reachable precisely when the move is large.** Three things kill
it:

1. **Median response is ~0.7–2.0 s from a lower-bound availability**, against
   measured venue read round-trips of median 273 ms / p90 459 ms — and an order
   submission does a preview POST first, so the real budget is worse.
2. **The illusion share rises with magnitude**: in the largest band **31% of
   apparent responses had already happened before the index bucket could even be
   published.** For the moves big enough to matter, PMUS leads the index
   measurably often. A naive timestamp join would have called those leads.
3. **Most state changes get no response at all** (55–88%), because the quote was
   already at the tick floor or already correct. Quote ages are tiny (median
   0.18 s), so this is not a stale-book artifact.

This reproduces the programme's earlier finding — real, but not usable — on
independent Oct 2+ executable tape and with settlement-state conditioning rather
than generic moves.

## What this does not establish

1. **17,100 second-rows are not 17,100 observations.** They come from 285
   windows, 60 rows per window, and adjacent seconds are nearly identical. The
   Wilson intervals above are **far too narrow**; treat the effective sample as
   ~285, clustered in 3 full days plus a partial one.
2. **Three days.** Oct 2–4 complete, Oct 5 partial (4 windows). No day-level
   inference is supportable.
3. **Displayed touch only.** The tape carries no ladder, so capacity beyond the
   touch is unknowable, and no market impact, queue position, partial fill or
   adverse selection is modelled.
4. **No fees are applied in this pass**, because no trade is proposed. Any cell
   that looks positive here must be re-examined with the canonical fee schedule
   bound (`src.pmus_fees`, θ_taker 0.0695) before it means anything.
5. **Settlement outcomes were used for labelling only.** No computed state
   field reads them.

## Artifacts

| path | contents |
|---|---|
| `analysis/pmus/brti_conditioned/brti_conditioned_oct2plus.json` | all distributions and calibration tables |
| `.../dumps/checkpoint_states.jsonl.gz` | A — 2,850 checkpoint state rows |
| `.../dumps/final_60s_states.jsonl.gz` | B — 17,100 per-second rows |
| `.../dumps/lag_events.jsonl.gz` | C — 16,815 state-change events |
| `data/brti_1s_oct2plus/` | the harvested 1s index per window, committed as evidence |
| `src/pmus/brti_state.py` + `tests/test_brti_state.py` | the arithmetic, with 28 synthetic-data guards |

Oct 1 remains sealed: pre-Oct-2 parts were deleted before any analysis step, no
window whose index span reaches a pre-Oct-2 second was eligible, and a post-run
gate re-proved every emitted row.
