# Freshness and execution telemetry: what is measurable, and what the
# disarmed path can produce on its own

PR #208, PRIORITY 1. The six measurements the owner asked for, the
instrumentation added for them, and an honest statement of which the
disarmed path can answer today.

**The 6.4–6.9 s recorded ladders are not used as evidence anywhere in this
document.** They are the reason it exists: the sampling interval of the
recorded books is three times the 2.0 s freshness limit, so no replay on
them can speak to whether that limit is met. Everything below is either
measured on the live process's own clock or explicitly labelled a bound.

---

## 1. The defect found, and fixed

`l5_book_completion.decide` recorded `stream_age_s` **only inside the
`STALE_BOOK` refusal.** A decision that *passed* the freshness test — the
only case in which we act on the book — carried no record of how fresh
that book was.

So `TS.MAX_BOOK_AGE_S = 2.0` was auditable only in the cases where it had
already refused, and unauditable in every case where it mattered. The age
is now written into the decision record before the test, so it is carried
on **every** outcome. One reporting-only line; no behaviour change, and a
test pins each direction (pass, stale, and a future-dated negative age).

## 2. The six measurements, and their status

| # | measurement | status | where |
|---|---|---|--:|
| 1 | first-leg fill **detection** latency | **NOT MEASURABLE** — bounded only | §3 |
| 2 | book age when the controller evaluates | measurable; now on every decision | §1 |
| 3 | first-leg fill → second-leg quote decision | measurable, against **our** detection | §4 |
| 4 | decision → submission eligibility | measurable | §4 |
| 5 | why second-leg quotes are refused or delayed | measurable, paired with the age | §5 |
| 6 | observer continuity across rollover | measurable | §6 |

`src/l5_completion_latency.py` is a pure per-market accumulator: no I/O, no
clock of its own, no transport, no decision, every input passed in by the
caller that already holds it. `_latency_safe` swallows its faults into a
stat, so a raising accumulator cannot refuse a submission or stop a
cancellation. It reports on the existing heartbeat under
`completion_latency`. 41 tests; 223 pass across every touched path.

## 3. Item 1 is a bound, and saying otherwise would be wrong

The venue's order-progress payload carries **no timestamp**. Its fields are
`coid`, `already`, `cum`, `delta`, `terminal`, `state`, `venue_order_id`,
`market`, `event`, `avg_px`, `commission`, `cum_qty`, `market_identity` —
and `apply_fill` stamps no time of its own either.

So the interval between *the venue filling us* and *our learning of it*
cannot be derived from any data this system holds. It is bounded above by
the order-poll cadence and by nothing narrower.

`note_poll` therefore measures the **realized** cadence rather than
restating `ORDER_POLL_S = 6.0`, because the deployed interval stretches
with the number of live orders
(`max(ORDER_POLL_S, n / ORDER_POLL_RATE)`). The report gives the median,
p95 and max gap, so the bound is a measurement.

**What would make item 1 measurable:** a venue-side fill timestamp on the
order-progress response, or a fill-event stream. Neither exists on this
transport today. Until one does, any "fill detection latency" figure this
program quotes is the poll bound, and must be labelled as such.

### And the earlier latency finding stays withdrawn

My previous "3.5–9.6 s ACK latency / joining the queue late" finding mixed
two clocks — the process clock in `body.t` and the collector clock in the
log line's `ts`, which lag each other by 0.029–9.641 s. It was withdrawn
and is not revived here. Submit→ACK is **not** measurable from that source.

## 4. Items 3 and 4 are measured against our own detection

Item 3 is `first sighting of a held position → completion decision`. The
first term is *our* detection, not the fill, so the interval is the part of
the delay we control. Combined with §3's bound, the true fill→decision
interval is `[measured, measured + poll gap]`.

Item 4 is `decision → submittable`. While disarmed, eligibility is
recorded with `submitted: false`, and the record says so. **Nothing in this
instrumentation can submit anything.**

## 5. What the disarmed path can produce TODAY — and the result is itself a finding

Read from the live heartbeat on deployment `4cec3f66` (commit `6116675ed`),
2026-11-08, read-only:

