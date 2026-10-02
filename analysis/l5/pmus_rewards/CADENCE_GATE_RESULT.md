# Cadence gate result: criterion 2 still fails — and the cause is now identified

Deployment `fa239ac6` SUCCESS, first emission observed 16:14:00Z in the harvested
span. Cadence confirmed changed: **p50 5.034 s** (was 10.042 s), p90 6.91 s, max
9.273 s. Cap unchanged at **400**.

The cadence change did exactly what it was predicted to do, and it was **not
sufficient**. Per the standing instruction I have stopped and made no further
recorder change.

## What changed

| | 10 s cadence | 5 s cadence |
|---|---|---|
| generated per market per emission | p50 99, max 110 | **p50 50, max 54** |
| typical rows per emission | ~99 | **p50 50, p90 51** |
| emission cadence | p50 10.042 s | **p50 5.034 s** |
| max document bytes | 56,849 (69.4%) | **56,855 (69.4%)** |
| emissions at 400 | 6 per window | **7 in 1615z, 8 in span** |

Typical emissions halved exactly as intended. The 400-row documents **did not go
away** and their byte size did not move at all — which is the tell.

## The actual mechanism: duplicate re-emission, not censorship

Every 400-row document is overwhelmingly **rows already emitted earlier**:

| emission | rows | already seen | genuinely new | span of rows |
|---|---|---|---|---|
| 16:22:24 | 400 | **350** | 50 | 84.3 s |
| 16:24:14 | 400 | **349** | 51 | 67.3 s |
| 16:25:45 | 400 | **350** | 50 | 59.7 s |
| 16:27:04 | 400 | **350** | 50 | 58.3 s |
| 16:28:12 | 400 | **351** | 49 | 49.8 s |
| 16:29:10 | 400 | **349** | 51 | 48.4 s |
| 16:29:58 | 400 | **365** | 35 | 49.5 s |

The cause is in `emit_new_books`:

```python
if len(seen) > 4000:
    _BOOK_LOG_SEEN[slug] = set(list(seen)[-2000:])
```

`seen` is a **set**, so `list(seen)[-2000:]` keeps 2,000 arbitrary elements rather
than the most recent ones. Rows evicted from that set are no longer recognised as
emitted, so on the next cycle they re-enter `fresh` and are emitted again — a burst
that then hits `fresh[-cap:]` and reports exactly 400.

This explains every observation at once: `rows_emitted` (12,042) exceeding
`rows_generated` (9,140); the periodic exactly-400 documents surviving a halved
cadence; their byte size being identical at both cadences; and the earlier
duplicate-inflated row counts.

**No information is lost.** `fresh[-cap:]` keeps the *newest* 400, and the genuinely
new rows are the newest, so they always survive — which is why criteria 1 and 3 pass
cleanly (`rows_lost_to_cap = 0`, per-emission `Δbook_updates − emitted = 0`
everywhere). The cap is binding on **re-emitted duplicates**, not on new states.

## Gate result, 1615z (the only complete post-boundary window)

| # | criterion | result |
|---|---|---|
| 1 | zero rows lost to cap | **PASS** (0) |
| 2 | zero emissions at 400 | **FAIL** — 7 emissions, all duplicate bursts |
| 3 | `Δbook_updates − emitted = 0` | **PASS** |
| 4 | ≥600 s coverage | **PASS** — 897.1 s, 8,356 distinct rows (9.3/s) |
| 5 | counter continuity | **PASS** — `book_updates` monotonic |
| 6 | BTC-reference coverage/sequence | not evaluated — gate already failed |
| 7 | no parse/truncation failures | **PASS** — 193 documents, 0 parse failures |
| 8 | gaps not batching/cap-attributable | 18 gaps, worst **4.748 s**; unchanged in count across three cadences and two caps, so venue-side |

**1630z was incomplete at check time** (53.1 s of 900), so a second qualifying
window does not exist yet regardless.

## Other requested figures

- max rows per emission: **400** (duplicate bursts); **51** excluding them
- max document bytes: **56,855 — 69.4%** of 81,920
- log volume: book documents **193 in ~17 min** versus 221 in ~38 min before, so
  roughly **2.2× the document rate**, as expected from halving the period; total
  book rows captured unchanged in substance
- trade collection: **unchanged in kind** — still emitting normally alongside, no
  schema or field change

## Why I am not making another recorder change

The remaining failure is a **code defect in the seen-set trim**, not a capacity or
cadence setting. No variable can fix it: raising the cap would make the duplicate
bursts larger (and 69.4% of the byte limit leaves little room), and lowering the
cadence further shrinks new-row batches without touching the re-emission cycle.

Options, for decision rather than action:

1. **Fix the trim** — replace the set with a bounded ordered structure, or trim by
   oldest timestamp. Small, local, in the credentialed recorder image, and would
   make criterion 2 pass honestly. Requires a code change and rebuild.
2. **Amend criterion 2** to exempt emissions whose rows are already-emitted
   duplicates, since the evidence now shows the cap binds only on duplicates and
   loses nothing. This is a change to the gate and must be your call, not mine —
   the whole value of the gate was that it was not relaxed after seeing results.
3. **Deduplicate at harvest and proceed** — the harvester already dedupes by
   `(slug, t, bid/ask)`, so the tape is unaffected by the re-emission; only the gate
   criterion objects.

Nothing is scored. 1300z frozen discovery; 1430z, 1445z, 1515z, 1530z and now 1615z
are recorder-validation windows only. Frozen `82714061` scoring continues
independently. No live orders.
