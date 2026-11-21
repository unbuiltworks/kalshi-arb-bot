# Observer restore: executed. Collection partly restored; two new findings.

**Date:** 2026-11-01
**Deployment:** `e238381d` SUCCESS, commit `3eeb46d3`, branch
`claude/gemini-btc-economic-viability-h3u7zl` (the observer's own build
branch). Build triggered by the push, not SKIPPED — `watchPatterns` matched.

**STOPPING before the 2 GB target, as instructed.** The measured per-window
size is **9.05 MB, not the estimated 42.6 MB**, so reaching 2 GB free would
delete **~226 windows rather than ~48** — 4.7× the estimate. That is a
material difference and it is your call, not mine. Only **4 windows** were
deleted.

---

## 1. Exact files deleted and storage recovered (item 8)

`L5_COLLECTOR_RETENTION` at 2026-11-01T23:00:22Z:

| field | value |
|---|---|
| `free_floor_mb` | **1.0** (a deliberate measurement floor, not 2048 — see §2) |
| `free_mb_before` | **0.0** |
| `free_mb_after` | **36.2** |
| `windows_before` | **1,178** |
| `windows_after` | **1,174** |
| `pruned_count` | **4** |
| `pruned_for_floor` / `pruned_for_count` | **4 / 0** |
| `failed_slugs` | **[]** |
| `floor_unreachable` | **false** |

**The four directories deleted, oldest-first:**

```
cpc-btc-updown-15m-2026-09-26-1300z
cpc-btc-updown-15m-2026-09-26-1315z
cpc-btc-updown-15m-2026-09-26-1330z
cpc-btc-updown-15m-2026-09-26-1345z
```

**Storage recovered: 36.2 MB.** `free_mb_before: 0.0` confirms the volume had
literally zero free bytes — the diagnosis was exact.

**Protected and untouched:** `protected_count: 2` —
`cpc-btc-updown-15m-2026-11-01-2300z` (the live window) and
`l5_pmus_realtime.jsonl` (the realtime ledger).

**Pre-cutover evidence proven intact (items 2, 4):**
`L5_COLLECTOR_EVIDENCE_INTACT {"checked": 1, "drift": [], "all_unchanged":
true, "taken_utc": "2026-09-26T13:10:46Z"}` — re-hashed after the prune, not
assumed. The 167,442,151-byte manifested file is byte-identical.

No trading ledger, settlement record or reconciliation state was touched;
those live on the executor's separate volume.

## 2. Why I used a 1 MB floor instead of 2048, and why I stopped

Deploying straight to a 2,048 MB floor would have deleted ~2 GB on the
strength of an estimate I could not check, with no way to undo it. So the
first deploy carried `L5_COLLECTOR_FREE_FLOOR_MB=1`: enough to free one or two
windows, restore writing, and **report the true per-window size**.

It was right to check:

| | |
|---|---|
| estimated per-window size | **42.6 MB** (50,000 MB ÷ ~1,175 windows) |
| **measured on the 4 oldest** | **9.05 MB** (36.2 MB ÷ 4) |
| windows to free 2,048 MB, at the measured rate | **~226** |
| vs the estimate | **4.7× more** |

**Why the estimate was wrong:** the volume averages ~42.3 MB per window
(49.8 GB over 1,178), but retention deletes **oldest-first**, and the oldest
windows are the cutover windows of 2026-09-26 — partial and far smaller than
average. So the first windows pruned are the cheapest, and the count needed
for a fixed byte target is much larger than an average-based estimate implies.

The true count for 2 GB lies **between 48 and ~226** and cannot be projected
honestly from four samples, because per-window size rises as pruning moves
into fuller windows.

**And the 2 GB target is not currently needed.** Collection is running at
36.2 MB free, and with the book feed down (§4) each window now consumes
**0.08 MB** — so 36 MB is roughly 450 windows of headroom. The right floor
depends on the restored window size, which is not yet known.

**Recommendation: leave the floor at 1 MB for now** — it is self-recovering
and prunes one window at a time only when needed — and set a real floor once
§4 is resolved and we can measure a full window.

## 3. What IS restored, and it is the important part

`L5_COLLECTOR_WINDOW` for `2026-11-01-2300z`:

| | |
|---|---|
| `storage_degraded` | **false** |
| `write_error` | **null** |
| `l5_polls` / `l5_polls_ok` / `polls_failed` | **288 / 288 / 0** |
| `raw_rows` | **28,800** |
| `rows_in_window` | **17,876** |
| `new_actions` | **76** |
| `l5_feed_health.healthy` | **true** |
| identity gate | 5 `MARKET_IDENTITY_MISMATCH` correctly refused |
| market binding | resolved, `token_up` / `token_down` bound |

**The 17-day silence has ended.** L5's wallet is being polled again — 288 of
288 polls succeeded and **76 new L5 actions were recorded in that single
window**. That was the actual cost of the outage: the only instrumented record
of L5's decisions, lost behind a SUCCESS deployment. It is back.

Verified across windows: `2315z` opened cleanly too (`degraded: false`,
`write_error: null`), so recording continues window to window (item 7, in
part).

