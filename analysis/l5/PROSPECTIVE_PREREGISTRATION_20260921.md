# Preregistration — prospective L5 execution observation

Written **before** any prospective observation was analysed and before anything
was fitted. Recorder: `src/l5_prospective_recorder.py`. Schema
`l5_prospective_event/v1`.

**No selector is built here. No threshold is chosen here. No P&L is read.**

## Feature table

One immutable record per observed L5 fill. Raw source records are written to
`raw_books.jsonl`, `raw_fills.jsonl`, `raw_prints.jsonl`; derived events to
`events.jsonl`. Raw is never rewritten from derived.

| field | source | timing uncertainty |
|---|---|---|
| `conditionId`, `asset`, `slug`, `outcome`, `outcome_index` | data-api/trades | — |
| `transactionHash` | data-api/trades | — |
| `timestamp` (+ `timestamp_resolution_s`) | data-api/trades | **whole seconds** |
| `price`, `size`, `side` | data-api/trades | as above |
| `is_taker`, `maker_taker_available` | `/trades?takerOnly` subset membership | as above |
| `action` (OPEN / EXTEND / OPPOSITE) | derived from earlier fills only | inherits |
| `run_before` | derived | inherits |
| `inv_0_before`, `inv_1_before`, `residual_before` | derived | inherits |
| `secs_to_settlement` | gamma `endDate` | market metadata |
| `pre_fill_book` (full ladder) | clob/book, **strictly before** | snapshot age recorded |
| `post_fill_book` (full ladder) | clob/book, first after | snapshot age recorded |
| `touch_bid`, `touch_bid_size`, `touch_ask`, `touch_ask_size` | pre-fill book | as above |
| `depth_at_fill_price` | pre-fill book | as above |
| `depth_ahead_of_fill_price` | pre-fill book | **upper bound** (see below) |
| `rested_at_touch`, `ticks_behind_touch` | pre-fill book | as above |
| `recent_prints`, `arriving_volume_since_pre_snapshot` | global tape | whole seconds |
| `arriving_volume_whole_market` | global tape | whole seconds |
| `arriving_flow_over_depth_ahead` | **primary measurement** | inherits both |
| `chain` (PositionSplit etc.) | polygon receipt | joined post hoc |
| `provenance{...}` | every joined source | per-source age + resolution |

Added for H-QUEUE-MISMEASURED. Each is computed **independently** from a raw row
that travels in the same record, so every derivation can be re-audited without
re-fetching anything:

| field | source | what it answers |
|---|---|---|
| `queue_trail` | clob/book, every snapshot in (pre, fill] | raw; the book's mutations, preserved |
| `queue_trail_scalars` | derived from `queue_trail` | depth ahead / at price at each look |
| `depth_ahead_at_pre`, `depth_ahead_at_last_look` | pre-book, last look | (1) apparent depth ahead |
| `depth_at_price_at_last_look` | last look | (2) depth at the exact price |
| `depth_ahead_disappeared`, `depth_drop_not_explained_by_trades` | trail + tape | (3) cancellation / depth disappearance |
| `queue_consuming_volume`, `queue_consuming_prints` | tape, **both conventions**, after backfill | (4) traded volume at/through the price |
| `bid_move_through_fill`, `ask_move_through_fill`, `mid_move_through_fill` | pre vs post ladder | (5) price movement through the fill |
| `pre_snapshot_age_s`, `last_look_age_s`, `unobserved_interval_s` | snapshot times | (6) snapshot age at the fill |
| `fill_time_lo`, `fill_time_hi`, `fill_time_uncertainty_s` | venue resolution | (7) fill-timestamp uncertainty |
| `complement_arrival_volume`, `complement_arrival_prints` | tape, opposite outcome | (8) complement-side arrivals |
| `position_split_context` (+ `position_split_scope`) | polygon receipt, joined offline | (9) PositionSplit **context only** |
| `atomic_group_key`, `atomic_group_size`, `atomic_group_both_outcomes` | transactionHash, joined offline | (10) multiple L5 fills in one transaction |
| `window_prints`, `window_prints_t0/t1` | tape | raw; the unfiltered window the above are derived from |

Two standing caveats, recorded in every event:

- **`depth_ahead_of_fill_price` is an UPPER bound.** Anonymous depth hides
  arrival order, so part of the same-price queue may have posted *after* L5.
