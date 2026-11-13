# Gemini BTC15m maker pair-making WITHOUT rebates — REPLAY result

**Label: REPLAY on recorded books. Every fill is hypothetical. Nothing here is realized.**
Date: 2026-11-13. Read-only study: no orders, no deployment or service-variable change, no commit.

## Verdict

**NEGATIVE, with a small sample.** No configuration (4 depths × 5 targets × 3 completion modes × 3 fee assumptions × 3 samples) has a 95% interval above zero.
On the 26 windows whose recording reaches the completion cut with no gap (END_COVERED, the primary sample), **every one of the 60 configurations has a negative mean, and 50 of 60 have an interval wholly below zero**, at zero fees.
Gemini's BTC15m spread is about 10x wider than PMUS (median 0.11 vs 0.01), and the pairs that complete are cheap (mean cost 0.80–0.90). But the leg that fills alone is adversely selected and loses about $0.25–0.42 per lone leg, which outweighs the pair profit.

## Method

`gemini_btc15m_pair_gate_measure.py` (in this folder). It is the PMUS script `analysis/l5/btc15m_pair_gate_measure.py` with only the loader replaced and a fee line added per filled leg:
- same policy (UP bid = yes_bid − d ticks, DOWN bid = 1 − yes_ask − d ticks, open both only if UP+DOWN ≤ T, one pair at a time);
- same cuts (entry < 600 s, completion until 870 s), same `through`/`touch` fill rules, same CHASE-to-0.99;
- same window-clustered 95% interval.

The fee-ZERO path is asserted equal to the untouched PMUS `sim()` on every window. Tick = 0.01, which is Gemini's tick for these contracts (`"tick": "0.01"` in `analysis/gemini/btc15m_v1/continuous_v1.jsonl`).

No lookahead. Each decision reads only the current row. Fills come only from later rows. The winner is read only to settle a lone leg.
Window = [expiry − 900 s, expiry], with the expiry parsed from the symbol. Rows from before the window start are dropped; Gemini lists the contract early with a 0.48/0.50 placeholder book.

## Data

| what | source | notes |
|---|---|---|
| book (WS best bid/ask) | `data/ref_lag/reflag_*.jsonl` (`kind=pbook`) | event-driven, about 37k in-window rows across 54 windows |
| book (polled ~5 s) | `data/policy_d_port*/tape_*.jsonl` (`venue=gemini kind=bbo`) | 69 windows |
| book (polled ~5 s) | `data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz` | 62 windows |
| settled outcome | `data/ref_lag/reflag_*.jsonl` (`kind=settlement`) | the venue-published `resolution_side`; 155 BTC15m settlements |

- **86 windows** have in-window book rows. **83** of them have a venue outcome (41 UP / 42 DOWN). 3 have no outcome and are excluded, not guessed: `GEMI-BTC15M2609011415-UP`, `2609021215`, `2609021345`.
- **Date range:** 2026-08-30T23:15Z to 2026-09-01T14:00Z, about 1.6 days. Our own Gemini live trading started on 2026-09-17, so none of our orders were in these books.
- **Coverage is the main weakness.** The recorders ran in bounded sessions, so many windows are partial:
  - **ALL** = all 83 windows. Many recordings stop before 870 s. That makes lone legs look like truncation artefacts, and the windows that are not observed contribute 0 rather than activity. Use this sample for the spread only.
  - **END_COVERED** = 26 windows whose recording reaches ≥ 870 s with no gap > 30 s. A lone leg here is real, not an artefact. **This is the primary sample.**
  - **FULL_COVERAGE** = 7 windows that are also observed from ≤ 60 s.
- Other recorded Gemini BTC15m books were found and **not used** in the main measurement: the 2026-09-22 cohort runs (`analysis/gemini/cohort_run_20260922T*.json`, 15 windows, outcomes in `p7_bulk_tape.json`). Those were LIVE windows with our own quote resting at the touch, so the book is contaminated, and most recordings are truncated. Their spread before TTE 300 was median 0.08 / p90 0.09 (1,344 rows), which is consistent with the main sample.

## Spread (yes_ask − yes_bid), rows before 600 s, ALL sample

| | value |
|---|---|
| rows | 32,050 |
| median / p25 / p75 / p90 | **0.110** / 0.090 / 0.130 / **0.160** |
| share ≥ 0.05 / ≥ 0.10 | 0.984 / 0.712 |
| median of per-window medians / p90 | 0.070 / 0.150 |
| one-sided rows / empty rows | 2.0% / 5.8% |
| crossed rows | 0 |

