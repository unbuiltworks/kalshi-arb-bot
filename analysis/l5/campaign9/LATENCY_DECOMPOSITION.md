# Where the 18.11 s goes: instrumentation, and what is already provable

`src/l5_latency_trace.py` (new), `tests/test_l5_latency_trace.py` (14
tests). Record-only, no behaviour change, nothing deployed.

## What the existing number actually spans — narrower than I said

`engine_row` computes `latency_s = recv - r["timestamp"]`:
**L5's own event time → our local receipt.** It ends at `handle`, so it
**excludes our decision, our gates and our wire entirely.** My previous
report implied the 18 s might be our batching; that was imprecise. Only two
things are inside it: the feed's indexing and our poll phase.

**And the poll phase is bounded by a constant we already know.**
`L5_LIVE_POLL_S` defaults to **3 s** and `Runner.poll` is called every tick
with no cadence gate, so a row that is queryable can wait at most ~3 s for
us to ask, ~1.5 s on average.

> **Median 18.11 s − at most ~3 s of poll phase ⇒ roughly 15 s is upstream
> of this process.**

That is an inference from the deployed constant, not a measurement, and it
is exactly what the instrumentation is for. But it already bounds the prize:
**the largest fix available on our side is worth about 3 of the 18 seconds**,
and if it holds, L5-action entry is **structurally** late and the answer is
strategic rather than technical.

## What is now recorded

| stage | source | honest status |
|---|---|---|
| `source_event_utc` | the activity row's own `timestamp` | given by the source |
| `feed_served_utc` | the poll's HTTP **`Date`** response header | given by the **server** — when it *answered*, not when it indexed. `None` when absent or unparseable |
| `first_seen_utc` | the first poll in which this `action_key` appeared | **measured by us**, bounded by the poll interval. A bound on visibility, **not** a publication timestamp |
| `receipt_utc` | existing | |
| `evaluated_utc` / `submitted_utc` / `venue_ack_utc` | decision, wire, venue | |

**There is no publication timestamp in this feed**, and none is invented.
`l5_activity` calls `GET /activity?user=…&type=TRADE`; the rows carry a
trade `timestamp` and nothing describing when the row became queryable.
`None` is recorded rather than a guess — a test asserts the module contains
no `time.time()`, `datetime.now` or `utcnow`, so it cannot manufacture one.

Segments, each `None` when either endpoint is missing (zero would be a claim
that there was no delay), each labelled with its owner:

| segment | spans | owner |
|---|---|---|
| `index_lag_s` | source event → first seen | **SOURCE_OR_POLL_PHASE** |
| `serve_lag_s` | feed served → first seen | NETWORK |
| `poll_lag_s` | first seen → receipt | **OURS** |
| `decide_lag_s` | receipt → evaluated | **OURS** |
| `submit_lag_s` | evaluated → submitted | **OURS** |
| `ack_lag_s` | submitted → venue ack | VENUE |

Negative segments are **reported, not clamped**: three machines, three
clocks, and hiding skew turns a measurable problem into a mysterious one.
`first_seen` keeps the *first* sighting and an evicted key reports `None`
rather than a later poll — reporting the later one would understate the very
lag this measures. `summarise` carries `n` beside every median, because
`ack_lag_s` exists only for submitted orders.

## The smallest fix, conditional on the split

- **If `index_lag_s` dominates (expected, ~15 s):** no engineering on our
  side closes it. The fix is not technical — it is that entry timed off L5's
  public action is structurally adversely selected, and entry timing should
  stop depending on it. That is a strategy decision, not a patch.
- **If `poll_lag_s` is material:** lower `L5_LIVE_POLL_S` from 3 s. One
  environment variable, no code change, no limit change, bounded gain ~3 s.
- **If `decide_lag_s` or `submit_lag_s` is material:** they are currently
  *unmeasured* — outside `latency_s` entirely — and would be new
  information.

## Deployed and arming status — actual, not aspirational

Read from the live service at **15:33:19Z**, deployment `e0fc7bc1`, commit
**`202456d3`** on `claude/l5-narrow-campaign-prep` (11:15:05Z, SUCCESS):

| field | value |
|---|---|
| `armed` | **false** |
| `preflight` | PASSED |
| `owner_arm_token` | true |
| `halt` (effective submission gate) | **true** |
| campaign | `approval_present: true`, and **`CAMPAIGN_IDENTITY_UNAVAILABLE` ×52** |
| `last_admitted_utc` | 2026-10-29T15:33:15Z — the L5 feed **is** live |
| market | `cpc-btc-updown-15m-2026-10-29-1530z` |

**Nothing from this session is deployed.** The running commit predates all
of it. `l5_latency_trace.py` and `l5_book_completion.py` exist only on
`claude/l5-wallet-ingestion-fix-kqcqoa`.

Two independent blockers to arming: the **expired campaign document**, and
`halt: true`. At 15:03–15:07Z `halt_latch` read CLEAR while `halt` now reads
true, so the latter is most likely the campaign refusal surfacing through
the effective gate rather than a separate watchdog latch — but I have not
proven that and am not going to assert it.
