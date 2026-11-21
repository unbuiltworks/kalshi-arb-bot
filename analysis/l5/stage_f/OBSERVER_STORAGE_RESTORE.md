# Restoring continuous PMUS BTC15m collection on `l5-pmus-observer`

**Date:** 2026-11-01
**Status: diagnosed, fix located, NOT applied.** The fix's first action is a
deletion, so it is presented with exact impact and awaits a decision.

---

## 1. Confirmed: the volume is full and recording has stopped

| | |
|---|---|
| volume | `l5-pmus-obs-evidence` (`b51edade-…`), mount `/state/l5-pmus-obs` |
| provisioned | **50,000 MB** |
| `DISK_USAGE_GB` | **50.197348352** |
| over 24h, 1,441 samples | **min = max = current = 50.197348352** |
| last SUCCESS deployment | **2026-09-26** (`8940a247`); every deployment since reads SKIPPED |
| restart policy | ALWAYS, 1 replica, `us-east4-eqdc4a` |

**Usage exceeds the provisioned size and is perfectly flat across every sample
in 24 hours.** A collector that was writing would not produce a constant. It
is writing nothing.

Live error, still repeating every ~5s through 21:26Z today:

```
L5_COLLECTOR_ERROR {"error": "OSError", "detail":
  "[Errno 28] No space left on device:
   '/state/l5-pmus-obs/windows/cpc-btc-updown-15m-2026-11-01-2115z'"}
  File "/app/src/l5_window_collector.py", line 436, in __init__
    self.dir.mkdir(parents=True, exist_ok=True)
```

It fails in `WindowRecord.__init__` — the **first** statement of
`collect_one` — so nothing downstream runs. `restartPolicyType: ALWAYS`
cannot help: the process is healthy and looping, not crashing.

## 2. Root cause, and it is already written down

`src/l5_window_collector.py:428` documents it exactly:

> *"There was no retention of any kind: one directory per 15-minute window,
> each holding a verbatim frame tape, written forever. **96 a day filled the
> 50 GB volume** and the collector then raised ENOSPC out of
> `WindowRecord.__init__` … every five seconds from 2026-10-08 19:30Z to
> 2026-10-26, so `new_l5_actions` was never reached and the wallet feed was
> never polled at all **while the deployment reported SUCCESS**."*

**The repair exists and is not deployed.** `src/l5_window_retention.py` (7,493
bytes) plus the `RETAIN_ENV` / `FREE_FLOOR_ENV` hooks in the collector:

| | |
|---|---|
| retention references in the **deployed** commit `8940a247` | **0** |
| `src/l5_window_retention.py` on the observer's build branch `claude/gemini-btc-economic-viability-h3u7zl` | **absent** |
| retention references in that branch's collector | **0** |
| branches that do contain it | `claude/compassionate-allen-wic64u` and descendants |

So the observer has been building a branch that never received the fix. That
is also why the symptom recurred after the comment's "to 2026-10-26" — nothing
on the deployed branch ever changed.

## 3. Smallest safe fix (item 2)

**Port two files to the observer's own build branch and push.** Nothing else.

| | |
|---|---|
| files | `src/l5_window_retention.py` (new), `src/l5_window_collector.py` (retention hooks) |
| target branch | `claude/gemini-btc-economic-viability-h3u7zl` — the branch the service already builds |
| why the push is the deploy | `watchPatterns` is `deploy/l5_window_collector/**`, `src/l5_window_collector.py`, `scripts/l5_window_collector_run.py`; the second entry matches |
| new service | none |
| service-config change | none |
| volume expansion | none |
| variable change | none (defaults are correct) |
| tests | **39** covering `prune_windows`, **58** on the collector, all passing |

## 4. EXACT IMPACT — presented, not performed (item 4)

**The volume is 100% full, so nothing can be written until bytes are freed.
Every path forward either deletes data or buys storage.** Both need your
decision.

### Option A — deploy the existing retention (deletes; no cost)

Defaults, which I would not change:

| setting | default | effect |
|---|---|---|
| `L5_COLLECTOR_RETAIN_WINDOWS` | **0** | **count bound OFF** — no window is deleted for being old |
| `L5_COLLECTOR_FREE_FLOOR_MB` | **2048** | frees **only** what the floor demands |
| `MIN_RETAIN_WINDOWS` | 24 | never prunes below 24 windows, whatever the floor says |

**Estimated deletion to restore writing:**

| | |
|---|---|
| bytes to free | **~2,048 MB** (free space is currently 0) |
| per-window size, derived | **~42.6 MB** = 50,000 MB ÷ ~1,175 windows |
| …where ~1,175 = | 2026-09-26 13:50Z deploy → 2026-10-08 19:30Z ENOSPC = 12.24 days × 96/day |
| **windows deleted** | **≈ 48** |
| **history lost** | **≈ 12 hours**, the oldest — roughly 2026-09-26 13:50Z → 2026-09-27 02:00Z |
| **share of stored windows** | **≈ 4%** |

**These are estimates.** I cannot list the volume from here — there is no exec
path to the container in this toolset. `prune_windows` returns
`pruned_slugs`, `free_mb_before`, `free_mb_after`, `windows_before/after`, so
the **exact** figures are reported by the first run and will appear in the log.

### Option B — expand the volume (no deletion; paid)

| | |
|---|---|
| 50 GB → 100 GB | buys **~12.2 more days** at the current write rate |
| then | the identical wall, in the identical way |