The PMUS comparison is 0.01 at both median and p90. The spread hypothesis is confirmed: Gemini's books are much wider. That alone does not make the strategy profitable.

## Results

`pnl/w` is the dollars per window per 1-contract pair attempt, before rebates. A completed pair earns 1 − (UP+DOWN). A lone leg settles at (winner value − price). Fees are subtracted per filled leg.

**END_COVERED, fee ZERO**

| completion | rule | d | T | opens/w | pairs/w | lone/w | compl % | pair cost | pnl/w | 95% CI (window-clustered) |
|---|---|---|---|---|---|---|---|---|---|---|
| STATIC | through | 0 | 0.99 | 0.96 | 0.42 | 0.31 | 57.9 | 0.903 | -0.0808 | [-0.1445, -0.0170] |
| STATIC | through | 0 | 0.97 | 0.96 | 0.42 | 0.31 | 57.9 | 0.903 | -0.0808 | [-0.1445, -0.0170] |
| STATIC | through | 0 | 0.95 | 0.96 | 0.42 | 0.31 | 57.9 | 0.903 | -0.0808 | [-0.1445, -0.0170] |
| STATIC | through | 0 | 0.93 | 0.96 | 0.42 | 0.31 | 57.9 | 0.901 | -0.0800 | [-0.1440, -0.0160] |
| STATIC | through | 0 | 0.90 | 0.92 | 0.42 | 0.31 | 57.9 | 0.896 | -0.0804 | [-0.1471, -0.0136] |
| STATIC | through | 2 | 0.99 | 0.85 | 0.31 | 0.31 | 50.0 | 0.864 | -0.0796 | [-0.1500, -0.0092] |
| STATIC | through | 2 | 0.97 | 0.85 | 0.31 | 0.31 | 50.0 | 0.864 | -0.0796 | [-0.1500, -0.0092] |
| STATIC | through | 2 | 0.95 | 0.85 | 0.31 | 0.31 | 50.0 | 0.864 | -0.0796 | [-0.1500, -0.0092] |
| STATIC | through | 2 | 0.93 | 0.85 | 0.31 | 0.31 | 50.0 | 0.864 | -0.0796 | [-0.1500, -0.0092] |
| STATIC | through | 2 | 0.90 | 0.85 | 0.31 | 0.31 | 50.0 | 0.864 | -0.0796 | [-0.1500, -0.0092] |
| STATIC | through | 3 | 0.99 | 0.85 | 0.27 | 0.27 | 50.0 | 0.844 | -0.0719 | [-0.1390, -0.0049] |
| STATIC | through | 3 | 0.97 | 0.85 | 0.27 | 0.27 | 50.0 | 0.844 | -0.0719 | [-0.1390, -0.0049] |
| STATIC | through | 3 | 0.95 | 0.85 | 0.27 | 0.27 | 50.0 | 0.844 | -0.0719 | [-0.1390, -0.0049] |
| STATIC | through | 3 | 0.93 | 0.85 | 0.27 | 0.27 | 50.0 | 0.844 | -0.0719 | [-0.1390, -0.0049] |
| STATIC | through | 3 | 0.90 | 0.85 | 0.27 | 0.27 | 50.0 | 0.844 | -0.0719 | [-0.1390, -0.0049] |
| STATIC | through | 5 | 0.99 | 0.77 | 0.19 | 0.31 | 38.5 | 0.798 | -0.0369 | [-0.1020, +0.0281] |
| STATIC | through | 5 | 0.97 | 0.77 | 0.19 | 0.31 | 38.5 | 0.798 | -0.0369 | [-0.1020, +0.0281] |
| STATIC | through | 5 | 0.95 | 0.77 | 0.19 | 0.31 | 38.5 | 0.798 | -0.0369 | [-0.1020, +0.0281] |
| STATIC | through | 5 | 0.93 | 0.77 | 0.19 | 0.31 | 38.5 | 0.798 | -0.0369 | [-0.1020, +0.0281] |
| STATIC | through | 5 | 0.90 | 0.77 | 0.19 | 0.31 | 38.5 | 0.798 | -0.0369 | [-0.1020, +0.0281] |
| STATIC | touch | 0 | 0.99 | 1.00 | 0.50 | 0.31 | 61.9 | 0.905 | -0.0777 | [-0.1426, -0.0128] |
| STATIC | touch | 0 | 0.97 | 1.00 | 0.50 | 0.31 | 61.9 | 0.905 | -0.0777 | [-0.1426, -0.0128] |
| STATIC | touch | 0 | 0.95 | 1.00 | 0.50 | 0.31 | 61.9 | 0.905 | -0.0777 | [-0.1426, -0.0128] |
| STATIC | touch | 0 | 0.93 | 1.04 | 0.54 | 0.31 | 63.6 | 0.901 | -0.0719 | [-0.1377, -0.0061] |
| STATIC | touch | 0 | 0.90 | 0.92 | 0.42 | 0.31 | 57.9 | 0.895 | -0.0792 | [-0.1453, -0.0132] |
| STATIC | touch | 2 | 0.99 | 0.88 | 0.35 | 0.31 | 52.9 | 0.863 | -0.0662 | [-0.1298, -0.0025] |
| STATIC | touch | 2 | 0.97 | 0.88 | 0.35 | 0.31 | 52.9 | 0.863 | -0.0662 | [-0.1298, -0.0025] |
| STATIC | touch | 2 | 0.95 | 0.88 | 0.35 | 0.31 | 52.9 | 0.863 | -0.0662 | [-0.1298, -0.0025] |
| STATIC | touch | 2 | 0.93 | 0.88 | 0.35 | 0.31 | 52.9 | 0.863 | -0.0662 | [-0.1298, -0.0025] |
| STATIC | touch | 2 | 0.90 | 0.88 | 0.35 | 0.31 | 52.9 | 0.863 | -0.0662 | [-0.1298, -0.0025] |
| STATIC | touch | 3 | 0.99 | 0.85 | 0.31 | 0.31 | 50.0 | 0.844 | -0.0704 | [-0.1389, -0.0019] |
| STATIC | touch | 3 | 0.97 | 0.85 | 0.31 | 0.31 | 50.0 | 0.844 | -0.0704 | [-0.1389, -0.0019] |
| STATIC | touch | 3 | 0.95 | 0.85 | 0.31 | 0.31 | 50.0 | 0.844 | -0.0704 | [-0.1389, -0.0019] |
| STATIC | touch | 3 | 0.93 | 0.85 | 0.31 | 0.31 | 50.0 | 0.844 | -0.0704 | [-0.1389, -0.0019] |
| STATIC | touch | 3 | 0.90 | 0.85 | 0.31 | 0.31 | 50.0 | 0.844 | -0.0704 | [-0.1389, -0.0019] |
| STATIC | touch | 5 | 0.99 | 0.81 | 0.27 | 0.27 | 50.0 | 0.804 | -0.0562 | [-0.1195, +0.0072] |
| STATIC | touch | 5 | 0.97 | 0.81 | 0.27 | 0.27 | 50.0 | 0.804 | -0.0562 | [-0.1195, +0.0072] |
| STATIC | touch | 5 | 0.95 | 0.81 | 0.27 | 0.27 | 50.0 | 0.804 | -0.0562 | [-0.1195, +0.0072] |
| STATIC | touch | 5 | 0.93 | 0.81 | 0.27 | 0.27 | 50.0 | 0.804 | -0.0562 | [-0.1195, +0.0072] |
| STATIC | touch | 5 | 0.90 | 0.81 | 0.27 | 0.27 | 50.0 | 0.804 | -0.0562 | [-0.1195, +0.0072] |
| CHASE<=0.99 | through | 0 | 0.99 | 1.15 | 0.69 | 0.23 | 75.0 | 0.990 | -0.0785 | [-0.1424, -0.0145] |
| CHASE<=0.99 | through | 0 | 0.97 | 1.15 | 0.69 | 0.23 | 75.0 | 0.990 | -0.0785 | [-0.1424, -0.0145] |
| CHASE<=0.99 | through | 0 | 0.95 | 1.15 | 0.69 | 0.23 | 75.0 | 0.990 | -0.0785 | [-0.1424, -0.0145] |
| CHASE<=0.99 | through | 0 | 0.93 | 1.15 | 0.69 | 0.23 | 75.0 | 0.990 | -0.0785 | [-0.1424, -0.0145] |
| CHASE<=0.99 | through | 0 | 0.90 | 1.08 | 0.65 | 0.23 | 73.9 | 0.990 | -0.0812 | [-0.1475, -0.0148] |
| CHASE<=0.99 | through | 2 | 0.99 | 1.12 | 0.65 | 0.27 | 70.8 | 0.990 | -0.0869 | [-0.1488, -0.0251] |
| CHASE<=0.99 | through | 2 | 0.97 | 1.12 | 0.65 | 0.27 | 70.8 | 0.990 | -0.0869 | [-0.1488, -0.0251] |
| CHASE<=0.99 | through | 2 | 0.95 | 1.12 | 0.65 | 0.27 | 70.8 | 0.990 | -0.0869 | [-0.1488, -0.0251] |
| CHASE<=0.99 | through | 2 | 0.93 | 1.12 | 0.65 | 0.27 | 70.8 | 0.990 | -0.0869 | [-0.1488, -0.0251] |
| CHASE<=0.99 | through | 2 | 0.90 | 1.12 | 0.65 | 0.27 | 70.8 | 0.990 | -0.0869 | [-0.1488, -0.0251] |
| CHASE<=0.99 | through | 3 | 0.99 | 1.08 | 0.54 | 0.23 | 70.0 | 0.990 | -0.0842 | [-0.1481, -0.0203] |
| CHASE<=0.99 | through | 3 | 0.97 | 1.08 | 0.54 | 0.23 | 70.0 | 0.990 | -0.0842 | [-0.1481, -0.0203] |
| CHASE<=0.99 | through | 3 | 0.95 | 1.08 | 0.54 | 0.23 | 70.0 | 0.990 | -0.0842 | [-0.1481, -0.0203] |
| CHASE<=0.99 | through | 3 | 0.93 | 1.08 | 0.54 | 0.23 | 70.0 | 0.990 | -0.0842 | [-0.1481, -0.0203] |
| CHASE<=0.99 | through | 3 | 0.90 | 1.08 | 0.54 | 0.23 | 70.0 | 0.990 | -0.0842 | [-0.1481, -0.0203] |
| CHASE<=0.99 | through | 5 | 0.99 | 1.04 | 0.54 | 0.19 | 73.7 | 0.990 | -0.0646 | [-0.1225, -0.0067] |
| CHASE<=0.99 | through | 5 | 0.97 | 1.04 | 0.54 | 0.19 | 73.7 | 0.990 | -0.0646 | [-0.1225, -0.0067] |
| CHASE<=0.99 | through | 5 | 0.95 | 1.04 | 0.54 | 0.19 | 73.7 | 0.990 | -0.0646 | [-0.1225, -0.0067] |
| CHASE<=0.99 | through | 5 | 0.93 | 1.04 | 0.54 | 0.19 | 73.7 | 0.990 | -0.0646 | [-0.1225, -0.0067] |
| CHASE<=0.99 | through | 5 | 0.90 | 1.04 | 0.54 | 0.19 | 73.7 | 0.990 | -0.0646 | [-0.1225, -0.0067] |

