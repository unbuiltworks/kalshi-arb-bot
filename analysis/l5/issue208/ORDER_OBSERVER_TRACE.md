# `events_appended: 0` — a quiet system, and two defects behind it

PR #208 **PRIORITY 2**. Tests `tests/test_l5_calibration_observer_wiring.py`
(25), driven in-process with synthetic events. **No order was submitted and
no transport was touched**, per the instruction. Production disarmed at
`6116675ed`.

---

## 1. The answer: both, and the second half is what matters

**The zero is legitimate.** Every producer of a calibration event sits on
the submission path or the venue order-progress path, and each needs a live
order. Disarmed, nothing submits, so nothing appends. That is a quiet
system, not a broken recorder.

**And the recorder could not have produced a usable record anyway.** Two
defects, both found by driving the lifecycle synthetically, both now fixed.

## 2. The wiring, hook by hook

| hook | category it produces | called from | status |
|---|---|---|---|
| `pre_submit` | `pre_submit_book`, `queue_estimate`, `fee_accounting` | `l5_live_execution`, immediately before the wire | wired |
| `on_ack` | venue binding | same, immediately after the ack | wired |
| `on_fill` | the fills, one per `delta > 0` | `apply_order_progress` via `_calib_safe` | wired |
| `on_fees_actual` | actual fee column | same, from the venue's `commission` | wired |
| `on_terminal` | `terminal_state` (venue-confirmed) | same, on `prog["terminal"]` | wired |
| `observe` | **`lifetime_observation`**, and the only source of `prints_at_our_price` | **`calibration_shadow_tick` only** | **was shadow-only — FIXED** |
| `on_settlement` | not required | nowhere | still uncalled; see §5 |

### Defect A — the two halves never met

`observe` is the only producer of `lifetime_observation`, which is one of
the five `REQUIRED_CAPTURE` categories, and the only source of
`prints_at_our_price`. It was reached **only** from
`calibration_shadow_tick`, with `shadow|…` coids. So:

| | observations | venue-confirmed terminal | usable |
|---|---|---|---|
| shadow records | ✅ | ❌ by construction, there is no order | never |
| **live records** | **❌** | ✅ | **never** |

An `l5f-` order that rested for two minutes produced four of five categories
present and **not one reading of the queue it was sitting in** — the single
field the entire queue-position question needs, and the reason a live window
could have been spent for nothing.

**Fixed** by `Runner.calibration_live_tick`, called from the same tick as
the shadow one, on the same book, ladder, prints and instant, over the live
`l5f-` coids from `store.live_orders()`. It is observational exactly like
every other recorder call: no transport, cannot refuse or alter a
submission, swallows its own faults, and `observe` itself filters the coids
to those it already knows with a matching slug.

### Defect B — restart boundaries were promised and not delivered

The recorder's class docstring says:

> "a record whose events straddle a restart is marked with the gap rather
> than stitched over"

It was not. `_fold` accepted a `replay` flag and **never read it**, and
`restart_boundaries` was initialised to `0` and incremented **nowhere**. So
a record spanning a container restart was silently stitched and read as
though one process had seen all of it — for an order whose life crosses a
restart, which is precisely the case a durable ledger exists for, the record
**looked complete and was not**.

**Fixed.** A boundary is the moment a record reloaded *from the file* first
receives a live event: its events then demonstrably span two processes.
Reloading alone counts nothing, or every restart would invent a boundary for
every order that ever existed; and a second live event does not count a
second boundary. `report()` now carries
`orders_straddling_a_restart`. Tests pin all three behaviours.

## 3. What the synthetic lifecycle proves

25 tests, no order, no network:

- a **driven** lifecycle — pre_submit → ack → observations → fill(s) →
  fees → terminal → settlement — yields all five categories `PRESENT` and
  `usable_for_calibration: true`;
- drive everything **except** `observe` and the only missing category is
  `lifetime_observation`, while `precapture_ok` stays true — which is the
  distinction that function exists to draw;
- **partial fills** arrive as successive fills with a rising cumulative;
- a **cancel** is a venue-confirmed terminal and still usable — a cancelled
  order is evidence too, that the queue did *not* clear;
- every event **reaches disk**, line count equals `events_appended`, and a
  restart rebuilds the record with the same captures while appending nothing
  and duplicating nothing;
- a **restart mid-order** is marked, not stitched;
- a **malformed ledger line** is skipped and counted, and the rest of the
  record still reads;
- an **observation gap** over `MAX_OBSERVATION_GAP_S` is reported as
  `MISSING:coverage_gap` rather than smoothed;
- prints are credited **only at our exact price**, and an absent print feed
  is recorded as absent rather than as zero prints;
- the queue reading says it is a **bound** and that our position within the
  level is `UNKNOWN`;
- and **no entry point raises** whatever it is handed — every one of the
  seven, given objects it cannot parse.

## 4. Corrections to my own first reading

My first pass reported `observe`, `on_fill`, `on_settlement` and
`on_fees_actual` as having no caller. That was wrong, and the cause is worth
recording:

- my `grep` printed only the first four hits per hook, which hid the real
  call site;
- `on_fill`, `on_fees_actual` and `on_terminal` are dispatched through
  `_calib_safe` with the hook name on a **continuation line**, so the
  contiguous string `_calib_safe("on_fill"` does not appear in the source at
  all. My structural test asserted on unflattened source and read "not
  wired" when it was. It now matches on whitespace-collapsed source.

The defect that survived scrutiny is narrower than the one I first
announced: not "nobody calls `observe`", but "`observe` is called only for
shadow coids".

## 5. Still open, deliberately

`on_settlement` is implemented and called from nowhere. It is **not** in
`REQUIRED_CAPTURE`, so it does not block a usable record, and settlement
cashflows are already accounted on the money path by `L5_LIVE_SETTLED`.
Recorded here so it is not mistaken for wired, and pinned by a test so it
cannot be quietly assumed.

## 6. What this changes in the experiment proposal

My proposed pre-flight was **too weak** and I am correcting it. It said
"confirm the observation path is reached and would append". That would have
passed on `pre_submit` alone and still produced an unusable record.

The acceptance gate is now, in order:

1. **Before arming** — on a disarmed tick, `report()` must show
   `orders > 0` from the shadow rehearsal with
   `capture.lifetime_observation == PRESENT` on at least one shadow record.
   That proves the observation machinery runs against real venue data.
2. **Within the window** — for the live `l5f-` order,
   `completeness(coid)["capture"]` must reach `PRESENT` on
   `pre_submit_book`, `queue_estimate` and `fee_accounting` **before** the
   second leg is quoted. `precapture_ok` is exactly this test and the probe
   may decline an uncalibratable order on it.
3. **At the end** — `completeness(coid)["usable_for_calibration"]` must be
   **true**, with `observations ≥ 2` and
   `coverage.gaps_over_%gs == 0`. A record with a gap is not a
   failed experiment, but it is not a calibration either and must not be
   used as one.
4. **Always** — `orders_straddling_a_restart` is reported with the result.
   A record that spans a restart is still evidence; it simply may not be
   read as one continuous observation.

**If gate 1 fails, the window is not spent.** That is the whole point of
having it.
