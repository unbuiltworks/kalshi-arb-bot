# ARB Control Plane

Last verified: **2026-08-25 02:28 UTC / 2026-08-24 22:28 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

The business aspiration is **$480/day realized net**, but that number is **not** the evidence threshold for the current market-capacity study. The market-supported executable dollars/day measured by the census sets the realistic operating target.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Current scoreboard

- **New realized result:** Caleb Durbin one-leg Gemini exposure settled at **gross -$0.42 / net -$0.44**, Gemini payout $0, at 2026-08-25 01:52:17 UTC (21:52:17 ET).
- Settlement reconciler subsequently reports `open_reconcilable=0` and `one_leg_open=0`.
- Prior historical proof remains approximately **+$0.2679** across seven earlier settled-compatible Gemini<->Kalshi sports trades. Keep historical proof separate from the new -$0.44 result; do not combine periods ambiguously.
- Same-Gemini crypto most recently showed roughly 2,647 certified q1 pairs / 275 books and **0 immediate >=3c FOK/FOK locks**.
- Polymarket MLB immediate-execution census most recently showed **621 participation-certified pairs and 0 immediate >=3c FOK/FOK locks**.
- Recent Polymarket maker attempts were zero-fill. Maker-indicated rows are not counted as arbitrage.
- FanDuel<->Polymarket exact authenticated preview has found refreshed normal-rules price gaps, but the lane remains **manual + settlement-rule-risk**, not guaranteed arbitrage and not part of locked-arb capacity.

## Strategic priority changed: edge capacity before more execution work

The next milestone is **not** another q1 plumbing proof. It is a zero-order-entry measurement of how much executable edge actually exists.

Protocol source of truth: **`ARB_EDGE_CENSUS_2026-08.md`**.

Locked decisions before seeing results:

1. Instrument existing production scanners/feeds; **do not create another census control plane/service**.
2. Census order entry is zero. Existing settlement/reconciliation may continue for already-executed positions.
3. Separate `TAKER_LOCKED` from `MAKER_INDICATED`.
4. Measure maker adverse selection with **public trade-print proxy**, not live q1 maker orders. At/through a would-be maker price, observe the hedge at +10/+25/+50/+100/+250ms where feeds support it. Label this a proxy, not a fill.
5. Locked-arb thresholds are >=1c, >=2c, >=3c **after all modeled venue fees** with exact visible executable depth.
6. Opportunity lifetimes use WS/event-driven feeds where available. REST may support broad discovery/recovery but may not be used to claim sub-second persistence.
7. Provisional hard realistic latency floor is **80ms end-to-end**. Effective floor becomes `max(80ms, measured production p95 critical-path latency)`. The floor may rise during this census but may not be lowered to rescue capacity. Opportunities shorter than the effective floor contribute **$0** to realistically capturable capacity.
8. No automatic `make the infra faster` branch for sub-floor opportunities. For this verdict, they are economically unavailable to this rig.
9. Do not double-count shared book depth across overlapping pair constructions. Raw pair-sum diagnostics must be labeled naive/overlapping, never final capacity.
10. Directional EV lanes such as weather use a **separate scorecard**: $/day per dollar of risk, return on deployed capital, drawdown, calibration/hit rate, turnover, capacity/slippage and regime stability. Do not force them into locked-arb metrics.
11. $480/day is an aspiration, not a go/no-go cutoff. If honest capacity is $120/day, evaluate the $120/day business on its merits and combine only genuinely independent profitable lanes.

## Fixed census schedule

- Instrumentation complete by **Wednesday 2026-08-26**.
- Then run **72 hours** of locked-edge collection.
- Publish the verdict on **Sunday 2026-08-30**.
- Do not extend because the economics are disappointing; only a documented data-quality failure justifies rerunning.

## Production mutation state during census

### Polymarket structural / MLB — `sports-kp-live-executor`

Service ID: `bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`

Census boundary applied:

- `SPORTS_POLY_RELAY_LIVE_Q1=0`
- `SPORTS_POLY_DIRECT_MLB_LIVE_Q1=0`
- `ENABLE_SPORTS_KP_LIVE=false`