**ALL, fee ZERO**

| completion | rule | d | T | opens/w | pairs/w | lone/w | compl % | pair cost | pnl/w | 95% CI (window-clustered) |
|---|---|---|---|---|---|---|---|---|---|---|
| STATIC | through | 0 | 0.99 | 1.12 | 0.33 | 0.58 | 36.0 | 0.905 | -0.0484 | [-0.1176, +0.0207] |
| STATIC | through | 0 | 0.90 | 1.07 | 0.28 | 0.58 | 32.4 | 0.896 | -0.0295 | [-0.1015, +0.0425] |
| STATIC | through | 2 | 0.99 | 1.02 | 0.22 | 0.54 | 28.6 | 0.869 | -0.0408 | [-0.1107, +0.0290] |
| STATIC | through | 2 | 0.90 | 1.01 | 0.19 | 0.55 | 25.8 | 0.863 | -0.0341 | [-0.1061, +0.0380] |
| STATIC | through | 3 | 0.99 | 1.00 | 0.18 | 0.55 | 24.6 | 0.845 | -0.0199 | [-0.0920, +0.0523] |
| STATIC | through | 3 | 0.90 | 1.00 | 0.17 | 0.55 | 23.3 | 0.842 | -0.0189 | [-0.0920, +0.0542] |
| STATIC | through | 5 | 0.99 | 0.94 | 0.13 | 0.57 | 19.0 | 0.804 | +0.0155 | [-0.0568, +0.0879] |
| STATIC | through | 5 | 0.90 | 0.94 | 0.13 | 0.57 | 19.0 | 0.804 | +0.0155 | [-0.0568, +0.0879] |
| STATIC | touch | 0 | 0.99 | 1.16 | 0.37 | 0.59 | 38.8 | 0.907 | -0.0498 | [-0.1201, +0.0206] |
| STATIC | touch | 0 | 0.90 | 1.08 | 0.29 | 0.59 | 32.9 | 0.895 | -0.0237 | [-0.0964, +0.0489] |
| STATIC | touch | 2 | 0.99 | 1.07 | 0.27 | 0.57 | 31.9 | 0.868 | -0.0302 | [-0.0994, +0.0389] |
| STATIC | touch | 2 | 0.90 | 1.06 | 0.24 | 0.58 | 29.4 | 0.862 | -0.0240 | [-0.0954, +0.0474] |
| STATIC | touch | 3 | 0.99 | 1.02 | 0.22 | 0.54 | 28.6 | 0.849 | -0.0311 | [-0.1009, +0.0387] |
| STATIC | touch | 3 | 0.90 | 1.01 | 0.19 | 0.55 | 25.8 | 0.843 | -0.0247 | [-0.0967, +0.0473] |
| STATIC | touch | 5 | 0.99 | 0.98 | 0.18 | 0.55 | 24.6 | 0.805 | -0.0010 | [-0.0736, +0.0717] |
| STATIC | touch | 5 | 0.90 | 0.98 | 0.18 | 0.55 | 24.6 | 0.805 | -0.0010 | [-0.0736, +0.0717] |
| CHASE<=0.99 | through | 0 | 0.99 | 1.29 | 0.51 | 0.55 | 47.7 | 0.990 | -0.0676 | [-0.1359, +0.0007] |
| CHASE<=0.99 | through | 0 | 0.90 | 1.25 | 0.48 | 0.51 | 48.8 | 0.990 | -0.0386 | [-0.1052, +0.0281] |
| CHASE<=0.99 | through | 2 | 0.99 | 1.24 | 0.46 | 0.48 | 48.7 | 0.990 | -0.0439 | [-0.1070, +0.0193] |
| CHASE<=0.99 | through | 2 | 0.90 | 1.24 | 0.46 | 0.48 | 48.7 | 0.990 | -0.0386 | [-0.1021, +0.0250] |
| CHASE<=0.99 | through | 3 | 0.99 | 1.22 | 0.41 | 0.47 | 46.6 | 0.990 | -0.0382 | [-0.1011, +0.0247] |
| CHASE<=0.99 | through | 3 | 0.90 | 1.22 | 0.41 | 0.47 | 46.6 | 0.990 | -0.0336 | [-0.0971, +0.0298] |
| CHASE<=0.99 | through | 5 | 0.99 | 1.13 | 0.35 | 0.45 | 43.9 | 0.990 | -0.0214 | [-0.0834, +0.0405] |
| CHASE<=0.99 | through | 5 | 0.90 | 1.13 | 0.35 | 0.45 | 43.9 | 0.990 | -0.0214 | [-0.0834, +0.0405] |

