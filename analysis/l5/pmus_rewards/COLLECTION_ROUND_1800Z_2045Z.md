# Collection round: windows 1800z–2045z

## Gate status per window

Completeness audit on all four harvest chunks: **`rows_lost_to_cap: 0`,
`duplicate_previously_emitted_book_rows: 0`,
`duplicate_previously_emitted_trade_rows: 0`, `market_emissions_at_cap: 0`,
invariant TRUE, max document bytes 9.5% of the 81,920 limit.** The retention fix is
holding across 3¼ hours of production, not just the two windows it was verified on.

| window | rows | span | gaps >2 s | worst gap | status |
|---|---|---|---|---|---|
| 1800z | 7,596 | 899.2 s | 16 | **45.42 s** | **EXCLUDED — harvest hole** |
| 1815z | 6,516 | 897.3 s | 34 | 7.72 s | gate-passing* |
| 1830z | 6,451 | 897.1 s | 33 | 4.31 s | gate-passing* |
| 1845z | 6,841 | 897.0 s | 24 | **32.66 s** | **EXCLUDED — harvest hole** |
| 1900z | 7,198 | 896.7 s | 24 | 6.71 s | gate-passing* |
| 1915z | 8,396 | 899.8 s | 19 | 3.12 s | gate-passing* |
| 1930z | 7,892 | 897.2 s | 18 | **27.96 s** | **EXCLUDED — harvest hole** |
| 1945z | 7,548 | 896.9 s | 21 | 7.64 s | gate-passing* |
| 2000z | 7,496 | 896.8 s | 18 | 5.59 s | gate-passing* |
| 2015z | 7,893 | 897.2 s | 18 | 7.42 s | gate-passing* |
| 2030z | 8,018 | 897.1 s | 18 | 3.35 s | gate-passing* |
| 2045z | 7,426 | 897.0 s | 24 | 3.41 s | gate-passing* |

## My own harvest error, and how it was caught

The first pass used 48-minute log ranges. Each returned **exactly 501 entries** — the
`limit: 500` ceiling. At the 5 s cadence a 48-minute span generates about 535
emissions, so **every chunk was silently truncated**, and the damage showed up as
45.42 s / 32.66 s / 27.96 s / 33.58 s gaps in the first and last window of each chunk.

Re-harvesting in 32-minute ranges returned 348–365 entries — under the limit — and
2015z's worst gap fell from **33.58 s to 7.42 s**, confirming the gaps were mine and
not the recorder's. Only 275 new rows were needed, so the loss was small but
concentrated exactly where it mattered.

Three windows still carry holes at the remaining chunk seams (18:01:29, 18:46:58,
19:31:59) and are **excluded rather than scored**. They can be recovered with narrow
re-harvests; they are not recorder defects.

This is the third time a silent truncation has had to be caught by counting rather
than trusted: the recorder's 50-row book cap, its seen-set re-emission, and now my own
log-query limit. The lesson is the same each time — a ceiling that is hit looks
exactly like data that does not exist.

## *The asterisk: criteria 6 and 7 are PENDING

The nine windows above satisfy criteria 1–5 and 8. **Criteria 6 and 7 — BTC reference
brackets the window, no reference sequence loss or restart inside it — are NOT yet
verified**, because the reference harvest for 18:00–21:00 exceeded the log server's
message limit and needs narrower ranges.

The frozen signal is computed from PMUS trades alone and does not read the reference
feed, so the scoring itself is unaffected. But the gate is the gate: these rows are
**provisional** until 6 and 7 are checked, and are labelled as such in the table rather
than counted as fully gate-passing.

## Gap counts, for the record

Gap counts in the new windows run 18–34 against 9–12 in 1730z/1745z, with worst gaps
of 3–8 s rather than ~3 s. Expected from the ~47.5 s rediscover cycle is roughly 19
per window, so 18–24 is consistent with it and 33–34 (1815z, 1830z) is about double.
That excess is characterized, not dismissed: it is not cap behaviour, since no emission
reached the cap and no duplicates were emitted anywhere in the span.