## 4. NEW FINDING — the order-book feed is down with HTTP 401

This is a **separate outage the storage fix uncovered**, and it blocks the
multi-level book capture item 5 asks for:

```
"raw_frames": 0, "book_states": 0, "prints": 0,
"observed_fraction": 0.0, "max_gap_s": 900.0,
"feed_connected_ever": false,
"feed_errors": ["InvalidStatus: server rejected WebSocket connection: HTTP 401",
                "InvalidStatus: server rejected WebSocket connection: HTTP 401"],
"excluded_for": ["OBSERVED_FRACTION_BELOW_0.95","GAP_ABOVE_20s",
                 "FEED_NEVER_CONNECTED"]
```

**`PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` are present** —
`missing_feed_credentials()` did not fire — **but the venue rejects them with
401.** So they are stale or invalid, not absent.

Consequences, stated plainly:

- **Item 5 is NOT met.** No two-sided, multi-level, timestamped book records
  are being written. Storage was necessary but not sufficient.
- This is why windows are 0.08 MB instead of ~42 MB: only `window.json` and
  the L5 action rows are being written, no frame tape.
- The window is correctly excluded as `FEED_NEVER_CONNECTED`, not treated as
  quiet — which is exactly the distinction the collector was rebuilt to make,
  working as designed.
- **I am not touching the credentials.** Rotating or re-issuing a venue API
  key is an owner action, and I do not create or handle credentials. The
  observer's variables are set and the venue is refusing them; that needs a
  key rotation in the PMUS account and a re-set of the two variables.

## 5. NEW FINDING — `verify_pre_cutover` hashes the whole volume at boot

The container started at 22:48:28Z and the first prune did not run until
**23:00:22Z — 11 minutes 54 seconds later**, with memory peaking at
**7.17 GB** (limit 32 GB, so no OOM, but it is not free).

Cause: `verify_pre_cutover()` calls `inventory(root)`, which does
`sorted(base.rglob("*"))` and sha256s **every file on the volume** — all
50 GB — even though it then compares only the **1** file the manifest records.

```python
before = {f["path"]: f for f in doc.get("files") or [] if "sha256" in f}
now    = {f["path"]: f for f in inventory(root) if "sha256" in f}   # whole volume
```

This is a real ordering defect: on a **full** volume the integrity check runs
*before* the prune that would free space, so recovery is delayed by a cost
proportional to how full the volume is. It did not block recovery here, and it
will shrink as retention bounds the volume, but the fix is small and obvious —
hash only the manifested paths. **I have not changed it**: it is outside what
was approved today, and it is a correctness/performance fix that deserves its
own review rather than being folded into a restore.

## 6. Status against each instruction

| # | instruction | status |
|---|---|---|
| 1 | port the tested retention to the observer's branch | **done** — `3eeb46d3`, 4 code files, no merge |
| 2 | verify pre-cutover manifest and protected files before pruning | **done** — `all_unchanged: true`, `protected_count: 2` |
| 3 | delete only the oldest eligible raw windows, target ~2 GB | **partial, deliberately** — 4 oldest deleted, 36.2 MB; stopped at the 4.7× variance |
| 4 | do not delete ledgers, settlement, reconciliation, protected evidence, current window | **honoured** — `failed_slugs: []`, protected untouched, different volume for trading state |
| 5 | preserve the 24-window floor | **honoured** — `windows_after: 1,174`; floor never approached |
| 6 | deploy the existing observer service only | **done** — one service, no new service, no volume resize |
| 7 | verify disk decreases, multi-level book records written, recording continues | **mixed** — disk 0.0 → 36.2 MB free; recording continues (2300z, 2315z); **book records NOT written, HTTP 401 (§4)** |
| 8 | report exact files deleted, storage recovered, deployment errors | **done** — §1; no deployment errors |

## 7. Storage growth and retention policy, revised

The earlier 4.09 GB/day figure assumed ~42.6 MB windows. Both inputs are now
in question: the measured oldest windows are 9.05 MB, and with the book feed
down current windows are 0.08 MB.

**A sustainable floor cannot be set honestly until §4 is fixed** and one full
window with frames can be measured. What holds regardless:

- the count bound stays **OFF** (`L5_COLLECTOR_RETAIN_WINDOWS=0`), so nothing
  is deleted for being old;
- the floor is the only trigger, and it deletes the least history that lets
  collection continue;
- the 24-window floor and the pre-cutover manifest are absolute.

## 8. What I need from you

1. **Rotate the PMUS API credentials** and re-set `PMUS_API_KEY_ID` /
   `PMUS_API_SECRET_KEY` on `l5-pmus-observer`. Until then there is no book
   capture, and item 5 cannot be met by anything I can do.
2. **Decide the floor.** Options: leave 1 MB (self-recovering, running now);
   or name a byte target knowing the count is 4.7× the earlier estimate. My
   recommendation is to wait for (1) and then size it from a real window.

Nothing else was changed: no service created, no volume resized, no trading
strategy touched, nothing armed, and no credential created or exposed.
