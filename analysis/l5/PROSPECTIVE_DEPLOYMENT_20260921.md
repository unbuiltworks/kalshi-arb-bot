# Prospective L5 full-L2 recorder — durable deployment

**Auth constants remain `None`. No live orders. No selector. No P&L read.**

## Where it runs, and why there

| | |
|---|---|
| service | `l5-live-habitat` (`db9e951c-98c1-459c-aae5-fe7adc6e147e`) |
| deployment before | `62a54463-b441-4f3a-a723-9545e6f368c9` |
| volume | `l5-live-habitat-state`, 50 GB, mounted `/state/l5-live` |
| recorder root | `/state/l5-live/prospective/` |
| restart policy | `ALWAYS`, plus the service's own per-thread supervisor |

It runs as a **supervised thread inside the existing durable collector**, not as
a new service. The owner's standing constraint is "don't create more Railway
services", and everything it needs — a durable volume, a 24/7 restart policy and
a per-thread supervisor that already restarts a crashed loop — exists on this
container. It has its own subdirectory and shares no file, no token set and no
state key with the collector threads.

**This deploy is not a response to the pmus_flow incident.** The owner's
instruction was not to restart or redeploy for that. The liveness-map fix rides
on this deploy because a redeploy was already required to ship the recorder.

## The image's isolation property is preserved, not weakened

The Dockerfile's claim is that no executor, session, signer, credential reader
or order transport can enter the image, **because none is copied into it**. The
copy list grew from one file to two, so the claim now has a test rather than a
comment: `tests/test_habitat_image_isolation.py` (11 tests) asserts that

- every copied module imports **stdlib only** (the recorder imports exactly
  `bisect, collections, concurrent, datetime, json, os, ssl, threading, time,
  typing, urllib`);
- an intra-repo import must name a module that is itself on the copy list;
- the Dockerfile copies **exactly** the audited modules — widening it fails the
  suite until the new file is audited and listed;
- no `sign` / `submit` / `place_order` call, no `urlopen(data=)` or
  `Request(method=)` (i.e. no non-GET), no `PRIVATE_KEY` / `REQUIRED_AUTH_ID` /
  `AUTHORIZED_SHA` / `AUTHORIZED_ENVELOPE` definition, and no read of
  `PMUS_SPRING_LIVE` / `_ARM` / `_SESSION_ID` / `_GATE_EVIDENCE`.

These are **AST checks, not substring scans**. Substring scanning has produced
false positives here twice: `POST_FILL_GRACE_S` contains "POST", a docstring
saying "no signer" contains "signer", and — caught while writing this suite —
`_LIVE` matched `L5_LIVE_ROOT`, which is the collector's own volume path.

## Volume discipline, measured rather than assumed

The first smoke run wrote **30.7 MB of `raw_fills` in two minutes**. The cause
was not volume of information: `/trades?user=` re-serves L5's newest 500 fills
on every poll, so at a 3 s cadence the same rows were being written ~28,800
times a day — about **17 GB/day** for `raw_fills`, **6 GB/day** for
`raw_prints`, **2 GB/day** for `raw_books`, against a 50 GB volume already
holding 2.7 GB and shared with four growing collector streams.

Three changes, none of which drops a distinct observation:

1. **Write each raw row once.** `raw_fills` and `raw_prints` persist only rows
   not already written, keyed by identity. A poll that returns nothing new
   still writes a row carrying `n_returned` and `n_new: 0`, so *"we did not
   look"* and *"we looked and there was nothing"* stay different facts.
2. **Write books on change.** The venue stamps each book with a `hash`; an
   unchanged hash is a ladder already on disk. A keepalive row every 60 s
   records that the book was read and found unchanged. **The in-memory ring
   still receives every poll**, so each event's `pre_fill_book`, `queue_trail`
   and `post_fill_book` are unaffected.
