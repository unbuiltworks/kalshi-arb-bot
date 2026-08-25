# ARB Edge Census Protocol — August 2026

Locked before interpreting census results. The purpose is to measure what the market actually supports, not to justify a preselected income target.

## Decision dates

- Instrument existing production scanners by **2026-08-26**.
- Run the zero-order-entry locked-edge census for **72 hours** after cross-venue instrumentation is complete and data-quality gates below are healthy.
- Publish the verdict on **2026-08-30**.

Do not extend the census merely because the result is disappointing. Any extension requires a written data-quality reason, not a desire for a different answer.

## Pre-committed Aug 30 business verdict thresholds

These thresholds are locked **before valid Kalshi<->Gemini or Kalshi<->Polymarket cross-venue census data lands**. They may not be loosened on Aug 30 because a result looks interesting.

Primary decision metric: the combined **settlement-certified, overlap-adjusted, after-fee, latency-floor-filtered, capital-constrained realistically capturable locked-arbitrage dollars/day** across independent automated lanes. Maker-indicated rows, manual FanDuel rows, rule-mismatched rows, shadow P&L and directional-EV strategies are excluded from this number.

- **< $50/day:** end dedicated locked-arbitrage engineering after the Aug 30 verdict. Keep only low-cost passive collectors, settlement/reconciliation and safety records that have independent value. Do not build faster infrastructure, add venues, fund accounts or extend the census to rescue the project. Engineering time goes elsewhere.
- **$50-$149/day:** locked arb becomes a **sidecar**, not a primary build. Operate only lanes that independently complete a clean `FILLED -> HEDGED -> SETTLED -> REALIZED` lifecycle and can run with low ongoing effort. No dedicated latency project, no speculative venue expansion and no major new infrastructure solely for arb.
- **>= $150/day:** the measured market supports continued active execution/scaling engineering. Scale only from observed executable depth, settlement certainty, fill evidence and capital demand; do not extrapolate to $480/day without data.

The **$480/day** figure remains a business aspiration, not a pass/fail threshold. A measured $120/day system is therefore not called a failed market; it is classified as a sidecar whose operating burden and independent profitability determine whether it should run.

### No moving the measurement window

The official 72-hour clock starts only when the required cross-venue lanes **Kalshi<->Gemini and Kalshi<->Polymarket** are both producing valid comparable observations with their required feeds healthy. Same-venue Gemini/Polymarket results do not delay this clock.

Once the clock starts:

- run 72 continuous wall-clock hours;
- do **not** add an extra week for an inconclusive or disappointing economic result;
- documented feed/data outages may add only the specifically invalid hours, capped at **24 additional hours total**;
- if a required lane remains technically unmeasurable on the current rig after that allowance, classify it as `UNMEASURABLE_ON_CURRENT_RIG` and assign **$0 deployable capacity** for the Aug 30 decision rather than restarting the study;
- target at least **90% valid feed coverage** across the measurement window for a lane to receive a numeric capacity estimate; otherwise use the unmeasurable classification above.

## Zero-risk census boundary

During the locked-edge census:

- no new arb order entry;
- no q1 maker canaries to manufacture fill samples;
- no capital reservations for census-only rows;
- existing scanner/feed infrastructure is instrumented in place rather than creating another control plane;
- settlement/reconciliation services may continue read-only work on already executed trades;
- FanDuel remains manual/rule-risk and is excluded from the guaranteed-arbitrage capacity number.

## Locked-arbitrage classifications

### TAKER_LOCKED

Both legs are simultaneously executable at the observed asks/bids with sufficient visible depth, exact venue fees included, and settlement/counterpart rules certified. This is the only category that may contribute directly to locked-arb capacity.

### MAKER_INDICATED

A resting maker price plus the current hedge would model a positive locked payoff if the maker filled now. This is **not** an arbitrage fill and contributes $0 to TAKER_LOCKED capacity.

### MAKER_PRINT_PROXY

Zero-risk adverse-selection proxy. When a public trade print occurs at or through the price where our maker would have been eligible to fill, record the sibling hedge state at +10ms, +25ms, +50ms, +100ms and +250ms where the venue feed supports those observations. This is a proxy for fill-conditioned economics, not proof that our order would have filled or had the same queue position.

Maker rows must never be relabeled as locked arbitrage merely because pre-fill arithmetic was positive.

## Locked-edge thresholds