**FULL_COVERAGE, fee ZERO**

| completion | rule | d | T | opens/w | pairs/w | lone/w | compl % | pair cost | pnl/w | 95% CI (window-clustered) |
|---|---|---|---|---|---|---|---|---|---|---|
| STATIC | through | 0 | 0.99 | 2.00 | 1.00 | 1.00 | 50.0 | 0.900 | -0.3229 | [-0.3993, -0.2464] |
| STATIC | through | 2 | 0.99 | 1.57 | 0.57 | 1.00 | 36.4 | 0.860 | -0.3443 | [-0.4323, -0.2563] |
| STATIC | through | 3 | 0.99 | 1.57 | 0.57 | 0.86 | 40.0 | 0.840 | -0.3057 | [-0.4209, -0.1905] |
| STATIC | through | 5 | 0.99 | 1.43 | 0.43 | 0.86 | 33.3 | 0.800 | -0.2257 | [-0.3433, -0.1081] |
| STATIC | touch | 0 | 0.99 | 2.14 | 1.14 | 1.00 | 53.3 | 0.904 | -0.3243 | [-0.4000, -0.2485] |
| STATIC | touch | 2 | 0.99 | 1.71 | 0.71 | 1.00 | 41.7 | 0.860 | -0.2943 | [-0.3883, -0.2003] |
| STATIC | touch | 3 | 0.99 | 1.57 | 0.57 | 1.00 | 36.4 | 0.840 | -0.3229 | [-0.4104, -0.2353] |
| STATIC | touch | 5 | 0.99 | 1.57 | 0.57 | 0.86 | 40.0 | 0.800 | -0.2671 | [-0.3769, -0.1574] |
| CHASE<=0.99 | through | 0 | 0.99 | 2.57 | 1.71 | 0.71 | 70.6 | 0.990 | -0.2700 | [-0.4296, -0.1104] |
| CHASE<=0.99 | through | 2 | 0.99 | 2.43 | 1.57 | 0.86 | 64.7 | 0.990 | -0.3043 | [-0.4245, -0.1841] |
| CHASE<=0.99 | through | 3 | 0.99 | 2.29 | 1.43 | 0.71 | 66.7 | 0.990 | -0.2929 | [-0.4383, -0.1474] |
| CHASE<=0.99 | through | 5 | 0.99 | 2.14 | 1.29 | 0.57 | 69.2 | 0.990 | -0.2243 | [-0.3826, -0.0660] |

