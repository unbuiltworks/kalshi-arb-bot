# Gate PASSES: 1730z and 1745z, first two windows under the fixed recorder

## Serving boundary, from recorder evidence

Production commit `ddc2195c` on `feature/l5-transfer-backtest` triggered Railway
deployment **`ed464f2d`** (created 17:12:51Z, SUCCESS). The boundary was taken from
the recorder's own output, not from deployment status: `PMUS_WS_CONN` and
`PMUS_WS_STATUS` show it connected, authenticated and emitting **by 17:18:31Z**.
`cap_per_market` still reads **400**; cadence p50 still **5.034 s**.

The first two BTC15m windows recorded entirely after that boundary are **1730z** and
**1745z**, verified from row timestamps. 1715z crossed the boundary and is excluded.
Everything through 1615z remains recorder-validation only.

## The eight criteria, unchanged and unrelaxed

| # | criterion | 1730z | 1745z |
|---|---|---|---|
| 1 | zero rows lost to cap | **PASS** — 0 | **PASS** — 0 |
| 2 | zero emissions at 400 | **PASS** — `market_emissions_at_cap: 0` | **PASS** |
| 3 | per-emission `Δbook_updates − emitted = 0` | **PASS** | **PASS** |
| 4 | ≥600 s coverage | **PASS** — 911.6 s, 8,516 rows | **PASS** — 909.6 s, 7,396 rows |
| 5 | counter continuity | **PASS** — `book_updates` monotonic, no regression | **PASS** |
| 6 | BTC reference brackets whole window | **PASS** — 9,421 in-window rows | **PASS** — 5,976 |
| 7 | no BTC sequence loss / restart inside window | **PASS** — seq 3858–4340, 483 lines, **0 missing**, 0 sessions, 0 errors | **PASS** |
| 8 | residual gaps not batching/cap-attributable | **PASS** — see below | **PASS** |

## The two new diagnostics

```
duplicate_previously_emitted_book_rows  = 0      (was 2,730)
duplicate_previously_emitted_trade_rows = 0      (was 349)
```

Both zero on the first windows after the fix, against non-zero on the last window
before it. That is the fix working on both streams.

## The unique-rows invariant

```
rows_emitted_unique               16,921
rows_generated_unique             16,818
first_emission_rows_not_in_generated 103
invariant_emitted_unique_le_generated_unique  TRUE
```

16,921 ≤ 16,818 + 103. The 103-row excess is a **window-boundary artifact**:
`generated` sums *consecutive* `book_updates` deltas, so it cannot account for each
slug's first emission — there is no prior value to difference against. It is not a
duplicate allowance; duplicates are counted separately and are zero. Before the fix
the same comparison failed by 116 rows **beyond** this allowance, which was the
duplicate re-emission.

## Criterion 8: what the residual gaps actually are

1730z has 9 gaps > 2 s, 1745z has 12. Their timing is the answer:

```
17:39:53  17:40:40  17:41:28  17:42:15  17:43:03  17:43:51  17:44:39
```

Spacing ≈ **47.5 s**, each gap 2.3–3.7 s. That matches the recorder's own
`PMUS_WS_CONN` cycle exactly — `{"kind":"disconnect","reason":"rediscover",
"duration_s":45.1}` followed by a reconnect ~0.6–2.5 s later. These are
**subscription-cycle gaps**: pre-existing recorder behaviour, unrelated to batching,
to the cap, or to this fix.

The two larger gaps sit precisely on window boundaries — 11.642 s from 17:45:00.748
and 9.576 s from 18:00:00.776 — i.e. **window rollover**, as the expiring market
hands over to the next. Also not batching or cap behaviour.

Gap counts fell from 18 per window (cap 200 and cap 400 at 10 s) to 9 and 12.

## Other requested figures

| | before (cap 400, 10 s) | after (fixed, 5 s) |
|---|---|---|
| max rows per emission | **400** (duplicate bursts) | **53** |
| max document bytes | 56,855 — **69.4%** of 81,920 | **8,019 — 9.8%** |
| emission cadence p50 | 10.042 s | 5.034 s |
| book documents | 193 / 17 min | 377 / 32 min |
| trade documents / rows | 220 / 12,104 | 369 / **25,407** |

Trade collection changed in volume, not in kind: schema and fields are identical and
duplicates are now zero, so the higher row count is market activity plus the doubled
document rate, **not** re-emission. The byte-ceiling risk is gone — documents now sit
at under 10% of the limit rather than 69.4%.

## Status

Gate passed on both windows with nothing relaxed. The frozen synchronized study
(`pmus_prefill_realtime_study.py`, `pmus_btcref_sync.py`, unmodified) is being run on
these two windows together, with unchanged `82714061` scoring alongside. 1300z remains
frozen discovery; 1430z, 1445z, 1515z, 1530z, 1615z and 1715z remain
recorder-validation only. No live orders.