Latest post-disarm deployment seen: `8c7d83cc-161b-4e69-b5a3-df4922674734`, `SUCCESS`, but startup cleanup hit a Polymarket `RateLimitError` and fail-closed with `order_entry=false`. This service therefore still needs its read-only census path separated from private cleanup before the instrumentation deadline.

### Gemini<->Kalshi sports — existing service repurposed to read-only WS hot shadow

Service ID: `0238eada-ced4-4927-8150-0ccce2e1a0a4`

Live mutation gates are disarmed:

- `ENABLE_GEMINI_KALSHI_LIVE_PROBES=false`
- `MLB_GK_MULTI_LIVE_RULE_RISK=false`

The prior live supervisor correctly crashed when the global live gate was removed. Its Railway start command has now been changed to the repo's existing **event-driven read-only** `src.gemini_kalshi_mlb_prop_hot_shadow` path, with `--hot-profit 0.01`, 50ms max quote skew, 100ms hot-book age and 10s heartbeat. That module already records OPEN/MOVE/CLOSE lifetimes and trigger-to-evaluation avg/p95 with `order_entry=false`.

Redeploy initiated as `6b2c2d48-a4c2-44d7-9179-2b561bba6502`; verify healthy measurement logs before calling this lane census-ready.

The Caleb one-leg quarantine reason is gone because the position settled, but **G<->K is intentionally not rearmed during the zero-risk census**.

### Same-Gemini crypto — `crypto-maker-live-probe-q1`

Service ID: `6e0dffb2-8309-47cf-8196-9106eda284d8`

Live/maker mutation arms are disarmed:

- `GEMINI_SAME_VENUE_LADDER_LIVE_Q1=false`
- `MAKER_LIVE_PROBE_LIVE=false`
- `MAKER_LIVE_PROBE_ARM=false`
- `CRYPTO_MAKER_CANARY_ARM=false`
- `CRYPTO_MAKER_SUPERVISOR_ARM=false`

Current q1 immediate discovery already evaluates the complete certified L<H threshold-pair universe, but its broad scan is REST-based and currently reports >=3c only. It still needs in-place census instrumentation for >=1c/2c/3c depth plus WS lifetime/public-print proxy before the instrumentation deadline. Do not mistake repeated REST snapshots for sub-second lifetime measurement.

### Settlement reconciler — keep running

Service ID: `43c52248-e3cc-433e-8d5d-e6a0d70e3701`

Latest confirmed state after Caleb settlement:

- `open_reconcilable=0`
- `one_leg_open=0`
- no new order-entry path in this service.

### FanDuel<->Polymarket monitor

Service ID: `4a3bbb15-6779-4353-aa5c-adc1a7e58b8d`

Keep alert/preview-only. It may continue to demonstrate manual/rule-risk dislocations, but:

- FanDuel remains manual;
- no capital is reserved;
- no automated Polymarket hedge should be fired before confirmed FanDuel placement;
- settlement-rule mismatch means these rows do **not** enter the guaranteed locked-arb table.

## Census outputs required

### Locked-arbitrage table

Per certified lane:

- >=1c / >=2c / >=3c after-fee counts;
- exact executable depth;
- p50 / p90 / max opportunity lifetime;
- overlap-adjusted theoretical 100%-capture $/day;
- latency-floor-filtered realistically capturable $/day;
- venue-capital requirement/bottleneck;
- settlement-rule certification and data-quality notes.

### Directional-EV table

Weather and other directional strategies:

- realized or properly out-of-sample expected $/day;
- $/day per dollar risk / return on deployed capital;
- max drawdown and daily volatility;
- hit-rate/calibration where applicable;
- turnover, capacity/slippage and regime stability.

### Maker proxy table

At public prints implying the would-be maker price was touched/crossed:

- sample count;
- hedge survival >=1c/2c/3c after fees at +10/+25/+50/+100/+250ms;
- median and p10 post-print edge;
- depth survival and adverse-move distribution;
- explicit sample/data-quality caveats.

## What happens after the verdict

If a lane has material **realistically capturable** capacity, then complete/validate `RESERVED -> FILLED -> HEDGED -> SETTLED -> REALIZED` at q1 and write a measured scaling ladder from depth/capital demand.

If a locked lane has negligible floor-filtered capacity, stop spending engineering time trying to manufacture the edge. Preserve the working infrastructure, but allocate effort to economically supported lanes instead.