3. **Rotation and a disk floor.** The raw firehoses rotate at 3 GB (one
   generation kept, so ~18 GB steady state against a 50 GB volume whose
   collector streams are unbounded and already at 2.7 GB); `events.jsonl`, `rotations.jsonl` and `gaps.jsonl` are
   **never** rotated. Below 2 GB free the raw streams are shed while events keep
   being written, and the transition is recorded in `gaps.jsonl` rather than
   happening silently. `l5-exec-shadow` hit 100% disk before; a collector that
   dies of its own logging is the failure this prevents.

The dedupe sets are FIFO-capped (200k fill keys, 400k print keys) so the disk
fix does not become a memory leak. An evicted key can cause one row to be
written twice — a duplicate is auditable, a dropped observation is not.

## What each maker fill now carries

Every quantity is computed **independently** from a raw row preserved in the
same record, so any derivation can be re-audited without re-fetching.

| # | requested | field(s) |
|---|---|---|
| 1 | apparent depth ahead | `depth_ahead_at_first_look`, `depth_ahead_at_last_look`, `depth_ahead_of_fill_price` |
| 2 | depth at the exact price | `depth_at_fill_price`, `depth_at_price_at_last_look` |
| 3 | cancellations / depth disappearance before the fill | `queue_trail` (raw), `queue_trail_scalars`, `depth_ahead_disappeared`, `depth_drop_not_explained_by_trades` |
| 4 | traded volume at/through the price after lag backfill | `queue_consuming_volume`, `queue_consuming_prints`, recomputed in `backfill_flow` |
| 5 | price movement through the fill | `bid_move_through_fill`, `ask_move_through_fill`, `mid_move_through_fill` |
| 6 | snapshot age at the fill | `pre_snapshot_age_s`, `last_look_age_s`, `unobserved_interval_s` |
| 7 | fill-timestamp uncertainty | `fill_time_lo`, `fill_time_hi`, `fill_time_uncertainty_s` |
| 8 | complement-side arrivals | `complement_arrival_volume`, `complement_arrival_prints` |
| 9 | PositionSplit context | `position_split_context` (joined offline), `position_split_scope` |
| 10 | multiple L5 fills atomically | `atomic_group_key`, `atomic_group_size`, `atomic_group_both_outcomes` |

Plus the raw rows themselves: `pre_fill_book`, `queue_trail`,
`post_fill_book`, `window_prints` (unfiltered, timestamped).

**Raw is preserved separately from derived.** `raw_books.jsonl`,
`raw_prints.jsonl`, `raw_fills.jsonl` hold source records as the venue returned
them; `events.jsonl` holds derived features. Raw is never rewritten from
derived, and `annotate_atomic` / `annotate_chain` copy rather than mutate —
tested.

## Two defects the first durable smoke run caught

Both were invisible to the bounded runner and both are now pinned by
regressions in `tests/test_queue_reconciliation.py`:

1. **`outcomeIndex=999`.** The venue served a fill with an outcome index
   outside `{0, 1}`. `MarketState` held a fixed `{0: .., 1: ..}` dict and raised
   `KeyError` inside the recorder thread; the supervisor restarted it, which
   reset the seed and discarded the whole book ring. One odd row from a venue
   cost minutes of coverage. The inventory is now a `defaultdict`.
2. **The queue trail was empty by construction, and the symptom was silent.**
   It was first taken as the snapshots *between* the pre-fill book and the fill
   — but `pre` is by definition the newest snapshot before the fill, so that
   window is always empty. Every event read `trail: 0`, and the whole
   cancellation/disappearance measurement was degenerate while looking like a
   fact about quiet markets. The trail now **ends at** the pre-book and reaches
   60 s backwards from it.

A third inconsistency was caught while wiring the backfill: it recomputed the
reconciliation terms over the *narrow* flow window `(pre_book, fill]` rather
than the reconciliation window `(first_look, fill]`, which would have made a
backfilled event disagree with the same event at capture time over the same
prints. The two windows are now named and kept separate — `window_prints_t0`
for the reconciliation, `flow_window_t0` for the H-FLOW numerator.

## A fourth defect, and why it matters most

Reconciling the smoke-run events as a backward-compatibility check produced
**4 of 4 scorable fills `UNEXPLAINED`** — a 100% rate, and exactly the shape of
result that would have looked like H-QUEUE-MISMEASURED confirming itself at
first light.

