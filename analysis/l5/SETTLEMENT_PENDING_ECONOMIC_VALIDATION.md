# Settlement-pending concurrency: read-only economic validation

Campaigns 1 (2026-10-27 0000z–0145z) and 2 (1330z–1515z), 14 window
transitions. Read-only: Railway deploy logs of `l5-pmus-live`
(`87f1834e`, executor pinned at `f6ad0f55`) and of the public read-only
tape `pmus-btc15m-tape` (`1f0c6aaf`). No production change, no wiring,
no campaign.

## Verdict

**NO-GO on further shadow validation of this feature.** The 85–116s that
motivated it is not the concurrency slot. It is a burst of ~125 venue
reads at every window boundary, paced by our own 1.5 req/s token bucket,
which sleeps **inside the tick**. Freeing the slot recovers **at most
4.9 seconds per transition** and the measured upper bound is
indistinguishable from the one unblocked control window.

The feature is correct and harmless. It is also, on this evidence, worth
approximately nothing until the boundary read burst is fixed. That burst
is the real lever and it is a separate change.

## 1. The authoritative expiry source

Three instants get called "the end of the window". Only two are the
venue's.

| Instant | Venue-stated? | Where | Observed |
|---|---|---|---|
| Window **start** | YES | `/v1/events?series_id=375` → `startTime`, confirmed by `OBS.verify_alignment` | per window |
| **Trading cutoff** | YES | market `state` leaves `MARKET_STATE_OPEN` | 1500z: `transact_time` **15:15:00.449Z**, first `MARKET_STATE_CLOSED` row 15:15:06.64Z |
| **Settlement** | YES | terminal state **and** binary `settlementPx` (`transport.settlement`, `TERMINAL_MARKET_STATES`) | 1500z read terminal 15:16:26.35Z, **86s after the cutoff** |
| Window **close** as we use it | **NO — derived** | `OBS.venue_window_bounds`: `end = start + AB.WINDOW_S` (900s) | always exactly 900s by construction |

So the cutoff and settlement are 86s apart and both are venue-stated.
The close our arming path uses is arithmetic anchored on a venue-confirmed
start — good enough for a pre-close margin on a window we are already
trading, **not** an authoritative expiry for a concurrency decision.

One consequence worth naming: `AUTH.window_expiry` guards the span with
`abs(span - nominal_s) > NOMINAL_WINDOW_TOLERANCE_S` and the note "a
contract expiry is not a window close". That guard **cannot fire for the
live producer**, because the same producer builds `end` as
`start + nominal`. It is live only for a producer that supplies a real
venue-stated end. Nothing today does.

### Fail-closed, proven

`tests/test_l5_expiry_source_fails_closed.py` — 15 tests, all passing:

- `venue_window_bounds` returns `None` on `aligned: False` (inconsistent),
  on `aligned: None` (read failed), and on a missing start.