- **A null is never a zero.** Unmeasured fields carry an explicit reason
  (`pre_fill_book_reason`, `arriving_flow_over_depth_ahead_reason`, …). Zero
  depth and an unread book are different facts.

## Primary hypothesis — H-QUEUE-MISMEASURED

**Supersedes H-QUEUE-IRRELEVANT, which is withdrawn.** H-QUEUE-IRRELEVANT
asserted that L5's maker fills are not gated by the same-outcome FIFO queue *at
all*, and that complement-buyer arrival drives them instead. That was an
over-commitment: it asserted a mechanism from n=16 observations in one market in
one hour, when those same observations are equally consistent with our having
measured the wrong quantity. It is replaced by the weaker, better-posed claim
that the data can actually adjudicate.

> **H-QUEUE-MISMEASURED: `depth_ahead_of_fill_price`, as computed from the
> public CLOB ladder, does not represent the effective queue that governed L5's
> maker fill.**

**The decisive observable.** For each maker fill, take the last public look at
the book strictly before the fill. Ask whether the depth still apparently ahead
of L5 at that moment is accounted for by *publicly observable* events in the
interval between that look and the fill:

```
residual_at_last_look  =  depth ahead of his price at the last snapshot
consuming_volume_gap   =  published volume in (t_last, t_fill] that consumed
                          resting bids at or ahead of his price
shortfall              =  residual_at_last_look  -  consuming_volume_gap
```

`consuming_volume_gap` counts **both trade conventions** — selling outcome A at
`p` and buying outcome B at `1−p` hit the same resting order, and the tape
reports whichever leg the taker took. Counting one convention would halve the
numerator and manufacture a shortfall out of bookkeeping.

Separately, over the interval we *did* watch:

```
observed_drop          =  depth ahead at the pre-snapshot − at the last look
cancellation_implied   =  observed_drop − consuming volume over that interval
```

**Per-fill verdicts** (`src/l5_queue_reconciliation.py`, one of exactly six):

| verdict | meaning |
|---|---|
| `NO_COVERAGE` | no pre-fill book, or the tape had not published the window |
| `NO_QUEUE` | under 1 share was ahead of him; constrains no queue model |
| `ACCOUNTED_BY_TRADES` | published trades cover the queue that was ahead |
| `ACCOUNTED_WITH_CANCELS` | residual covered, but depth left earlier with no print |
| `INCONCLUSIVE_GAP` | the book was last read long enough before the fill that this market's **own observed trade rate** could have cleared the queue unseen |
| `UNEXPLAINED` | **the H-QUEUE-MISMEASURED signature** |

`INCONCLUSIVE_GAP` exists to stop our own sampling cadence from being scored as
a finding. The plausibility bound is the market's measured rate over its own
pre-window, not a constant. When that rate is **unmeasurable** — a single book
look, so the observed span collapses to zero — the verdict is also
`INCONCLUSIVE_GAP`, carrying `degenerate_observed_interval: True`. Claiming the
queue could not have cleared requires a rate we do not have, and without this
rule `UNEXPLAINED` is the only reachable verdict for any single-look fill with
a queue in front of it. That is not hypothetical: it produced a 4-of-4
"unexplained" reading at first light which was entirely instrumentation.

**Prediction that can fail:** across at least 200 scorable maker fills *within a
single market*, `UNEXPLAINED` is a substantial and repeatable share of verdicts,
and the fills that draw it are not concentrated in the ones with the longest
unobserved intervals.

**What falsifies it:** `UNEXPLAINED` is rare once both trade conventions and
`INCONCLUSIVE_GAP` are applied — i.e. public mutations plus public trades *do*
account for the queue clearing, and the public ladder is an adequate measure of
the effective queue. Also falsified if `UNEXPLAINED` correlates with
`unobserved_interval_s` rather than with market state, which would make it an
artefact of cadence.

**What H-QUEUE-MISMEASURED deliberately does NOT claim.** It does not name a
replacement mechanism. Confirming it says our queue variable is wrong; it does
**not** license "complement arrival drives fills", "there is a hidden queue",
"PositionSplit confers priority", or any other mechanism. A mechanism needs its
own hypothesis and its own evidence.

