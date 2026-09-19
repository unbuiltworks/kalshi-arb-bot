# 2026-09-19 — l5-exec-shadow volume reached 100% and killed the recorder

Storage remediation. No trading or model behaviour was changed, and the
Gemini BTC15m lane was not touched.

## What happened

`l5-research-state` (50 GB, mounted `/state/l5-research`) filled completely.
From 16:13Z every stream failed with `OSError: [Errno 28] No space left on
device`, and the replica then crashed: `running: 0, crashed: 1`, 12 critical
issues. Evidence collection stopped entirely.

Nothing warned first. The recorders were healthy, the streams were fresh, and
the first symptom was every write failing at once — the worst failure mode
for an evidence recorder, because it is silent right up to the moment it is
total.

## What was on the volume

Measured from the container's own filesystem listings once it was back up:

| directory | bytes | GB | share | files |
|---|---|---|---|---|
| **l5-surface-shadow** | 38,775,773,740 | **38.78** | **80.1%** | 13 |
| gemdepth | 4,156,402,719 | 4.16 | 8.6% | 3 |
| l5-book-context | 3,115,768,960 | 3.12 | 6.4% | 18 |
| gemini-surface | 1,058,537,821 | 1.06 | 2.2% | 17 |
| l5-shadow-state | 1,045,034,304 | 1.05 | 2.2% | 12 |
| pmus-l5-surface | 142,987,704 | 0.14 | 0.3% | 6 |
| chainval | 113,388,075 | 0.11 | 0.2% | 5 |
| l5-window-forensics | 31,275,868 | 0.03 | 0.1% | 1 |
| **TOTAL** | **48,439,169,191** | **48.44** | | |

(plus `coverage.jsonl` at 6.95 MB and 43 `seg_*` directories whose contents
were not separately enumerated.)

The five largest single files are all one stream:

```
surface_shadow_20260918T222730Z.jsonl   8.86 GB
surface_shadow_20260918T123735Z.jsonl   8.85 GB
surface_shadow_20260917T224144Z.jsonl   8.82 GB
surface_shadow_20260919T081525Z.jsonl   7.00 GB
surface_shadow_20260918T081817Z.jsonl   3.83 GB
```

## Growth, and why the resize alone was not the fix

| stream | measured | rate |
|---|---|---|
| `surface_shadow` | 38.78 GB over 43.7 h | **21.31 GB/day** |
| `book_context` | 3.12 GB over 46.2 h | 1.62 GB/day |
| `gemdepth` | 4.16 GB over 97.3 h | 1.03 GB/day |
| all streams | | **≈ 26 GB/day** |

**I resized the volume to 250 GB before I knew the growth rate, and at 26
GB/day that buys only 8.5 days.** That is not substantial headroom, and the
resize on its own would have reproduced this outage inside a fortnight. What
actually fixes it is compression: gzip on this JSONL is lossless and recovers
roughly an order of magnitude, which takes the effective rate to ~2.2 GB/day
and the runway to **~100 days**.

## Canonical vs regenerable

**Nothing on this volume is regenerable, and nothing was deleted.** Every
byte is recorded market observation — books, surfaces, depth, prints — from
venues that serve no history, so a deleted row cannot be re-fetched at any
price. There is no cache, no build output and no derived artifact consuming
the disk. Under the owner's rule that is the case where capacity is increased
rather than reclaimed, and that is what was done.

The one genuinely worthless category is the **zero-byte segments** the
crash-restart loop left behind — every restart against a full volume opened a
new segment it could never write, leaving dozens across every stream
directory. They occupy no space, they are the record of the loop, and the
retention sweep explicitly skips them rather than converting them to `.gz`
clutter.

## The retention policy

`src/l5_state_retention.py`, swept by the supervisor at boot and every 15
minutes.

- **It compresses; it does not delete.** `x.jsonl` becomes `x.jsonl.gz`, and
  the source is unlinked **only** after the archive is read back and verified
  byte-for-byte by sha256. A verification failure, or a write that runs out of
  space mid-archive, leaves the source in place and reports why.
- **Deletion is off by default and pressure does not turn it on.**
  `delete_after_days` defaults to `None` and the supervisor passes `None`. A
  test asserts no `unlink` in the module is keyed off a usage level. A
  recorder may fail closed; it may not destroy the record to keep running.
