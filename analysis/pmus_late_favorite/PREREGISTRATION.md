# PMUS BTC15m late-favorite / opposite-longshot — frozen preregistration

**Committed before any outcome, settlement or P&L quantity is joined to a
candidate state in this lane.** The commit date of this file, and of
`windows.json` beside it, is the evidence that the protocol was fixed first.
Nothing below may change during scoring; any change starts a new block and says
so in the file.

**SHADOW / HISTORICAL ONLY. No live orders. No executor. WAIT is a valid action
and is scored as one.**

## Primary question

> When BTC is sufficiently far from its window-open reference late in a PMUS
> 15-minute window, is the favorite systematically underpriced relative to
> settlement probability, and if so, what is the cheapest defensibly executable
> way to acquire that exposure?

Phase 1 answers only the first half, and must be frozen before Phase 2 runs.
**The first conclusion must be about calibration, not about profit.**

## Design lessons imported from `unbuiltworks/kalshi-longshot-fader`

Research design only. **No Kalshi threshold, fee, fill assumption, bucket cutoff
or conclusion is carried into PMUS.** Kalshi and PMUS differ in book structure,
participants, fee sign (PMUS *pays* the maker), settlement mechanics and tick
regime, so a Kalshi number transferred here would be a category error. What
transfers is method:

1. probability calibration before strategy P&L;
2. horizon-scaled distance as the primary feature, in the exact form below;
3. ask first whether calibrated probability carries information *beyond* the
   executable market probability;
4. freeze the candidate state before joining settlement truth;
5. normalize buy-favorite and sell-opposite-longshot to identical terminal
   payoff vectors before comparing economics;
6. maker fillability / adverse selection is a separate question from settlement
   edge, and is reported separately;
7. nonfills count — headline results are never conditioned only on fills;
8. unconditional opportunity EV and conditional-given-defensible-fill EV are
   reported as two distinct numbers;
9. no touch = fill.

From that repo's `CALIBRATION_PREREGISTRATION.md` and `SILENT_FAILURES.md`, the
governing warning is adopted verbatim in spirit: *the trading process must never
promote a rule because its own analysis says the rule works.*

## Instrument-specific definition of "strike"

PMUS BTC15m is **Up/Down**, not struck. It settles **Up iff final ≥ P0**, where
`P0` and `final` are each the mean of 60 one-second BRTI prices in the minute
preceding `T_start` and `T_end`, rounded to 2 dp. **The tie goes to Up.**

So `strike := P0`, the window's own opening reference. On the available data
`P0` and `final` are approximated by OHLC4 of the first and last minute of the
window from a Coinbase 1-minute proxy; this is declared a proxy everywhere it is
used and is the dominant limitation on the smallest-distance bucket.

## Observation scheme

**One observation per market per checkpoint.** Not every tick. Ticks within a
window are serially dependent and would inflate n without adding information.

Checkpoints, as seconds remaining before `T_end`:

```
T-300   T-180   T-120   T-60   T-30
```

A checkpoint is covered iff a state observation exists at or before the mark and
the tape has not already ended. **State is never interpolated, backfilled, or
carried forward across a gap longer than 10 s**; a checkpoint whose nearest
preceding observation is staler than 10 s is dropped, not imputed. Checkpoints
may be adjusted only for technical coverage impossibility, only before any
outcome inspection, and only with the adjustment recorded here.

## State recorded at each checkpoint

Frozen before settlement truth is joined.

| field | definition |
|---|---|
| `seconds_remaining` | `T_end − t`, from the venue clock |
| `btc_ref` | reference price at `t` (proxy; source named in the row) |
| `strike_p0` | window-open reference `P0` |
| `dist_usd` | `btc_ref − strike_p0`, signed |
| `dist_bps_signed` | `10000 · (btc_ref − strike_p0) / strike_p0` |
| `dist_bps_abs` | `|dist_bps_signed|` |
| `vol_1m` | trailing realized 1-minute volatility of the reference, as a fraction, measured on a trailing window ending at `t` and **never using any data after `t`** |
| `z` | **`((btc_ref − strike_p0) / strike_p0) / (vol_1m · sqrt(minutes_remaining))`** — horizon-scaled signed distance, the primary feature |
| `btc_move_30s`, `btc_move_60s` | recent reference movement to `t` |
| `fav_side` | `UP` if `dist_bps_signed ≥ 0` else `DOWN` (tie → UP, matching settlement) |
| `fav_ask`, `fav_bid` | executable top-of-book for the favorite side |
| `opp_ask`, `opp_bid` | executable top-of-book for the opposite (longshot) side |
| `spread`, `bid_qty`, `ask_qty` | displayed touch depth on each side |
| `flow_30s`, `flow_60s` | recent PMUS trade flow (authenticated windows only) |
| `settlement_winner` | **joined last, after the state rows are written and hashed** |