**Partitioning, fixed now.** Results are reported **per market**. No pooled rate
is computed until at least one market reaches n=200 scorable fills;
`l5_queue_reconciliation.pooled()` raises `InsufficientEvidence` rather than
returning a number, and markets below the floor are excluded from any pool and
listed by name. **No fitting at n=16.**

## Secondary hypothesis — H-FLOW (retained, tested second)

> **H-FLOW: arriving executable flow relative to resting depth ahead of L5
> explains L5 maker-fill occurrence substantially better than our previous
> queue-depth model.**

H-FLOW presumes the denominator is the binding quantity, which is exactly what
H-QUEUE-MISMEASURED puts in question, so it is tested **after**.

`arriving_flow_over_depth_ahead = (executable volume arriving between the
pre-fill snapshot and the fill) / (resting size at or better than L5's price)`.

Executable means **same outcome, price ≤ L5's price** — flow that could
actually have reached him. Counting the whole market's prints would inflate the
numerator with volume that could never have filled him; both are recorded so the
choice is auditable.

**Comparison model (the incumbent):** fill probability as a function of
`depth_ahead_of_fill_price` alone — the queue model used in the PMUS replays.

**Decision rule, fixed now:** H-FLOW is supported only if the ratio separates
filled from unfilled resting states **out of sample** and by a margin that
survives a market-clustered bootstrap. "Better" is judged on discrimination,
not on P&L, and not on in-sample fit.

**What would falsify it:** the ratio discriminates no better than depth alone;
or it discriminates only in-sample; or the effect is reproduced by a
within-market shuffle of the ratio across L5's fills.

## Secondary measurements (descriptive, no thresholds)

1. How long L5 appears to rest before a maker fill, where inferable — bounded
   below by the first snapshot showing size at his price, and reported as a
   bound, never as a point estimate.
2. Whether he rests **at** the touch or behind it (`rested_at_touch`,
   `ticks_behind_touch`).
3. Whether price moves **through** him versus flow consuming **toward** him —
   pre vs post book, and the direction the ask travelled.
4. Maker-fill probability conditional on **touch depth** and on **arriving
   flow**, reported as a table, not a fitted surface.
5. Whether **≥2 same-outcome price levels** are observable prospectively — the
   one surviving structural claim from `007feaab`, and the first chance to see
   it in a book rather than infer it from fills.
6. **OPEN taker behaviour** — the autopsy found P(taker|OPEN) = 87%/77%
   retrospectively. Does it hold prospectively?
7. **Late-settlement taker behaviour** — the autopsy found 5%→52% as TTE runs
   out. Does it hold prospectively?

## Sample-size and stopping rule

Declared before looking: no analysis of **either** hypothesis is reported until
at least **200 scorable maker fills** have accumulated **within a single
market**, across a corpus spanning **multiple markets**. "Scorable" excludes
`NO_COVERAGE` and `NO_QUEUE` — a fill with no pre-fill book, or with nothing
ahead of it, is not evidence about queueing in either direction.

Below the floor, only per-market descriptive counts are reported, always with
their denominator and always flagged. `pooled()` raises rather than returning a
number. Observation does not stop when a result looks good; it stops at a fixed
time or on operator instruction.

**Right-censoring.** A fill whose window the tape had not published, or whose
book we never read, is `NO_COVERAGE` — censored, never counted as a miss and
never counted as zero flow. The same discipline applies to the
`pmus_flow` gap of 2026-09-21 03:55:13Z–06:07:17Z
(`PMUS_FLOW_INCIDENT_20260921.md`): that interval is absent, not empty.

## What is NOT permitted under this preregistration

- Fitting any threshold, weight or cutoff on the prospective cohort.
- Building a selector of any kind.
- Reading P&L, settlement or our own hypothetical fills as an input.
- Re-defining `arriving_flow_over_depth_ahead`, the verdict rules, the
  tolerances or the `INCONCLUSIVE_GAP` bound after seeing results.
- Reporting an in-sample separation as evidence.
- Pooling markets before a within-market effect is established.
- Fitting anything at n=16, or at any n below the floor.
- Inferring a queue or priority mechanism from PositionSplit proximity. Chain
  receipts are transaction/matching **context** only; the settled finding stands
  that minting does not distinguish maker from taker (86.5% vs 90.0%, CI
  includes zero).
- Treating a confirmed H-QUEUE-MISMEASURED as evidence for any particular
  replacement mechanism.