For every certified lane, record after-fee simultaneous executable edge at:

- >= $0.01 per contract/unit;
- >= $0.02;
- >= $0.03.

Every observation must include:

- stable edge key;
- venue pair / lane;
- exact market identifiers and side orientation;
- source timestamps and local receive timestamps;
- after-fee edge;
- executable top-of-book quantity/depth on every required leg;
- settlement/rule-certification state;
- the maximum quantity supported by the observed books without assuming replenishment;
- whether the observation passes the realistic latency floor below.

Do not sum overlapping opportunities that consume the same visible book liquidity as if they were independent. Raw pair-sum capacity may be logged for diagnostics but must be labeled `NAIVE_OVERLAPPING_CAPACITY`, never final capacity. Final dollars/day must avoid double-counting shared depth/capital.

## Opportunity lifetime

For a stable edge key, reconstruct contiguous lifetimes from feed/scanner observations. Report at least:

- p50 lifetime;
- p90 lifetime;
- maximum lifetime;
- opportunity count/day;
- executable depth distribution.

Use WebSocket/orderbook feeds where available for lifetime measurement. REST snapshots may be used for broad universe discovery or authoritative recovery, but a slow REST polling interval cannot be used to claim sub-second opportunity persistence.

## Hard realistic latency floor

The floor is fixed **before** looking at census economics:

- provisional floor for this census: **80ms end-to-end**;
- once production telemetry provides a measured p95 critical-path latency, the effective floor becomes `max(80ms, measured_p95_critical_path_ms)`;
- the floor may increase during this census if measured p95 is worse than 80ms;
- the floor may **not** be lowered during this census to rescue capacity;
- any opportunity whose measured lifetime is shorter than the effective floor contributes **$0** to realistically capturable locked-arb capacity.

There is no automatic `latency optimization` branch for sub-floor opportunities. They are treated as economically unavailable to this rig for the current decision.

## Locked-arbitrage scorecard

Produce one table per certified locked lane with at least:

- observed >=1c / >=2c / >=3c opportunity counts;
- simultaneous executable depth;
- opportunity lifetimes;
- gross observed 100%-capture ceiling before overlap adjustment;
- overlap-adjusted theoretical dollars/day;
- latency-floor-filtered realistically capturable dollars/day;
- venue capital required to capture observed depth;
- capital bottleneck by venue;
- settlement-rule certification status;
- data-quality/coverage notes.

The census output determines the market-supported target. **$480/day is a business aspiration, not the evidence bar.** If the honest realistic ceiling is $120/day, the decision is whether a $120/day system is worth operating and what other independent lanes can add — not whether the census somehow failed.

## Directional-EV scorecard

Directional strategies must not be forced into locked-arbitrage metrics. Weather and any other directional/forecast lane are evaluated separately using:

- realized or properly out-of-sample expected $/day;
- dollars/day per dollar of risk/capital;
- return on deployed capital;
- max drawdown;
- volatility of daily P&L;
- hit rate and calibration where applicable;
- turnover / trade count;
- capacity and slippage sensitivity;
- regime stability by time period/season/market family.

FanDuel-vs-exchange normal-rules dislocations may be tracked as a separate manual/rule-risk opportunity lane, but they are not included in guaranteed-arbitrage capacity unless settlement equivalence and executable confirmation are proven.

## Maker adverse-selection output

Public-print proxy results must report, at each post-print horizon (+10/+25/+50/+100/+250ms):

- count of qualifying proxy events;
- fraction where hedge remained >=1c / >=2c / >=3c after fees;
- median and p10 post-print edge;
- hedge depth survival;
- adverse move distribution;
- missing-feed/sample quality.

If the maker-indicated edge systematically disappears when prints imply the maker could have traded, classify the maker lane as market making/inventory risk rather than guaranteed arbitrage.

## Verdict

On **2026-08-30**, publish:

1. one market-supported, latency-floor-filtered locked-arb dollars/day estimate per lane;
2. one combined locked-arb capacity estimate with shared liquidity/capital overlap handled honestly;
3. separate directional-EV lane economics;
4. maker print-proxy adverse-selection findings;
5. the pre-committed business classification above: end dedicated arb engineering, sidecar, or continue active scaling.

A clean `FILLED -> HEDGED -> SETTLED -> REALIZED` lifecycle remains necessary before scaling any lane that passes the capacity test, but plumbing proof is not a substitute for market-capacity evidence.