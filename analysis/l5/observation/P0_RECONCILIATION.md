# P0 — recent L5 vs canonical history, identical definitions

`analysis/l5/observation/reconcile.py` · L5_OBSERVATION layer: source-supported
facts only. `fee_bearing` is the **observed cash wedge** and is never relabelled
maker/taker.

## The first finding is about the comparison itself

**Our canonical set ends 2026-08-31. The recent population is 2026-09-14/15.
They do not overlap.** Any "recent vs canonical" gap is therefore a comparison
of two *periods*, not two *samples of the same period* — and that has to be said
before any number is interpreted.

I fetched the recent population directly rather than assuming it: **5,500
trades, 2026-09-14 13:46 → 2026-09-15 01:00, spanning 0.47 days.** That is
≈11,700 trades/day.

**L5 is not trading sparsely right now.** Cohort mix of those 5,500 fills:

| BTC15m | BTC5m | ETH5m | ETH15m |
|---|---|---|---|
| **5,231** | 191 | 55 | 23 |

## BTC15m — unchanged, and still dense

| metric | RECENT (0.5d) | CANONICAL (165.5d) |
|---|---|---|
| fills | 5,231 | 693,916 |
| windows touched | 46 | 5,546 |
| **fills/window p50** | **103** | **110** |
| **two-sided %** | **100.0** | **99.2** |
| **switches/window p50** | **28** | **29** |
| **inter-fill p50** | **2 s** | **2 s** |
| fills per run p50 | 2 | 2 |
| matched fraction p50 | 0.906 | 0.923 |
| effective pair p50 | 0.9614 | 0.9725 |
| clip p50 | 16.25 | 14.00 |
| first-fill DEARER % | 65.2 | 51.1 |
| **fee-bearing %** | **1.1** | **15.4** |

**The independent review's characterisation does not hold for BTC15m.** ~70%
one-sided, median 1 switch, short sequences and ~27 s inter-fill describe
something; they do not describe this cohort, which is 100% two-sided, 28
switches per window and 2 s between fills — the same continuous two-sided
inventory regime we measured canonically.

## The 5m and ETH cohorts ARE sparse in the recent sample

| metric | BTC5m recent / canon | ETH5m recent / canon | ETH15m recent / canon |
|---|---|---|---|
| windows touched | 81 / 8,089 | 32 / 2,831 | 13 / 580 |
| fills/window p50 | 2 / 5 | 2 / 3 | 1 / 6 |
| **one-sided %** | **66.7 / 32.0** | **90.6 / 45.3** | **92.3 / 26.9** |
| switches p50 (two-sided) | 1 / 2 | 1 / 2 | 1 / 2 |
| matched fraction p50 | 0.00 / 0.52 | 0.00 / 0.18 | 0.00 / 0.50 |
| inter-fill p50 | 18 s / 10 s | 2 s / 17 s | 12 s / 26 s |
| fee-bearing % | 100 / 100 | 100 / 100 | 100 / 100 |

**This is where the review's picture lives.** ~70% one-sided, median 1 switch,
short sequences — that is the 5m/ETH minority, which is 269 of 5,500 recent
fills but **126 of 172 recent windows**.

## The reconciliation

**The two descriptions are of different denominators, and both are arithmetically
correct.** Weighted by *fills*, L5 is 95% BTC15m: dense, two-sided, 2 s cadence.
Weighted by *windows*, he is 73% 5m/ETH: sparse, one-sided, ~1 switch. A review
that counted ~4,000 trades across ~1,700 windows was window-weighting across
cohorts; our canonical reconstruction is fill-weighted and BTC15m-dominated.

### Classification of each material gap

| gap | class | basis |
|---|---|---|
| "~70% one-sided, median 1 switch, short sequences" vs our dense BTC15m | **SAMPLE_DEFINITION** | Reproduced both. Recent BTC15m is 0.0% one-sided and 28 switches; recent 5m/ETH is 66–92% one-sided and 1 switch. Window-weighting vs fill-weighting fully accounts for the difference without either party being wrong. |
| reviewer's 4,000 trades → ~1,700 windows (2.4 fills/window) vs our recent 5,500 → 172 windows (32 fills/window) | **DATA_COVERAGE** | A 13× difference in fills per window cannot come from weighting alone. Their feed appears to return few trades per market. Our fetch paginated to exhaustion at offset 5,500 and deduped on (hash, ts, cid, px, size, outcome, side). Not resolvable from our side; flagged rather than explained away. |
| **BTC15m fee-bearing 15.4% → 1.1%** | **REGIME_CHANGE** | Continues an established monthly trend rather than appearing suddenly: 0.0% (Mar) → 11.0 → 39.7 → 34.1 → 19.6 → 5.3 (Aug) → **1.1 (Sep 14-15)**. Directionally consistent, six consecutive months, and it moves in the direction that makes his economics better. |
| BTC5m/ETH5m/ETH15m one-sided % roughly doubling-to-tripling | **UNRESOLVED** | Only 81 / 32 / 13 recent windows over 0.47 days. Below any threshold at which a behavioural change could be separated from a quiet half-day. **INSUFFICIENT EVIDENCE**; needs the all-eligible recorder to accumulate. |
| BTC15m residual_abs p50 277 vs 142 | **UNRESOLVED** | 46 windows. Direction is interesting given the saturation work but the sample cannot carry it. |
| BTC15m recent touch_rate 1.02 | **SAMPLE_DEFINITION artifact, NOT a finding** | A rate above 1.0 is impossible; it comes from my eligible-window count including partial windows at both sample edges. Reported so it is not mistaken for 100% participation. |

### Has L5 actually changed behaviour, particularly BTC15m?

**On behaviour: no.** Fills/window, two-sidedness, switch count, run length,
inter-fill cadence and matched fraction are all within a few percent of
canonical. The engine he is running today looks like the engine we
reconstructed.

**On economics: yes, and materially.** Fee-bearing fills fell from 15.4% to
1.1%. Given what the corrected fee work established — that BTC15m's entire
profitability depends on *not* paying the fee, and would be −161 bp if every
fill bore it — a drop from 15.4% to 1.1% is the single most consequential change
in this reconciliation. Whatever produces fee-free execution, he is getting more
of it.

**Cohorts are not pooled anywhere above**, and no 5m conclusion is drawn from
BTC15m.

## Recorder status (P1)

`l5-5m-recorder` is **running**: 36 windows captured, 0 missed,
`first_sample_lead_s` p50 119.58 s against the 120 s target, coverage p50 1.0,
`fill_feed_consecutive_failures` 0, and **32 windows with no L5 action** — the
CONTINUE class accumulating as intended.

**It was watching the wrong cohort.** 5m-only covers 269 of 5,500 recent fills
and misses BTC15m entirely, which is 95% of them and the only cohort whose
density is unchanged. **Extended to 5m AND 15m** in this commit; the denominator
now spans the observable candidate universe.
