# Gate check at cap 400 — FAILS criterion 2. Nothing scored.

Deployment `a955eb8e` SUCCESS 15:09:05Z; emissions confirm `cap_per_market: 400`.
First two windows recorded entirely under it: **1515z** and **1530z** (verified from
row timestamps, not assumed — 1500z was already in flight at the switch).

| # | criterion | 1515z | 1530z |
|---|---|---|---|
| 1 | `rows_lost_to_cap = 0` | PASS (0) | PASS (0) |
| 2 | **no emission reaches 400** | **FAIL — 6 emissions at exactly 400** | **FAIL — 6 emissions at exactly 400** |
| 3 | per-emission `Δbook_updates − emitted = 0` | PASS | PASS |
| 4 | ≥600 s continuous coverage | PASS (897.2 s) | PASS (892.9 s) |
| 5 | sequence/counter continuity | PASS (`book_updates` monotonic) | PASS |
| 6 | BTC reference brackets window | not evaluated — gate already failed | — |
| 7 | no BTC sequence loss/restart | not evaluated | — |
| 8 | >2 s gaps characterized, not cap-attributable | 18 gaps, worst **3.043 s** | 18 gaps, worst **3.071 s** |

**The synchronized study was NOT run.** Criterion 2 is disqualifying and was not
relaxed, which is the whole point of declaring it in advance.

## The at-cap emissions

1515z: 15:22:31, 15:24:11, 15:25:44, 15:27:06, 15:28:16, 15:29:51 — each exactly
400 rows, 56,323–56,849 bytes.
1530z: 15:37:25, 15:39:16, 15:40:48, 15:42:12, 15:43:22, 15:44:57 — same shape.

They are catch-up emissions: generation runs at p50 99 / max 110 states per market
per emission, so a 400-row document spans several emission intervals' worth of
backlog. No per-emission loss is detectable (`Δbook_updates − emitted = 0`
everywhere, and these documents emit *more* than their own delta). But an emission
landing exactly on the cap is the signature the gate exists to catch, and at 400 it
is also within reach of the hard byte ceiling: **56,849 bytes is 69.4% of the
81,920-byte limit**, so a two-market document at 400 rows each would be truncated
and unparseable.

## Encouraging, separately

The gap distribution improved markedly: worst gap is now **~3.0 s** on both
windows, against 5.1 s at cap 200 and 5.2 s at cap 50. Counts are flat at 18 per
window, so those residual gaps look venue-side rather than cap-side — which is what
criterion 8 asks and is the first positive evidence for it.

## Why raising the cap again is NOT the next step

`PMUS_WS_BOOK_LOG` is hard-clamped at 400 in code (`max(0, min(v, 400))`), so 400
is the ceiling without a code change, and the byte headroom above argues against
going higher anyway.

The actual driver is emission CADENCE, not capacity. Book emission is called from
the status cycle (`emit_new_books` inside the status worker), whose period is
`PMUS_WS_STATUS_EVERY_SECONDS` — currently producing p50 10.04 s / p90 20.04 s.
Halving that period would roughly halve rows per emission and put 400 out of reach,
and it is a **variable-only** change needing no code and no rebuild of the
credentialed image.

Its cost, stated honestly: the status report, the gateway cross-check and the trade
emission all share that cycle, so the period also doubles status log volume and
worker CPU. That is a change to the recorder's operating rate, not just a buffer
size, so **I am proposing it rather than making it** — the standing authorization
covered book-row capacity only.

## Audit bug fixed in passing

`scripts/pmus_book_completeness_audit.py` counted a row once per document it
appeared in, reporting 9,814 rows for both 1515z and 1530z. Distinct receipt
timestamps are **7,914** and **7,932**. Row counts are now de-duplicated; the gap
statistics were unaffected (duplicates contribute zero-length intervals).

## Unchanged

1300z frozen discovery, never rescored. 1430z/1445z recorder-validation only.
Frozen `82714061` scoring continues independently. No threshold fitted, no feature
mining, no strategy change, no orders. `skillful-nature` left for manual cleanup.
