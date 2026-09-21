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

Two standing caveats, recorded in every event:

- **`depth_ahead_of_fill_price` is an UPPER bound.** Anonymous depth hides
  arrival order, so part of the same-price queue may have posted *after* L5.
- **A null is never a zero.** Unmeasured fields carry an explicit reason
  (`pre_fill_book_reason`, `arriving_flow_over_depth_ahead_reason`, …). Zero
  depth and an unread book are different facts.

## Primary hypothesis

> **H-FLOW: arriving executable flow relative to resting depth ahead of L5
> explains L5 maker-fill occurrence substantially better than our previous
> queue-depth model.**

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

Declared before looking: no analysis of H-FLOW is reported until at least
**200 maker fills carrying a non-null `pre_fill_book`** have been observed.
Below that, only descriptive counts are reported and no comparison is made.
Observation does not stop when a result looks good; it stops at a fixed time or
on operator instruction.

## What is NOT permitted under this preregistration

- Fitting any threshold, weight or cutoff on the prospective cohort.
- Building a selector of any kind.
- Reading P&L, settlement or our own hypothetical fills as an input.
- Re-defining `arriving_flow_over_depth_ahead` after seeing results.
- Reporting an in-sample separation as evidence.