**Expansion alone does not fix this.** At 4.09 GB/day (§7) any fixed volume
fills; the defect is the absence of a bound, not the size of the bound. If you
want more history retained, the sensible shape is **retention *and* a larger
volume**, not expansion instead of retention. I have not changed the volume
size and will not without an explicit instruction naming the GB and the cost.

### Recommendation

**Option A with defaults.** It deletes ~4% of the oldest tape, restores
collection immediately, and is self-limiting thereafter. If that 12 hours
matters, say so and I will export it first — but note it is 2026-09-26/27
BTC15m frame tape, which no current analysis depends on.

## 5. What is protected (item 3)

**The trading records are not on this volume at all.** Settlement evidence,
order ledgers and reconciliation state live on the **executor's** volume
(`l5-pmus-live`, `L5_LIVE_ROOT=/data/l5-live-20261012z`) — a different
service, a different volume. Observer retention cannot reach them.

On the observer volume itself, `prune_windows` protects:

- **everything named in `PRE_CUTOVER_MANIFEST.json`** — never touched, and
  `verify_pre_cutover()` can prove the pre-cutover history byte-identical
  afterwards;
- **the live window, by name** (`keep_slugs`), so the open tape cannot be
  removed from under itself;
- **a floor of 24 windows**, whatever the free-space floor asks for;
- **a floor it cannot reach is reported** as `floor_unreachable` rather than
  satisfied by emptying the volume.

And the structural guarantee, which is why this is safe to deploy at all:
`l5_window_collector` is **asserted by test to call no destructive verb** —
no `unlink`, `rmtree`, `remove`, `truncate`, `rmdir`, `move` or `copytree`.
Deletion exists only in the separate operational module, as one named
function.

## 6. Depth capture is already sufficient (item 5)

**No collector change is needed to get three or more depth levels — the raw
tape already has them.**

- `ingest()` writes the **verbatim WS frame**:
  `self.sink({"kind": "ws", "t": t, "market": …, "msg": msg})`. Nothing is
  dropped. This is what makes a window directory ~42.6 MB.
- `SUB_MARKET_DATA` frames carry `bids` and `offers`/`asks` as **level
  lists** (`pmus_ws_evidence_recorder.py:199-204`).
- Independent confirmation that PMUS serves deep books:
  `data/l5_strand_replay/residual_books_20261025.jsonl` holds **8 levels per
  side**.
- The derived `book_states` keeps **BBO only** (`_level()` takes `levels[0]`),
  but that is a convenience view, not the evidence.

So restoring writes restores ≥3-level, timestamped, two-sided capture. If a
**derived** multi-level view would be useful for replay, that is a separate
small change and I have not made it.

## 7. Verification plan, once the fix is applied (item 6)

Not yet checkable; naming the checks so the result is falsifiable:

1. `L5_COLLECTOR_ERROR` ENOSPC lines **stop**;
2. a `prune_windows` report appears with `free_mb_after >= 2048` and
   `floor_unreachable: false`;
3. new `windows/cpc-btc-updown-15m-*` directories appear with advancing
   timestamps, one per 15 minutes;
4. `DISK_USAGE_GB` **stops being constant** and oscillates below 50;
5. after a container restart, `verify_pre_cutover()` still reports the
   pre-cutover history **byte-identical**;
6. re-read one window directory written after the fix and confirm the frame
   tape parses and carries ≥3 levels per side.

## 8. Storage growth and a sustainable retention policy (item 7)

| | |
|---|---|
| windows per day | 96 |
| per window | ~42.6 MB |
| **growth** | **~4.09 GB/day** (~28.6 GB/week) |
| capacity of the 50 GB volume | **~12.2 days** |

**Sustainable policy: the shipped default — floor-only, 2,048 MB.** It keeps a
**rolling ~11.5 days** of full-depth tape, reaches steady state, needs no
ongoing attention, and prunes ~4 GB/day in the same amount it writes. It is
self-limiting by construction: the bound is free bytes, not a guess about row
size.

Two levers if more history is wanted, in preference order:

1. **Export, then set a count bound.** `L5_COLLECTOR_RETAIN_WINDOWS=N` keeps N
   windows (N is floored at 24). The module's own docstring is the warning:
   *"Deletion is irreversible: export the volume before setting a count
   bound."*
2. **Enlarge the volume alongside retention.** Each +50 GB buys ~12.2 days of
   retained history at the same write rate. Paid, and your call.

What I would **not** do: shrink the per-window tape to save space. The verbatim
frames are what make ≥3-level depth available, and that depth is exactly the
evidence the ceiling comparison (`CEILING_096_TO_099.md` §7) could not be run
without.

## 9. On the next question

`CEILING_096_TO_099.md` §7 named a continuous two-sided PMUS book series over
≥100 contiguous windows, recorded independently of our order state, as the
prerequisite for the outstanding profitability work. **This collector is that
series**, and at 96 windows/day it clears 100 windows in **~25 hours** once
writing resumes.

I have **not** returned to the L5 inventory question, because collection is
not restored yet and the honest answer to "which inventory decisions generate
positive net returns on PMUS" still depends on the data this service is
supposed to be producing. Restoring it first is the right order.

**Nothing was deleted, no volume resized, no service created, no strategy
deployed, nothing armed, and no push was made to the observer's branch.**