`fav_executable_implied_p` is the favorite's executable ask, which is the
probability the venue will actually sell. **No midpoint anywhere.** Where only
one side is quoted, the complement is derived as `1 − other_side_touch` and the
row is flagged `one_sided`, never silently treated as two-sided.

## Pre-registered buckets

`|z|` buckets, fixed now:

```
|z| < 0.5     0.5 ≤ |z| < 1.0     1.0 ≤ |z| < 1.5     1.5 ≤ |z| < 2.0     |z| ≥ 2.0
```

Raw-bps buckets may be reported **diagnostically only**; their cutoffs are not
optimized and no result is promoted on them. `|z|` buckets are not re-cut for
any reason during scoring.

## Phase 1 output — the only thing reported first

Per (checkpoint × `|z|` bucket) cell:

- `n` **independent markets** (markets, not ticks, and stated as such);
- favorite settlement rate;
- mean executable favorite implied probability;
- **calibration residual** = settlement rate − executable implied probability;
- a 95% interval on the residual, by Wilson on the rate plus the market-count n,
  and additionally a per-market clustered interval since the five checkpoints of
  one market are not independent;
- Brier and log-loss of the executable price vs the calibrated probability, where
  cell counts make it meaningful;
- discovery and untouched-validation reported **separately and never pooled**.

No threshold search occurs in Phase 1. No P&L is computed in Phase 1.

## Phase 2 — execution expressions, only after Phase 1 is frozen

Evaluated on **identical candidate states**, with `WAIT` always available:

- **A — taker favorite.** Buy the favorite at the contemporaneous executable
  ask, capped at displayed touch depth. Fees from `src/pmus_fees.py` at the
  row's own timestamp. No midpoint, no stale quote, no zero-latency repricing.
- **B — maker favorite.** Rest at a pre-declared price. **No touch = fill.** A
  fill requires observed queue consumption at that price or strict trade-through
  evidence in the authenticated trade tape. No invented queue; a quote behind
  the touch has unknown queue-ahead and cannot fill.
- **C — opposite-longshot maker / economically equivalent sell.** Normalized to
  the **same terminal payoff vector** as A/B before any comparison, with the
  equivalence shown explicitly in the output. This is **not** called an
  arbitrage. Any economic difference is attributed to a named cause: spread,
  entry price, maker/taker classification, fees, verified maker rebate, fill
  probability, or adverse selection.
- **D — WAIT.**

For **both** maker arms, reported: placements/opportunities, defensible fills,
nonfills, fill rate, time to fill, outcome probability **unconditional** on fill,
outcome probability **conditional on fill**, the adverse-selection delta caused
by conditioning on fill, and markout after fill where available.

The conditional-vs-unconditional comparison is a priority, because the Kalshi
lane contains evidence that a real settlement edge can become unattractive once
passive fills are adversely selected.

## Phase 3 — economics, reported as separate lines

Gross directional P&L; spread capture; taker fees; maker rebates **only if
verified**; adverse-selection cost; unresolved/cleanup loss; net; opportunities
per window; fills per window; net per eligible window; net per traded window;
capital required; maximum concurrent exposure; realistic capacity from displayed
depth and evidenced flow.

$3/window and $5/window are tested **explicitly** and answered yes or no with
the supporting n. **q1 results are never scaled linearly.** Capacity is measured
by replaying actual contemporaneous depth, and the report states the maximum size
for which every fill remains supported by observable liquidity.

## Anti-overfitting rules, binding

- Discovery and validation window IDs are persisted in `windows.json` **before**
  any scoring code exists.
- No threshold is fitted to maximize historical P&L.
- Any candidate rule is frozen, hash-guarded, and scored on untouched windows.
- Promotion never rests on win rate.
- **Maker economics may not rescue a settlement-probability hypothesis that
  fails out of sample.** If Phase 1 shows no miscalibration, a profitable-looking
  Phase 2 is treated as an execution artefact and reported as a negative result.
- `WAIT` is preserved as an action throughout.
- `RESERVED_OTHER_EXPERIMENT` windows are never read, scored or consumed.

## Known-invalid-today disclosure, carried into every output

`CANDIDATE_VALIDATION` is empty by construction: every window with usable
late-window coverage predates the recorder's seen-retention fix `ddc2195c`, and
the only post-fix windows are reserved to the active experiment. With 38
discovery markets against a 25-cell grid, the effective n per cell is ~1.
**Therefore no Phase-1 result from today's data may be stated as evidence for or
against the hypothesis.** It is a direction-of-effect reading that tells us
whether forward accrual is worth paying for, and it is labelled that way
wherever it appears.