- `AUTH.window_expiry` refuses by name: `WINDOW_BOUNDS_UNKNOWN`,
  `WINDOW_BOUNDS_UNCONFIRMED` (our clock grid is not a venue window),
  `WINDOW_BOUNDS_UNREADABLE`, `WINDOW_BOUNDS_IMPLAUSIBLE` (close before
  open; span that is not this series' window).
- The classifier takes `is True` and nothing else: `None`, `False`,
  `"yes"`, `1` and `"MARKET_STATE_EXPIRED"` all leave the market holding
  its slot.

A market we cannot date keeps its slot. That direction is preserved
everywhere.

## 2. The transition table

`+s` is seconds after the window's 900s boundary. "loop resume" is the
first executor log line after the boundary read burst. "cash left" is
`max_cash_deployed_usd` ($12.00, `NO_RECYCLING`) less the prior market's
unreleased `cost_total`.

| new | prev | prev settled +s | loop resume +s | first order +s | order−resume | cash left $ |
|---|---|---|---|---|---|---|
| 0015z | 0000z | 108.1 | 104.6 | 109.3 | 4.70 | 8.67 |
| 0030z | 0015z | 84.4 | 84.4 | 84.4 | 0.01 | 10.28 |
| 0045z | 0030z | 89.0 | 84.2 | 89.6 | 5.36 | 10.12 |
| **0100z** | **0045z** | **n/a — no exposure** | **94.1** | **96.9** | **2.79** | **12.00** |
| 0115z | 0100z | 112.4 | 103.4 | 112.8 | 9.43 | 10.12 |
| 0130z | 0115z | 86.3 | 86.3 | 86.3 | 0.01 | 9.78 |
| 0145z | 0130z | 86.0 | 84.8 | 86.5 | 1.72 | 8.60 |
| 1345z | 1330z | 87.2 | 87.2 | 87.2 | 0.01 | 10.02 |
| 1400z | 1345z | 86.8 | 86.8 | 87.1 | 0.36 | 11.16 |
| 1415z | 1400z | 108.0 | 103.8 | 108.8 | 5.02 | 9.62 |
| 1430z | 1415z | 115.6 | 114.6 | 116.1 | 1.54 | 11.38 |
| 1445z | 1430z | 108.7 | 102.5 | 109.4 | 6.91 | 10.16 |
| 1500z | 1445z | 113.8 | 113.8 | 142.4 | 28.63 | 7.93 |
| 1515z | 1500z | 86.3 | 86.3 | 86.3 | 0.01 | 7.15 |

Prev settled: n=13, 84.4–115.6s, median 89.0.
Loop resume: n=14, 84.2–114.6s, median 90.7.
First order: n=14, 84.4–142.4s, median 93.2.

## 3. Why the slot is not the constraint

**The executor is not running during the blocked interval.** At every
transition the loop emits nothing for 70–90s. The 60s heartbeat skips a
beat across the boundary — 1515z: 15:13:20, 15:14:20, *(missing)*,
15:16:29; 0015z: 00:13:57, 00:14:59, *(missing)*, 00:16:57.

The cause is measured, not inferred. Heartbeat rate-limiter counters,
across the boundary:

| | requests | throttled_waits | total_wait_s | http_429 | in_cooloff |
|---|---|---|---|---|---|
| 1515z 15:14:20 → 15:16:29 | 1233 → 1354 (**+121**) | 885 → 996 (**+111**) | 479.62 → 540.76 (**+61.14s**) | 0 | false |
| 0015z 00:14:59 → 00:16:57 | 324 → 452 (**+128**) | 219 → 330 (**+111**) | 117.83 → 177.98 (**+60.15s**) | 0 | false |

No 429s and no cool-off. This is `VenueRateLimiter.before()` calling
`self._sleep(wait)` on the tick thread: ~125 reads at `rps=1.5` is ~60s of
pacing plus ~20s of round trips, which accounts for the whole gap. The
settlement read is one of those ~125 and lands near the end of the burst,
which is why retirement and the first order arrive together.

**Three independent confirmations that the slot is not binding:**

1. **The control window.** 0045z took fills in no leg, so it held no
   `committed` and 0100z had **no settlement-pending market to clear**.
   0100z still took **96.9s** to its first order — inside the range of
   the 13 blocked transitions (84.4–142.4) and above their median. n=1,
   but it is a direct observation, not a model.
2. **Arming is downstream of the burst.** `L5_LIVE_ARMED` fires at the
   end of the burst in all 14 transitions, 0.0–5.3s before the first
   order. Preflight's venue reads are inside the burst, so no opening
   order could have passed the gates earlier whatever the concurrency
   count said.
3. **The upper bound.** The loop could not have acted before it resumed,
   so recoverable time ≤ (first order − loop resume): **63.7s total over
   13 transitions, mean 4.90s, median 1.72s**. That bound is generous —
   it includes 1500z's 28.6s, which elapsed *after* settlement had
   already cleared the gate and therefore cannot be the gate's doing.
   Excluding it: 35.1s total, **mean 2.92s**, against the control's
   2.79s.

## 4. Capital

Never binding. `max_cash_deployed_usd` $12.00 and `cash_policy
NO_RECYCLING` in both campaigns; per-round envelope
`max_worst_case_deficit_usd` $3.00; venue buying power $338.08 (C1) and
$341.35 (C2).

Cash left while the prior settlement was pending: **$7.15–$12.00, median
$10.07**. **14/14 transitions had at least 2.4× the $3.00 round
envelope.** The unsettled commitment is correctly still charged and the
account-wide `cash_basis` still counts it; there was simply room beside it.

## 5. What the recordings cannot support

**No authenticated book exists for any blocked interval.** The executor's
authenticated websocket mirror is the only authenticated source and the
process emitted nothing while it was asleep in the limiter — no decisions,
no book snapshots, no shadow records, in all 14 transitions.

Public tape coverage of the new window inside the blocked interval is
**partial and inconsistent**:

- **1430z — covered.** 14:30:32 bid 0.58/ask 0.59 two-sided,
  `shares_traded` 4,716.84; 14:31:01 bid 0.65/ask 0.66, 12,530.14;
  14:31:32 bid 0.66/ask 0.67, 17,049.21. So ≥12,332 shares traded on the
  venue before our first order at 14:31:56.
- **1515z — not covered.** `SERIES_RESLATE_S = 45.0`, so the tape moved
  to 1515z only at 15:15:39.66, then returned a 404 at 15:16:41. Its
  first usable 1515z row is `t = 15:16:33`, *after* our first order.

Even where covered, the tape is public, carries **no fills** (PMUS serves
no public trade endpoint), and reports displayed depth rather than queue
position. **These recordings cannot support fill or settlement economics
for the recovered interval, and I am not extrapolating profit from them.**
The point is moot in any case: there is no usable recovered interval to
evaluate an opportunity in.

Answering the six questions as asked, per transition: (1) in the table;
(2) the loop-resume column, which is the earliest the executor could
evaluate anything; (3) the cash column, $7.15 minimum against a $3.00
envelope; (4) **no** — arming had not completed; (5) **no authenticated
data exists**, public coverage partial; (6) **no** — in 5 of 13
transitions the first order landed within 0.01s of settlement anyway.

Recovered eligibility, an executable order, and a maker fill stay three
different things here. We measured the first and found it ≈0. We never
reached the second, and the third is not in the data at all.

## 6. What this does not say

- It does not say the feature is wrong. Parity is identical by name and
  the 33 tests pass. It says the feature is **inert in economic terms**
  while the boundary burst dominates.
- It does not clear PMUS venue permission for ordering in window N+1
  while N is unsettled. Still **unverified**; still a hard blocker for
  any live use.
- One control window is n=1. The upper-bound argument in §3.3 does not
  depend on it.

## 7. The lever this uncovered

~125 paced venue reads per window boundary, ~60s of which is our own
synchronous sleep on the money-path tick. Candidates, in rough order of
value per unit of risk — none implemented, none authorized:

1. Do not re-run full preflight from scratch at every boundary when the
   series, account and venue facts have not changed.
2. Move the alignment pagination (`verify_alignment`, up to 40 sequential
   GETs, measured 8.21s today) off the tick, or seek the series' tail
   instead of paging from offset 0.
3. Let the limiter's wait yield instead of blocking the tick, so the
   first evaluation of a new window is not behind the whole read queue.

Each is a change to the money path and each needs its own parity run.
Reported here as findings only.