It was an artefact of the instrumentation, start to finish. Those events came
from the degenerate-trail build, so each had a single book look. With one look
the observed span collapses to zero, the market's own trade rate is
unmeasurable, `plausible_unseen_volume` is null, and `INCONCLUSIVE_GAP` can
never fire — leaving `UNEXPLAINED` as the only reachable verdict for any fill
with a queue in front of it.

`reconcile_fill` now returns `INCONCLUSIVE_GAP` with
`degenerate_observed_interval: True` whenever the rate is unmeasurable:
**claiming the queue could not have cleared requires a rate we do not have.**
Re-scored, the same four fills come out `0.000 (n=4)` unexplained — all
`INCONCLUSIVE_GAP`.

Neither number is a result. n=4, one market, one quarter-hour window, below
every preregistered floor. It is recorded because the difference between the
two runs is entirely our own measurement, and that is the failure mode this
whole exercise exists to avoid.

## Shared rate limits, and what to watch

The recorder polls `clob.polymarket.com` and `data-api.polymarket.com` from the
same container as `intl_books_loop`, which also polls CLOB. Estimated combined
load is order tens of requests per second. Every recorder cadence is therefore
**env-tunable** (`L5_PROSPECTIVE_BOOK_CAD`, `_FILL_CAD`, `_PRINTS_CAD`,
`_DISCOVER_CAD`, `_GRACE_S`) so it can be backed off without a code change or a
rebuild.

**Watch `book_errors` and `intl_books` row rate in the heartbeat after deploy.**
If either degrades, loosen `L5_PROSPECTIVE_BOOK_CAD` first. Note the cost: the
3 s book sweep is what keeps the pre-fill snapshot *near* the fill, so loosening
it widens `unobserved_interval_s` on every reconciliation and pushes verdicts
toward `INCONCLUSIVE_GAP`.

## The reconciliation test

`src/l5_queue_reconciliation.py`. For each maker fill: **can publicly observed
book mutations plus publicly published trades account for the apparent queue
ahead of L5 disappearing before his fill?**

Six verdicts, per fill, defined in the preregistration. Two deliberate guards:

- **Both trade conventions count.** Selling outcome A at `p` and buying outcome
  B at `1−p` hit the same resting order. Counting one would halve the numerator
  and manufacture `UNEXPLAINED` out of bookkeeping. Tested.
- **`INCONCLUSIVE_GAP`.** If the book was last read long enough before the fill
  that *this market's own measured trade rate* could have cleared the queue
  unseen, the fill is inconclusive, not unexplained. Without this, our sampling
  cadence would be scored as a finding.

Volume before the last book look is **not** counted against the residual — it is
already reflected in the depth we measured there. Tested; double-counting it
would explain the same shares twice.

A third guard was added after noticing the reconciliation re-derives its terms
from `window_prints`, which is capped at 1,000 rows at capture. On a market busy
enough to overflow that cap, re-deriving would undercount consuming volume and
report `UNEXPLAINED` **because of a list cap**. The event now carries
`window_prints_truncated` and `window_prints_n_total`, and a truncated window is
a `NO_COVERAGE` refusal rather than a finding. The aggregate
`queue_consuming_volume` stored on the event is still computed over every print,
not the capped list.

## Partitioning and the fitting floor

- Results are reported **per market**. `market_report()` always works and flags
  every market below the floor with its denominator.
- `pooled()` **raises `InsufficientEvidence`** rather than returning a number
  until a market reaches **n=200 scorable** fills; markets below the floor are
  excluded from any pool and listed by name.
- `NO_COVERAGE` and `NO_QUEUE` are excluded from "scorable" — a fill with no
  pre-fill book, or nothing ahead of it, is not evidence in either direction.
- **No fitting at n=16.** The n=16 first-light observations are descriptive
  only and are not the basis of any claim.

## Tests

`tests/test_queue_reconciliation.py` (28), `tests/test_habitat_image_isolation.py`
(11), `tests/test_habitat_livemap_coverage.py` (8), plus the existing
`tests/test_prospective_recorder.py` (21).