**Fee sensitivity (best and worst pnl/w over all 60 configs per cell)**

| sample | fee | worst pnl/w | best pnl/w (config) | best CI | any CI lower bound > 0 |
|---|---|---|---|---|---|
| ALL | ZERO | -0.0676 | +0.0155 (STATIC through d=5 T=0.99) | [-0.0568, +0.0879] | no |
| ALL | MAKER_RAW | -0.0739 | +0.0123 (STATIC through d=5 T=0.99) | [-0.0599, +0.0846] | no |
| ALL | MAKER_CEIL1 | -0.0833 | +0.0072 (STATIC through d=5 T=0.99) | [-0.0649, +0.0794] | no |
| END_COVERED | ZERO | -0.0869 | -0.0369 (STATIC through d=5 T=0.99) | [-0.1020, +0.0281] | no |
| END_COVERED | MAKER_RAW | -0.0934 | -0.0396 (STATIC through d=5 T=0.99) | [-0.1050, +0.0257] | no |
| END_COVERED | MAKER_CEIL1 | -0.1027 | -0.0438 (STATIC through d=5 T=0.99) | [-0.1097, +0.0220] | no |
| FULL_COVERAGE | ZERO | -0.3443 | -0.2243 (CHASE through d=5 T=0.99) | [-0.3826, -0.0660] | no |
| FULL_COVERAGE | MAKER_RAW | -0.3531 | -0.2327 (STATIC through d=5 T=0.99) | [-0.3498, -0.1155] | no |
| FULL_COVERAGE | MAKER_CEIL1 | -0.3657 | -0.2429 (STATIC through d=5 T=0.99) | [-0.3592, -0.1265] | no |