| stat | count |
|---|--:|
| `book_completion:ORIGIN:UNKNOWN` | 11,372 |
| `book_completion:NO_INVENTORY` | 4,360 |
| `book_completion:DECIDE_COOLDOWN` | 4,334 |
| `book_completion:NO_ACTION_YET` | 2,678 |

**Every one of those exits before the book-freshness check.** So:

> **No completion decision in production currently reaches the freshness
> test at all.** The 2.0 s limit is not being met or missed — it is never
> being evaluated.

That is the same shape as the replay's finding that 5 of 7 holds had **zero**
completion evaluations: the binding constraint is not fill latency, and not
book staleness. It is that the controller is almost never in a state where
it evaluates anything.

Consequently, deployed disarmed, this instrumentation will populate:

- **item 5 immediately** — every decision is noted with its reason, so the
  refusal histogram becomes a time series rather than a cumulative counter,
  and `book_age_at_decision_s.n == 0` is the honest headline;
- **item 6 immediately** — the observer heartbeat is taken above every
  guard, so continuity across a rollover is recorded whether or not there
  is anything to observe;
- **items 1–4 only once inventory exists.** No live order means no poll to
  time and no held leg to detect.

This is the precise, previously unmeasured gap that only a live window can
close, and it is now instrumented *before* any window is spent — which is
what my own earlier pre-flight proposal was too weak to guarantee.

## 6. Item 6, and why the heartbeat sits above every guard

`calibration_live_tick` returns early when there are no live orders. If the
observer heartbeat sat below that, "the observer continued across the
rollover" could not be distinguished from "the observer stopped and there
happened to be nothing to observe". So the heartbeat is taken **first**,
keyed to the observer itself rather than to the market that just changed,
and a rollover is read as a change in the transport's slug.

Three tests pin the cases that matter: a slug change records exactly one
rollover and observation continues; the same slug twice is not a rollover;
and a transport that has not yet resolved a market is not charged a roll on
its first tick. Continuity reads **false** before any rollover rather than
vacuously true.

The heartbeat is looked up with `getattr` rather than called by name:
`calibration_live_tick` documents that a partially built object cannot make
it raise, and a bare call broke seven synthetic-transport rigs the instant
it was added. That is the contract catching the mistake, not a reason to
weaken the rigs.

---

## 7. Addendum to the one-window proposal

`ONE_WINDOW_MAKER_ONLY_PROPOSAL.md` stands, with two changes of emphasis
this work forces. **It remains a proposal and not an authorization.**

**(a) Its deliverable is measurement, not profitability.** The economic
classification puts the gap to profitable matched-pair execution at
**+0.0651/pair — 6.5 ticks** at the measured completion rate. One window
cannot resolve a gap that wide, and nothing should be read as if it could.
What one window *can* do, which no replay can, is populate items 1–4 above
and establish our own queue position.

**(b) A pre-registered freshness refusal, independent of the economics.**
If the window produces completion evaluations whose **p95 book age exceeds
2.0 s**, the completion controller cannot be trusted to act on the book at
all, and that is a blocker on its own — separate from, and prior to, the
6.5-tick economics. The instrumentation now makes that readable from the
window's own record rather than inferred.

**(c) The cheaper option first.** Because item 5 and item 6 populate while
disarmed, the next verification does **not** need a live window: deploying
this instrumentation disarmed produces the refusal-reason time series and
the rollover-continuity record for free. The live window should be spent
only after that data has shown the controller reaching evaluations at all.

## 8. Provenance

- `src/l5_completion_latency.py`, `tests/test_l5_completion_latency.py`,
  `tests/test_l5_latency_wiring.py` — the accumulator and its 41 tests.
- `src/l5_book_completion.py` — the reporting-only age fix.
- `src/l5_pmus_live_runner.py` — four observation points and the heartbeat.
- `mcp__Railway__get-logs` on deployment `4cec3f66`, read-only, for §5.
- `analysis/l5/issue208/ECONOMIC_CLASSIFICATION.md` for the 6.5-tick gap.