## Suite state, and one test left deliberately red

Full suite: **7,283 passed, 2 failed, 27 skipped.** Four tests were failing on
HEAD before any of this work (verified by stashing); two of those are now fixed
because they name the exact service being deployed:

- `test_deploy_fallthrough_guard` — `deploy/l5_live_habitat/` was the **only**
  Dockerfile directory in the repo with no `railway.json`. That guard exists
  because a service without an explicit start command is how a config-less
  service once ran the order-capable Kalshi bot. Manifest added; Railway reads
  its config from the service root, not from there, so it pins intent rather
  than driving the build.
- `test_chain_ca_fallback` — seven modules pinned the agent-proxy CA bundle
  path, **three of them with no fallback at all**, so they would raise in any
  image lacking it. That is the documented cause of three prospective windows
  recording 361 executions with an empty leg list. All seven now fall back to
  the system trust store. Three also needed `import os` added, caught by an
  AST check before commit rather than at container start.

**Left red on purpose:** `test_r1_source_lineage` and `test_lane_a_probe_v0`
report `content_drift` on `analysis/l5/authorization/`, pinned
`c2551101…`, actual `05daf9e0…`. The drift is
`RESTING_LEDGER_REPAIR_20260921.md`, a documentation file added in `319e4935` —
the commit the owner accepted — without the lineage pin being updated alongside
it. `forbidden_class` is **empty**: nothing execution-class changed.

Re-pinning would be me re-blessing a change to an authorization directory on my
own initiative, which is an owner decision, so the check stays red and reports
accurately. **This needs an owner call**, not a code change.

## Not claimed

- Nothing here has been fitted, and no selector exists.
- No rate is reported until the preregistered floor is reached in one market.
- A confirmed H-QUEUE-MISMEASURED would say our queue variable is wrong. It
  would **not** establish any replacement mechanism.
- `depth_ahead` remains an **upper** bound: anonymous depth hides arrival order.

---

## Post-deploy verification (2026-09-21T07:32-07:36Z)

Deployment **`6553d6dd-cd9e-43f2-b5b1-1c92d2d5876d`**, commit `85c8a741`,
status SUCCESS. The previous deployment `62a54463` stayed healthy until cutover
and was never restarted for the incident.

**The liveness-map fix is confirmed in production, and the effect is large.**

| | old build (`62a54463`) | new build (`6553d6dd`) |
|---|---|---|
| `pmus_live_events` on the same slate | **0** | **32** |
| `pmus_flow_selected` | (not emitted) | **42** |
| `pmus_flow` rows/min | **7.5** | **315.0** — **42x** |

The 32 live events are the ones the `/v2/leagues` map could not see. The flow
stream had been observing a few percent of the live slate overnight, every
night, not only during the 2 h 12 m window that made it visible.

Recorder, first four minutes:

```
prospective_seeded  tokens 69, backlog_marked 500 (marked seen, not emitted)
prospective_tokens  21 -> 37
book_unchanged      101          (hash dedupe working)
volume_free_gb      44.02        (stable)
thread restarts     0
prospective_events  0            (L5 quiet in the watched markets; the seed
                                  correctly suppressed the 500-fill backlog)
```

All four collector streams advanced normally throughout; `intl_books` went
169 -> 316 -> 484 across the same heartbeats, so the added request load did not
degrade the sibling that shares CLOB.

### One operational note, not a regression

`book_errors` reached 591 and `tokens_retired` 195 in the first ~200 s. This is
**pre-existing recorder behaviour**, present identically in the bounded smoke
runs (425 / 128 over 420 s) and not introduced by the deploy. The mechanism is a
churn loop: a habitat token whose CLOB book 404s is retired after three errors,
and `refresh_habitat_for_settlement` re-adds it within 15 s, so a just-rolled
window cycles retire -> re-add -> retire until it ages out of the arithmetic
slug set.

It is self-limiting, it did not affect the collector, and `prospective_tokens`
settles at the expected live-habitat count. It is wasteful of request budget and
it makes `book_errors` useless as a health signal, so it is worth fixing — but
as its own change, not folded into a deploy that is already healthy.