**Decomposition, fee ZERO, rule through, T=0.99 (before fees)**

| sample | completion | d | matched-pair pnl/w | lone-leg pnl/w | pnl per lone leg |
|---|---|---|---|---|---|
| ALL | STATIC | 0 | +0.0310 | -0.0794 | -0.137 |
| ALL | STATIC | 2 | +0.0283 | -0.0692 | -0.128 |
| ALL | STATIC | 3 | +0.0280 | -0.0478 | -0.086 |
| ALL | STATIC | 5 | +0.0260 | -0.0105 | -0.019 |
| ALL | CHASE<=0.99 | 0 | +0.0051 | -0.0727 | -0.131 |
| ALL | CHASE<=0.99 | 2 | +0.0046 | -0.0484 | -0.101 |
| ALL | CHASE<=0.99 | 3 | +0.0041 | -0.0423 | -0.090 |
| ALL | CHASE<=0.99 | 5 | +0.0035 | -0.0249 | -0.056 |
| END_COVERED | STATIC | 0 | +0.0412 | -0.1219 | -0.396 |
| END_COVERED | STATIC | 2 | +0.0419 | -0.1215 | -0.395 |
| END_COVERED | STATIC | 3 | +0.0419 | -0.1138 | -0.423 |
| END_COVERED | STATIC | 5 | +0.0388 | -0.0758 | -0.246 |
| END_COVERED | CHASE<=0.99 | 0 | +0.0069 | -0.0854 | -0.370 |
| END_COVERED | CHASE<=0.99 | 2 | +0.0065 | -0.0935 | -0.347 |
| END_COVERED | CHASE<=0.99 | 3 | +0.0054 | -0.0896 | -0.388 |
| END_COVERED | CHASE<=0.99 | 5 | +0.0054 | -0.0700 | -0.364 |
| FULL_COVERAGE | STATIC | 0 | +0.1000 | -0.4229 | -0.423 |
| FULL_COVERAGE | STATIC | 2 | +0.0800 | -0.4243 | -0.424 |
| FULL_COVERAGE | STATIC | 3 | +0.0914 | -0.3971 | -0.463 |
| FULL_COVERAGE | STATIC | 5 | +0.0857 | -0.3114 | -0.363 |
| FULL_COVERAGE | CHASE<=0.99 | 0 | +0.0171 | -0.2871 | -0.402 |
| FULL_COVERAGE | CHASE<=0.99 | 2 | +0.0157 | -0.3200 | -0.373 |
| FULL_COVERAGE | CHASE<=0.99 | 3 | +0.0143 | -0.3071 | -0.430 |
| FULL_COVERAGE | CHASE<=0.99 | 5 | +0.0129 | -0.2371 | -0.415 |

