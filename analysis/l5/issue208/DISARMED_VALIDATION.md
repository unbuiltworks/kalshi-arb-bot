# The freshness telemetry, validated in disarmed mode

The owner's step after the instrumentation landed: **validate it disarmed
before any deployment.** Done offline, with no Railway change and no
deployment. 14 tests drive the **real** method bodies — `Runner`'s
`maybe_book_completion`, `poll_orders` and `calibration_live_tick` — bound
to a stub self carrying only the attributes they touch, against a real
`InventoryStore` holding a real one-sided UP position and a book at a
known stream age.

## What came out

A single harness run: eight ticks on a 0.4 s book, then two on a 6.8 s
book, then five observer heartbeats spanning one window roll.

```
book_age_at_decision_s     n 10   median 0.4   p95 6.8   max 6.8
detection_to_decision_s    n 10   median 27.5  p95 49.5  max 49.5
decision_reasons           PAIR_ABOVE_REDEMPTION 8, STALE_BOOK 2
decisions 10   actionable_decisions 0
poll_gaps_s                n 0            (no live order in this arm)
decision_to_eligible_s     n 0            (nothing became submittable)
fill_detection_latency     measurable: false, bounded_by: order_poll_cadence
observer: rollovers 1, before 3, after 2, continued_across_rollover true
```

**The median/p95 split is the result that matters.** `median 0.4` with
`p95 6.8` is a book-age distribution that meets the 2.0 s requirement
typically and misses it at the tail. A single average, or a replay on
6.4–6.9 s ladders, reads that as either uniformly fine or uniformly
broken. This is the distinction the instrumentation was built for, and it
is now measurable from the path's own clock.

In a separate arm with one ledger order, real polls at 10.0 / 16.2 / 22.5 /
35.0 s give `poll_gaps_s n 3, max 12.5` — the realized cadence, which is
the only bound available on fill→detection. (A first poll at t=0 is
correctly skipped: `last_order_poll` defaults to 0.0, so the interval floor
reads it as already polled.)

## Each of the six, as validated

| # | measurement | validated by |
|---|---|---|
| 1 | fill detection latency | declares itself unmeasurable; the poll cadence is measured from real `poll_orders` calls |
| 2 | book age at evaluation | recorded on decisions that **pass** freshness, on stale ones with their age, and the p95 separates the tail |
| 3 | detection → decision | stamped on the first tick that sees the position; a market we hold nothing in reports no detection and no false interval |
| 4 | decision → eligibility | never recorded as `submitted` here, because nothing here can submit |
| 5 | refusal reasons | `PAIR_ABOVE_REDEMPTION` and `STALE_BOOK` counted separately, each paired with the age it was reached on |
| 6 | observer continuity | one real slug change across `calibration_live_tick`, counted either side; and the heartbeat is taken with **no live orders at all** |

## What the harness cannot do, asserted

- The transport exposes no `submit`, `place`, `order`, `cancel` or `amend`.
- The executor stub has no `handle` — the only door from this method to a
  submission.
- The held leg's cost basis makes the complement dearer than the 0.96 cap,
  so every decision refuses on **economics** after passing the freshness
  check. A test asserts no decision returns `ok: true` and the re-entry is
  never counted. That is both the realistic production case and what makes
  the containment provable rather than asserted.
- With no inventory and no live orders, **only the observer populates** and
  `markets` is empty. That is the honest disarmed picture from
  `FRESHNESS_TELEMETRY.md`, now a test: if items 1–4 ever appeared in that
  arm they would be measuring something that did not happen.

## And the telemetry cannot interfere

Two identical harnesses, one with a deliberately exploding accumulator:
the `book_completion:*` decision histograms are **equal**, and the broken
one additionally carries `latency_error:*`. The decisions do not move.

## One pre-existing observability note, reported not fixed

The `L5_MISSED_PAIR` refusal record emitted on this path already carried
`evidence.stream_age_s`, so a *refused* completion could always be audited
for freshness. What it could not do is speak for a decision that **passed**
— that record is emitted only when a completion is decided against. So the
gap my change closes is specifically the passing case, and the claim is no
broader than that.

Separately, the record reads `evidence.basis: EVIDENCE_BASIS_UNKNOWN` with
`book_source: null` and `"book source not stated"`. In this harness that is
the stub book lacking a source field, not a defect; in production it is
worth checking whether the real snapshot states its source.

## Provenance

`tests/test_l5_latency_disarmed_validation.py` — 14 tests, no order, no
venue call, no network, no Railway change. Full-suite parity at the
preceding commit: 20 failed / 18,273 passed against the `ec97d8ef2`
baseline's 20 / 18,206, same 20 IDs, zero introduced.
