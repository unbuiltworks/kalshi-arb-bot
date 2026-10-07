# E1 — owner decisions, 2026-10-07

Four decisions, and what each actually required. Two needed no code at all,
which is stated plainly rather than dressed up as work.

## 1. Maximum 240 placements = 20/window x 12 windows — NO CODE CHANGE

The artifact already froze this. `spec.py`:

    MAX_ATTEMPTS_PER_WINDOW = 20
    MAX_PLACEMENTS = WINDOWS * MAX_ATTEMPTS_PER_WINDOW   # 240

and the invariant list pins `("cohort_bounds.max_placements", 240)`, asserted
at import and at every boot. Verified at this commit: `max_placements 240`,
`max_attempts_per_window 20`, `windows 12`.

So "228" never existed in the artifact. It was my reporting error in chat: 19
attempts was the count that EMERGED at the $0.60 first-leg ceiling, and I
reported an emergent observation as if it were a bound. The owner's correction
is exactly right and required nothing to be unlocked.

Unchanged, as instructed: `$1.56` per-window hard limit and the `$5.00`
forward-looking cohort risk ceiling (which includes unresolved worst-case
exposure, not only realized loss).

## 2. Sparse `Q_ahead` accepted, no periodic snapshot re-request — NO CODE CHANGE

Already the behaviour, and verified rather than assumed:

- `live_book._set_top_locked` sets `size_at_our_price` and `size_ahead_of_us`
  to `None` and writes `size_unknown_reason` whenever the frame carries no
  ladder at the best bid. It never infers.
- a `price_change`'s `size` is the size at the CHANGED price, so it is used
  only when the changed price IS the best bid on the BUY side.
- `provider.py` only RECORDS those two fields into the snapshot.
  `runner.py` reads neither. No decision in the cohort depends on queue depth.
- there is no snapshot-request mechanism anywhere in the package, so nothing
  had to be removed to comply.

Measured sparsity, from the proven probe (deployment 90fa9013, market
btc-updown-15m-1791372600): 2 book snapshots out of 11,523 frames. Queue depth
is therefore unknown for essentially the whole cohort, recorded as unknown.

## 3. `_set_top` synchronisation — CODE CHANGE, and wider than `_set_top`

Investigating the named defect found the writer was the SAFER half. Two READERS
were tearing:

- `book()` read `self.top[up]` and `self.top[down]` as two unsynchronised dict
  lookups, then reported `book_unix` as the MAX of the two. A collector thread
  landing between them returned a FRESH leg beside an OLDER one, under the
  fresh timestamp. That is the same failure class as the frame-shape bug that
  superseded `cdc63519...`: a stale book presented as current, pointing the
  wrong way.
- `health()` read `last_update` three times plus four counters, so the
  freshness it certified could belong to no single instant.

Fix: a per-feed `threading.RLock` guarding `top`, `last_update`, `last_seen`
and the counters. One frame is one transaction (`observe` holds it across every
`price_changes` entry, so a reader never sees half a frame); `book()` copies
both legs in one critical section; `health()` snapshots everything in one.

WHY NOT LITERALLY THE EXISTING `_LOCK`. The owner asked for "the existing
lock". `_LOCK` guards a different invariant — the one-feed-per-slug registry —
and `feed_for` HOLDS it while calling `f.start(...)`, which starts the
collector thread. Taking that same lock on the per-frame publish path would
put a lock held across thread startup on the path of the thread being started,
and would serialise unrelated markets through one global. The requirement was
that consistency stop being accidental; a per-feed lock delivers that and
leaves the registry lock to its job. `test_the_registry_lock_is_still_its_own_lock`
asserts the two are distinct.

NO FEED SEMANTICS CHANGED. The `max()` framing of `book_unix` is left exactly
as it was — with an atomic read, per-token staleness is still gated by
`health()`, so the pair and the gate now agree. Flagged as an observation, not
altered.

### The regression test fails without the fix

`test_e1_live_book_concurrency.py`. The writer publishes real-shaped
`price_change` frames whose two legs ALWAYS sum to 1.00 (UP 0.30/DOWN 0.70,
alternating). Any one frame is consistent; any MIX of two sums to 0.60 or
1.40. So a torn read is detected arithmetically, with no sleeps and no timing
assumptions.

Verified by neutralising the new lock and re-running:

    FAILED test_both_legs_always_come_from_one_frame
    FAILED test_health_and_book_agree_under_concurrent_writes
    2 failed, 3 passed

and the health failure showed the defect concretely — `tokens_last_seen_unix`
came back 11ms OLDER than `tokens_last_book_update_unix`, i.e. health
certifying an instant that had already gone. With the lock: 5 passed.

Honest scope on the third test: `test_counters_are_not_lost_under_concurrent_writers`
passes WITHOUT the lock too at this scale, because the GIL rarely switches on
this loop's bytecode boundaries. It is a guard against a future writer, not a
demonstrated bug, and its docstring says so.

## 4. Pin Railway to the exact commit — OPERATIONAL, done after the final push

The service is currently branch-tracked, so any push redeploys it and would
restart the executor mid-cohort. It is pinned to the exact final commit before
arming, and the pinned SHA is recorded in `DISARMED_BOOT_PROOF.md`.

## Fingerprint

UNCHANGED: `481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486`

Decisions 1 and 2 changed nothing. Decision 3 is implementation-level: it
touches `live_book.py`, which the frozen `SPEC` dict does not describe, so
`assert_frozen()` still passes against the same literal. This is NOT a sixth
superseded fingerprint, and these decisions are deliberately NOT written into
`SPEC` — doing so would move the fingerprint for a reason that changes no
cohort behaviour, and would invalidate the authorized artifact to record
paperwork.