The target T barely binds, because Gemini's wide spread puts most openings well under 0.90. The matched pairs that complete are profitable (+$0.04 per window static). The loss comes from lone legs, at −$0.25 to −$0.46 each in the covered samples: the leg that fills alone is the one the market moved through, and it expires worthless more often than not.
In the ALL sample the loss per lone leg looks milder (−$0.02 to −$0.14). That is the truncation artefact: legs that are "lone" only because the recorder stopped are not adversely selected.

## Fee assumption

Source: `analysis/gemini/MAKER_FEE_RESOLVED.md` and `MAKER_FEE_DISCREPANCY.md`.

- **ZERO**: the latest 18 maker fills (2026-09-22) were charged $0.00 according to two venue sources. This is the headline column.
- **MAKER_RAW**: 0.0175 × P × (1 − P) per contract, unrounded. This is the large-size limit.
- **MAKER_CEIL1**: the same, rounded UP to the next cent per 1-contract fill, which is $0.01 per fill. This rate was charged on this account on 2026-09-01/02, which is **inside this data period**.

Fees only make the result worse. Maker rebates and liquidity rewards are excluded by design.

## Netting: can a matched UP+DOWN pair be held on Gemini?

Source: `analysis/gemini/EVIDENCE_PACKET_003_VENUE_SEMANTICS.md`.

- Gemini publishes **one signed position per instrument**: positive = yes, negative = no (`positions/settled`, `positions@account`, changelog, Collateral Return worked example).
- Buying the opposite outcome **reduces, then flips** that position. It is **SUPPORTED** on venue schemas, but has never been observed on our own account.
- "Rejected" and "must cash out first" are **FALSIFIED**: a maker-or-cancel YES buy was accepted and filled while we held NO on the same instrument.
- So a matched UP+DOWN pair is **not held**. The second leg nets the first toward zero, and the account is left holding cash.

Effect on the pairing economics (EP003 §8, a **HYPOTHESIS**):
- If the reduce is booked at 1 − price, the pair P&L 1 − (UP+DOWN) is arithmetically identical. It is realized at completion instead of at settlement.
- The fee is symmetric in P, so fees are unchanged too.
- Netting therefore does **not** break the pair arithmetic in this replay. It does mean `Inventory(yes_qty, no_qty)`, gross spend caps and two-leg settlement accounting misdescribe the venue state.

The booking price of a buy-opposite reduce is **unverified**. The decisive read (`POST /v1/prediction-markets/positions/settled`, `withCashOuts=true`) has never been run.

Netting does not rescue the strategy either way. The loss comes from lone legs, which are single-signed positions that both models represent identically.

## Caveats

- 26 primary windows over about 1.6 days of late Aug / early Sep 2026 is a small sample. The regime may differ from today's Gemini book.
- The size column on the polled books is ignored. As in PMUS, there is no queue model: a through-fill is assumed to fill our 1 lot. That is optimistic.
- One pair at a time, 1 contract. No rebates or rewards. Nothing is realized.

## Files

- `gemini_btc15m_pair_gate_measure.py`: the script.
- `gemini_btc15m_pair_gate_measure.json`: every row (sample × fee × config) with its CI, decomposition and pnl per open.
- `run_output.txt`: the full console output.
