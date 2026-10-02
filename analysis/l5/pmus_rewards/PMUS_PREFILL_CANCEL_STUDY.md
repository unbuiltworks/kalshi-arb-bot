# PMUS pre-fill cancellability diagnostic

Script: `scripts/pmus_prefill_cancel_study.py`
Raw output: `analysis/l5/pmus_rewards/PMUS_PREFILL_CANCEL_STUDY.json`
Sample: the 6 complete authenticated PMUS BTC15m windows 1045z–1200z, 2026-10-02.
First legs 236 — **19 TOXIC (FORCED)**, **217 QUALIFYING**.

Diagnostic only. No thresholds fitted, no strategy change, no orders.

## 1. Feasibility, established before interpreting anything

**Timestamp resolution (sufficient).** PMUS `t` is microsecond receipt time; `trade_time`
is nanosecond venue time. Book inter-arrival p10 0.098s / p50 0.101s / p90 0.200s (~10 Hz);
trade inter-arrival p50 0.103s. A 100ms horizon therefore spans roughly one book update —
it is at the edge of the data's own resolution, not inside it.

**External BTC reference: UNAVAILABLE.** The only BTC feed in the repo is
`data/btc15m/coinbase_1m.jsonl` — 1-minute candles, 60,000ms, one to three orders of
magnitude coarser than every requested horizon (100ms–5s). The external-BTC leg of the
question **cannot be run on existing data** and no proxy was substituted. Every result
below uses PMUS-internal observables only.

**Cancel latency (measured, not assumed).** 4,567 in-repo RTT samples: min 0.187s,
p50 **0.266s**, p90 **0.612s**. Horizons are therefore gated by construction:

| horizon | actionability |
|---|---|
| 100ms | NOT_ACTIONABLE (below min RTT) |
| 250ms | NOT_ACTIONABLE (below p50) |
| 500ms | OPTIMISTIC_ONLY (between p50 and p90) |
| 1s / 2s / 5s | ACTIONABLE |

A signal that only separates at 100–250ms is not a cancellation signal for us, however
strong it looks.

## 2. Separation, sigma = (TOX mean − QUAL mean) / pooled SE

| feature | 0.1s | 0.25s | 0.5s | 1s | 2s | 5s |
|---|---|---|---|---|---|---|
| queue_depletion | −0.54 | −0.84 | −0.65 | **−3.73** | **−3.59** | **−3.65** |
| spread_change | −2.08 | −2.18 | +0.46 | −1.39 | −1.69 | −1.47 |
| adverse_mid_move | −1.33 | −1.08 | −1.29 | −0.28 | −0.34 | −1.67 |
| adverse_flow | +1.49 | +1.54 | +1.58 | +1.52 | +1.70 | +1.79 |
| quote_staleness | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

`sweep_seen` (share of lots with a sweep in the window), TOX vs QUAL:
42.1/27.6 (0.1s) · 52.6/32.3 (0.25s) · 57.9/36.9 (0.5s) · 57.9/43.8 (1s) ·
57.9/50.2 (2s) · 57.9/**62.2** (5s).

## 3. Answer to the primary question

> What fraction of subsequently toxic fills had a detectable adverse pre-fill signal
> early enough for an order already resting on PMUS to be cancelled?

Restricted to ACTIONABLE horizons, exactly **one** feature separates materially:
**queue_depletion at h ≥ 1s** (TOX −3859 vs QUAL −1264 at 1s, −3.73 sigma; the same
at 2s and 5s). Mechanically that is the toxic fill being front-run by size leaving the
level ahead of us — our queue position collapsing about a second before we trade.
`sweep_seen` reaches 57.9% of toxic lots at h = 1s, but 43.8% of qualifying lots carry
the same mark, and by 5s the ordering **inverts** (57.9% TOX vs 62.2% QUAL), so sweep
occurrence is not a usable discriminator at actionable range.

Nothing else is both strong and actionable:

- `spread_change` is the second-strongest separator (−2.08/−2.18 sigma) but **only at
  100ms and 250ms**, i.e. below our own p50 round trip. It is real and unusable.
- `adverse_mid_move` is weak at every horizon (−0.28 at 1s).
- `adverse_flow` has the wrong sign for a danger signal and never reaches significance.
- `quote_staleness` is identically 0.0000 at every horizon. This is an **artifact of the
  frozen policy**, which re-places the entry quote at the touch on every book event, so
  it can never be stale. It is not a market finding and must not be read as one.

**Cost side.** No cutoff was fitted, so no exact good-fill removal count is claimed. The
distributions overlap heavily: at h = 1s the qualifying mean queue depletion is −1264
against the toxic −3859, and 217 of 236 lots are qualifying, so any depletion cutoff
severe enough to catch most of the 19 toxic lots removes a substantial, double-digit
number of qualifying fills. Quantifying that requires the cutoff this study is forbidden
to fit.

## 4. Limits

- **n = 19 toxic lots.** Far too thin to support a rule, however large the sigma. A −3.73
  sigma on 19 observations against 217 is a lead, not a finding.
- The external-BTC leg was not run at all; the strongest candidate signal
  (external move vs our stale quote) remains untested for want of a sub-minute feed.
- Six windows, one instrument family, one morning. Window-to-window variance has
  dominated every prior effect in this program.

**No cancellation rule is proposed and no threshold is selected.**