- **Three independent guards protect the segment a recorder still has open**:
  it is skipped if it is the newest file in its directory, if it was modified
  within 2 hours, or if it is zero bytes.
- **Warnings at 70 / 85 / 95%.** `usage()` goes on every supervisor
  heartbeat, and `L5_DISK_NOTICE` / `L5_DISK_WARN` / `L5_DISK_CRITICAL` is
  printed whenever the level is not OK.
- **The sweep runs before the streams start.** If the volume is full at boot,
  every stream the supervisor launches dies on its first write — which is
  exactly what this outage looked like. Freeing space first is what makes the
  boot meaningful.

## Capacity

| | before | after |
|---|---|---|
| volume size | 50,000 MB | **256,000 MB** |
| used | ~50 GB (100%) | 48.44 GB (~19%) |
| free | 0 | ~205 GiB |
| growth | 26 GB/day | ~2.2 GB/day once compressed |
| runway | **0 days** | **~100 days** |

PRO plan allows volumes to 1000 GB, so there is room to grow again if the
stream set expands.

---

# Verification (17:05Z)

All figures below are the container's own `shutil.disk_usage` off the mounted
volume, carried on every supervisor heartbeat — not estimates.

### service healthy → volume writable → recorder advancing

`not_running: []`. All eight streams `process_alive: true`, every one growing
between heartbeats:

| stream | 16:58:33 | 17:05:07 |
|---|---|---|
| `surface_shadow` | 64,349,804 | 135,994,986 |
| `gemdepth/samples.jsonl` | 1,998,181,950 | 2,000,535,230 |
| `book_context` | 9,175,127 | 16,350,717 |
| `gemini_surface` | 2,943,854 | 5,634,564 |
| `window_forensics` | 113,947 | 1,797,759 |
| `pmus_surface` | 520,149 | 1,064,765 |

No `Errno 28` since the restart. `pmus_surface` reports `tape: SERVED`,
`unreadable_observations: 0`, and its evidence ladder is advancing
(`EVIDENCED_FILL` 3 → 5 → 7 on BUY_YES).

### no evidence corruption

`compress()` sha256s the source, gzips, re-reads the archive through
`gzip.open`, compares, and unlinks the source **only** on a match — with the
failure path covered by two tests. Independently of that, the directory
conserves its entries: `l5-surface-shadow` held 37 entries before the sweep
and 37 after (plus one new active segment), and the only two names that left
`.jsonl` are exactly the two that appeared as `.jsonl.gz`. Nothing else moved.

```
surface_shadow_20260918T222730Z   8,863,548,721 -> 181,253,490   48.9x
surface_shadow_20260918T123735Z   8,852,409,046 -> 183,543,295   48.2x
```

**48x, four times the 12x I assumed when I sized the volume.**

### used / free capacity

| | |
|---|---|
| total | 251,654,762,496 (251.65 GB) |
| used | 31,766,425,600 (31.77 GB) |
| free | 219,871,559,680 (219.87 GB) |
| **used** | **12.62% — level OK** |

Down from 100% and a crashed replica.

### growth/day and days to the next threshold

Measured across heartbeats 16:58:33 → 17:05:07: 89,378,816 bytes in 394 s =
**19.60 GB/day uncompressed**. That is the raw write rate between sweeps; the
sweep then compresses closed segments at ~48x, so net growth settles near
**0.5–1.0 GB/day**.

| threshold | GB to go | at 0.5 GB/day | at 1.0 GB/day | if compression stopped |
|---|---|---|---|---|
| 70% NOTICE | 144.4 | 289 d | 144 d | 7 d |
| 85% WARN | 182.1 | 364 d | 182 d | 9 d |
| 95% CRITICAL | 207.3 | 415 d | 207 d | 11 d |

The last column is the honest failure case: **the volume only holds if the
sweep keeps running**, and that is exactly what the 70/85/95 warnings exist
to catch. They now fire on every heartbeat, months before the wall.

### one pre-existing condition, not caused by this work

`window_forensics` shows `restart_count: 109`, `last_restart_reason:
file_not_growing>1200s`. It is the one stream with `SEGMENT_PER_START`, so
every restart opens a new `seg_*` directory — which is why there were 43 of
them. It is being restarted by its watchdog rather than crashing, and it is
recording now. Flagged rather than fixed: it is a churn source and a separate
question from storage.
