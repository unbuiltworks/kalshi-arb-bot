# Fresh-window verification after PMUS_WS_BOOK_LOG 50 -> 200

Window `cpc-btc-updown-15m-2026-10-02-1430z`, the first recorded entirely after the
change (recorder redeployed ~14:24Z). Audit: `scripts/pmus_book_completeness_audit.py`.

| # | check | result |
|---|---|---|
| 1 | zero cap-induced book loss | **PASS**, with a caveat below. `rows_lost_to_cap: 0`; per-emission `Δbook_updates − rows_emitted = 0` on **every** emission of all three markets in the span |
| 2 | sequence continuity | **PASS**. PMUS `book_updates` strictly monotonic, no regression. BTC reference seq 1420–1684, 265 lines, **0 missing** |
| 3 | full ≥600 s coverage | **PASS**. 1430z book span **915.9 s**; the reference spans 1,079.6 s and brackets 14:30:00–14:45:00 |
| 4 | book-gap distribution | **IMPROVED, not eliminated**. 1430z: **18** gaps >2 s (worst 10.092 s) against **87–95** per window before. 1445z so far: **1** gap, worst 2.559 s. Only 3 of the 18 end at an emission boundary, so most are venue-side quiet periods rather than emission artifacts |
| 5 | trade stream unchanged | **PASS**. 136 trade documents, 5,195 rows in span; 3,108 deduped trades in 1430z against 2,111 in 1300z — same order, no degradation |
| 6 | BTC-reference collection unchanged | **PASS**. 10,179 BBO rows, **0 gaps >2 s**, skew p50 0.0038 s / p90 0.0156 s, no restart since 12:56Z |

Book rows for 1430z: **8,377 emitted** (span 915.9 s) versus 4,126 in 1300z — roughly
double, which is what recovering the censored half looks like.

## The caveat, stated plainly

**Five emissions carried exactly 200 rows** — the new cap exactly. They are catch-up
emissions (each emitted *more* than its own `Δbook_updates`, so no loss is detectable
in them), and no emission anywhere shows positive loss. But a document landing exactly
on the cap is the same signature that the old 50 produced, so headroom on catch-up
emissions is thinner than the aggregate figure suggests.

Room to raise it exists if those recur: document size is now 34.8% of the
81,920-byte limit at its maximum (28,510 bytes), and the code hard-clamps the cap at
400. I have **not** raised it again — one change, verified, then stop.

## Status

Checks 1, 2, 3, 5 and 6 pass. Check 4 is an ~80% reduction in gaps rather than zero,
and the residual gaps are mostly not emission artifacts. The binding censorship is
gone: the study can resume on **new windows only**. 1300z stays frozen at `0d560681`
and is **not** rescored.
