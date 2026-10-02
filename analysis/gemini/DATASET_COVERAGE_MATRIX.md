# Gemini BTC5m/15m dataset coverage matrix — and the first real fills

2026-10-02. Collectors left running throughout. No orders. Fill rules
unchanged and not relaxed to make old data usable.

## Coverage matrix

Usable = can support queue-aware two-sided replay under the EXACT current
rules, which need all three of: a ladder or touch size (queue-ahead), trades
with an aggressor side (the only thing allowed to consume queue), and
timestamps that let a trade bind to the book state strictly before it.

| dataset | rows | mkts | depth | trades | aggressor | own fills | settle | **usable** | why |
|---|---|---|---|---|---|---|---|---|---|
| `gemini_trajectory/gemini_5m_canonical.jsonl.gz` | 182,119 | 756 | **touch size** (bid_sz/ask_sz, 75.7%) | **735 prints** | **`buyer_is_maker`** | no | no | **YES** | the only dataset with queue-ahead *and* venue-stamped aggressor |
| `p10_capture/cap_*.jsonl` | 4,168 | 15 | **BBO only, no sizes** | yes | no | no | no | NO | `kind:book` carries bid/ask/last and no size — queue-ahead unknowable |
| `btc5m_v2/continuous_v1.jsonl` | 338 | 15 | BBO from public contract prices | yes | no | no | no | NO | no size, no aggressor |
| `btc15m_v1/continuous_v1.jsonl` | 46 | 1 | BBO | no | no | no | no | NO | one market, no trades |
| `continuous_v1/continuous_v1.jsonl` | 992 | 43 | BBO | no | no | no | no | NO | no trades |
| `p9d_events/btc_arms_events.jsonl` | 4,643 | 631 | none | **hypothetical** | yes | no | no | NO | rows are `tier: AT_LEVEL_HYPOTHETICAL` with `tid: null` — modelled fills, not executions |
| `p7_bulk_tape.json` | 1 doc | 400 | none | no | no | no | no | NO | slate metadata |
| `tape_replay.json` | 1 doc | 49 | none | yes | no | no | yes | NO | derived replay output, no book |
| `window_volume.json` | 1 doc | 30 | none | yes | no | no | no | NO | public prints, no book to bind to |
| `cohort_run_*.json`, `cohort_window_*.json` | — | 23 | none | yes | **88 own fills** | yes | yes | NO (for replay) | the 88 own fills audited in `GEMINI_P0_*`; all BUYs, no book |
| `data/gemini_shadow/*` (live) | growing | 7 | **full ladder** | via companion poller | yes | no | no | **YES** | the prospective sample; 4 trades so far |

**One existing dataset qualifies**, and it was already in the repo:
`gemini_5m_canonical.jsonl.gz`, **2026-08-30T23:18Z → 2026-09-01T12:16Z**,
**103 distinct windows that printed**, 139 markets, 98,202 contracts traded.
That is roughly 100× the prospective sample collected so far.

## Why it qualifies without loosening anything

`buyer_is_maker` is the venue's own flag, so the consumed side is **read, not
inferred** — True means the buyer rested and the seller crossed, so the BID
was consumed. No quote-rule guessing. `bid_sz`/`ask_sz` is the resting size
**at the touch**, which is exactly the queue a maker quoting at the touch
joins.

**Two hard limits, enforced rather than papered over.** There is no depth
beyond the touch, so when inventory skew steps a quote off the touch it lands
on a level whose resting size is unobserved and **cannot fill** — this
under-counts fills and never invents one. And 24.3% of BBO rows lack a touch
size; those sides are **removed** from the published book rather than carried
forward, because a stale size is precisely the free-queue-priority error the
rule exists to prevent.

## First defensible fills — BTC only

The adapter emits the recorder's own schema, so `gemini_twosided_sim.py` ran
**unchanged**. 1,390 (symbol × q × withdrawal) rows; **300 produced at least
one defensible fill**, 280 of them BTC (20 were ETH and are excluded).

**Only 98 of 280 BTC rows are interpretable as P&L** — the rest end with
residual the tape cannot prove was closed, and are excluded rather than
settled by assumption. That exclusion is 65% of the rows and is itself a
selection concern: rows that failed to close are disproportionately one-sided
fills, so the surviving 98 are biased toward windows where both legs traded.

| q | withdraw | n | matched/window | net after modeled comp (mean) | median |
|---|---|---|---|---|---|
| 5 | T−90 / T−60 | 12 | 2.8 | −$0.296 / −$0.238 | −$0.243 / −$0.190 |
| 10 | T−90 / T−60 | 12 | 4.4 | −$0.781 / −$0.665 | −$0.485 / −$0.381 |
| 25 | T−90 / T−60 | 10 | 10.3 | −$0.610 / −$0.261 | −$0.952 / −$0.767 |
| 50 | T−90 / T−60 | 7 / 8 | 7.6 / 12.9 | −$5.726 / −$4.637 | −$2.243 / −$1.873 |
| 100 | T−90 / T−60 | 7 / 8 | 14.8 / 25.4 | −$11.612 / −$9.414 | −$4.485 / −$3.093 |

**Negative at every size, and worse as size grows** — the signature of
adverse selection, not of a fee or a modelling constant.

### Per-fill markouts (BTC, residual-closed, per contract)

| horizon | mean | fills measured |
|---|---|---|
| 1s | **−0.01163** | 240 |
| 5s | **−0.02074** | 240 |
| 15s | **−0.01046** | 220 |
| 30s | +0.02980 | 191 |
| 60s | +0.08300 | 153 |

Adverse at 1–15 seconds, which is the horizon a maker is exposed over. The
positive 30s/60s figures are drift on a shrinking sample (153 of 240 fills
survive to 60s) and should not be read as the maker's edge.

## A double-count in my own simulator — corrected, and it mattered

`simulate()` closes residual **at the opposite touch** (`cash += bb*qty` for a
long), which already pays the spread, and **then** subtracts another
`(ba-bb)/2*qty` as `taker_cleanup_cost`. The crossing cost is charged twice.

| | as reported | corrected |
|---|---|---|
| net before compensation | −$284.18 | **−$99.19** |
| net after modeled compensation | −$266.26 | **−$81.27** |
| net per matched share | −$0.3157 | **−$0.0964** |
| windows with positive net | 31/98 | **57/98** |

**The uncorrected figure overstated the loss by 3.3×.** The verdict does not
change — it is still negative — but reporting −31.6¢/share when the arithmetic
says −9.6¢ would have killed the hypothesis for the wrong reason. The
simulator is left unmodified per instruction; the correction is applied in the
reported arithmetic and the one-line fix is flagged for approval.

### Decomposition, corrected, over the 98 interpretable rows

    captured spread (both legs only)   +$132.57
    modeled rebate (MODELED, not real)  +$17.92
    everything else (adverse selection
      and residual closing)            -$231.76
    ------------------------------------------
    net after modeled compensation      -$81.27   over 843 matched shares
                                      = -$0.0964 per matched share

**Spread capture is real and substantial.** It is simply not large enough:
adverse selection plus the cost of closing residual exceeds it by about 1.7×.

## Against the target

$3/window at the observed **−9.6¢** per matched share is unreachable at any
size — the sign is wrong. For reference, it would need **+13¢/share at 23
matched shares**, and the only configuration reaching 23 matched (q100, T−60,
25.4) is the **most** negative at −$9.41/window.

**This is an interim read on 98 windows from 2026-08-30/09-01 with a 65%
exclusion rate, not a verdict.** The prospective collectors keep running, and
the next checkpoint merges both samples under the same rules.
