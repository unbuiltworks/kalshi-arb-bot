# Fill-calibration recorder: production verification

Deployment `6f01fd6c` (commit `c6e5aa9a`), SUCCESS 2026-10-19T20:57:48Z.
Verified against **production shadow records**, not tests. The tests passed
on the first deployed commit; the records did not.

## The build was never stuck

`c6e5aa9a` reached SUCCESS at 20:57:48Z. Layers 40/52 -> 52/52 all completed
between 20:55:44 and 20:56:23, `exporting to docker image format` at 20:56:24.
The extra time over the usual ~2m45s was BuildKit cache invalidation caused by
the added `COPY src/l5_fill_calibration.py` line, which lands above the
cached layers. Nothing was wrong and nothing needed restarting.

## What the records showed

41 orders, 109 events, 0 recorder errors, by 21:07Z, at
`/data/l5-live-20261012z/fill_calibration.jsonl`. Two record families:

* `shadow|<POLICY>|<slug>|<t>` -- the four shadow variants' quotes, captured
  by `calibration_shadow_tick`;
* `l5f-<hex>` -- **real executor intents on the L5-following money path**.
  The hook fires immediately before the wire, and `transport.submit` then
  raises `NotArmed`: the arming refusal happens AT the wire, not before it.
  No order was sent, no order could be sent, and the capture is exercised by
  the real submission path rather than only by the shadow one.

Correct in the records: fee block (`maker_rebate_usd` matching the schedule,
`maker_fee_usd` 0.0000 with the no-maker-fee-term basis stated, full
provenance, `venue_actual: null` / `venue_actual_status: PENDING`, estimate
and actual never summed); the queue bound and its `UNKNOWN`
within-level position; `queue_ahead > level_depth` (correct by definition --
`ahead_of_us` includes better-priced levels); every record reporting itself
NOT usable for calibration.

## Three defects the records exposed

1. **The money path's first intents were blind.** `shadow_tick` runs LAST in
   the tick and was the only place that set `calib_book`/`calib_ladder`; the
   hook fires earlier. The first four `l5f-` records (21:01:15) carry
   `book: null` and `queue.why: "no_ladder"`. Thereafter the book was the
   previous tick's snapshot.
2. **A negative frame age was reported as fresh.** `frame_age_s: -75.496`
   with `stale: false` at 21:01:15, and `-0.058` with `stale: false` at
   21:07:33. The age was computed against the caller's `now`, which on the
   shadow path can be the window start, and the staleness test had no
   negative branch.
3. **The book block carried no timestamp at all**, so book staleness was
   unmeasurable -- contrary to the stated requirement of a pre-submission
   book ladder *and timestamp*. The input had `last_update_t`,
   `stream_age_s` and `book_updates`; `_book_block` dropped them.

## The fix

* `roll` points the executor at the **mirror itself** (`self.book`) and the
  window's ladder, both of which already exist where the executor is built,
  so the capture is armed before anything can submit. `_book_block` takes the
  snapshot at the submission instant. `shadow_tick` re-points rather than
  overwriting with a snapshot.
* Freshness is measured against the **read instant** (an injectable clock),
  not the caller's `now`, which is kept as the record's identity. A frame
  ahead of the read instant by up to `FRAME_RACE_TOLERANCE_S` is a feed
  thread race and is not stale, with a `clock_note`; beyond it the two
  timestamps are not on one clock and `stale` is **None** -- unknown, never
  False.
* The book block carries `observed_t`, `last_update_t`, `age_s`,
  `stream_age_s`, `book_updates`, `feed_source` and `capture_source`.

Eight new tests, including one that fails against the deployed wiring.

---

# Post-fix verification: deployment 104f1959, commit 778222e0

SUCCESS 2026-10-19T21:29:09Z. Regression on this tree: 26 failed / 11,649
passed / 14 errors = the SAME 40 IDs as the clean baseline, zero NEW, zero
GONE; the +8 passed are the 8 new tests. (A first attempt segfaulted at 55%
with dozens of leaked `gemini_ws` websocket threads because I had concurrent
pytest sessions in the same container; re-run alone, it completed.)
The code commit `c11c55d0` was SKIPPED by Railway and only the
`DEPLOY_TRIGGER` commit built, which is the watch pattern behaving correctly.

## Durable persistence across a restart: proven

First heartbeat after boot, 21:30:38Z:
`orders: 79, events_appended: 0, errors: {}`. Seventy-nine records folded off
the Railway volume, none written by this process yet. Pre-restart the count
was 75 and climbing. Nothing lost, nothing stitched over. By 21:33:59Z:
`orders: 84, events_appended: 47, errors: {}` -- 47 appends for 5 new
pre-submits, so the lifetime-observation path is appending too.

## The three defects, re-checked on real records

1. **First record of the new process**, 21:31:52Z, a real money-path intent
   (`l5f-c2f62492...`, two minutes after boot): `book` POPULATED,
   `capture_source: "mirror_snapshot_at_read"`, `queue_ahead: 1954.52`. Not
   `book: null`, not `no_ladder`.
2. **The same shape of record that produced -75.496** now reads correctly:
   `t: 1792445436.52` against `read_t: 1792445512.777` -- the caller is 76s
   behind the read instant, exactly the mixed-clock case -- and
   `frame_age_s: 0.088, stale: false`. Every observed age is positive and
   small: 0.066, 0.088, 0.022, 0.008.
3. **The book block carries time**: `observed_t`, `last_update_t`,
   `age_s` (0.066 / 0.088 / 0.125 / 0.111), `stream_age_s`, `book_updates`,
   `feed_source: authenticated_ws`, `capture_source`. The money path reads
   `mirror_snapshot_at_read`; the shadow path `caller_supplied_mapping`.

## Safety invariants at 21:33:59Z

`submission_gate`: blocked true, reason `SHADOW_ONLY_LOCK`, shadow_lock true,
armed false, owner_arm_token false. `live_orders: 0`, `parked_orders: 0`,
`quarantined: []`, `cash_deployed: []`. Account exposure total `0` against an
unchanged `max_total_exposure_usd: 5` and `max_usd_per_market: 5`.
`realized_total: -8.4013`, unchanged across the deployment -- no accounting
discontinuity. Four shadow controllers running: ARGMAX, PAIR_AVAILABLE,
RESIDUAL_URGENCY, ARGMAX@ONE_TICK_BEHIND. Recorder errors empty.

## Verdict: GO for the instrumentation, with its limit stated

Verified on production records: the PRE-submission half of the capture --
book with its timestamp, queue bound with provenance and its stated
unknown, fee schedule estimate with provenance, order-ID linkage, durable
append, restart fold, and the containment (no veto, `precapture_ok` never
called by the submission path, a recorder fault counted and swallowed).

NOT verified, and not verifiable in shadow: acknowledgment, partial fills,
cancellation, venue-confirmed terminal state, settlement reconciliation, and
`venue_actual` fees/rebates. Every one of those needs a real resting order;
`transport.submit` raises `NotArmed`, so no shadow record can reach them. All
84 records report `usable_for_calibration: false`, which is the correct
answer and not a defect. Those five paths have unit coverage only.

That gap is the reason to run a bounded live calibration, not a reason to
withhold one. The instrumentation is ready; what it has not yet done is
record a real fill, because it has never been allowed one.
