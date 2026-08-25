# ARB Control Plane

Last verified: **2026-08-25 20:46 UTC / 2026-08-25 16:46 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. We are no longer allowing telemetry-readiness or lifecycle-fingerprint work to freeze the roadmap indefinitely.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Edge-census protocol — clock now runs through imperfect data

The 72-hour K<->P census clock is **STARTED at 2026-08-25 20:43 UTC / 16:43 ET**. It is not paused for REST fallback, missing WS lifetime proof, temporary feed degradation, or incomplete source quality. Instead, periods that fail the committed capture-quality requirements count as **zero realistically deployable capacity for those periods**. This removes the telemetry gate without making weak data look executable.

The fixed economic thresholds remain unchanged:

- **<$50/day** realistically deployable locked-edge capacity: end dedicated arb engineering and archive the project.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward the $480/day aspiration.

Required measurements remain >=1c/>=2c/>=3c after fees, exact executable depth, p50/p90/max lifetime where source quality supports it, quote skew, overlap-adjusted capacity, and latency-floor-filtered realistic capture. The provisional latency floor remains **80ms** and may rise to measured production p95 but may not be lowered to rescue capacity. Maker adverse-selection evidence uses public trade-print proxy, not census orders.

Source-quality labels remain mandatory. `REST_SNAPSHOT_NOT_WS_LIFETIME` observations may contribute descriptive price/depth information where valid, but they do **not** get promoted into valid sub-100ms lifetime/capture evidence. Their unsupported capture interval is scored as zero rather than stopping the clock.

Same-venue Gemini/Polymarket/Kalshi zero results remain near-verdict evidence; do not spend engineering time manufacturing same-venue ladder violations.

## Current authoritative scoreboard

- **2026-08-25 ET:** last direct Neon verification remains **0 `TRADE_REALIZED` guaranteed-arbitrage rows / $0.0000 realized guaranteed-arb net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44** and remains classified non-guaranteed.
- Weather since **2026-03-01:** last direct Neon verification remains **0 weather-tagged `TRADE_REALIZED` rows / $0.0000 verified realized weather P&L**.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Production is healthy and read-only. Fresh Railway project status shows latest deployment **`28e0fbab-0769-4b35-982d-da418618ffe3` = SUCCESS**.

The gateway decision telemetry is live and strict discovery has produced **72 exact certified counterparts** with event readiness observed. The Kalshi leg is deliberately using REST fallback after concurrent authenticated-WS 429 behavior. Consequently the decision census correctly labels affected observations `REST_SNAPSHOT_NOT_WS_LIFETIME`.

That REST/source-quality limitation is **no longer a clock-start gate**. The 72-hour wall clock is running now. Unsupported lifetime/capture periods count as zero realistic capacity rather than extending or pausing the measurement window.

The census methodology itself is unchanged: >=1c/>=2c/>=3c after fees, q1 executable depth, quote skew, source quality and the >=80ms realistic-capture filter remain explicit. We will not lower economic or latency thresholds to make the result look better.

Breadth may still be improved during the running window when settlement/rule equivalence can be established cleanly, but missing breadth is no longer a reason to delay the verdict clock.

### Gemini<->Kalshi sports

The previous control-plane rule `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION` is **no longer a roadmap blocker**.

The read-only `sports-gk-autonomous-supervisor` remains available for telemetry and is configured for its built-in Kalshi REST fallback. Positive shadow signals are no longer required to wait for a completed full lifecycle fingerprint before a bounded live probe.

The existing isolated execution shell `sports-gk-multifamily-live-canary` is the permitted path because it retains:

- q=1 exactly;
- >=3c modeled after-fee economics;
- <=50ms quote skew / <=100ms book age;
- 50ms exact-price/depth revalidation;
- dual-FOK transport with ambiguity reconciliation;
- one submission attempt;
- $5 hard budget;
- Railway restart policy `NEVER`;
- explicit accounting classification `LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB` until authoritative settlement supports a stronger classification.

At **2026-08-25 20:43 UTC**, Railway variables were re-armed for this bounded canary. Deployment **`e029636d-5342-478e-a733-7f8c0dadf669` = SUCCESS**. Direct runtime logs prove:

- `MLB_GK_MULTI_PREFLIGHT`: live requested, env gate true, one-use token present, armed true;
- Gemini terms accepted;
- available balances seen by preflight: Gemini USD **61.7955**, Kalshi USD **3405.71**;
- `MLB_GK_MULTI_ARMED`: **139 eligible exact keys**, all currently Home Runs, q=1, >=3c, order entry true;
- no `SUBMITTED`, `FILLED`, `RESULT`, or settlement event has been verified from this run.

The current blocker is now transport, not policy: the live canary's authenticated Kalshi cross-book websocket is repeatedly rejected with **HTTP 429**. This is a real execution/feed constraint and is not to be disguised as a roadmap gate. No trade has been counted from this run.

Removing the lifecycle gate does **not** convert a rule-risk live probe into guaranteed arbitrage. Any submitted/fill/settlement states must remain labeled accurately.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains crashed on deployment `03080d7a-1696-4c38-a86d-54fa41882892`. This lane is not being reopened here; settlement-index non-equivalence remains economically relevant evidence, and AWS/low-latency routing work is isolated in the separate maker chat.

## Highest-value next actions

1. **Let the K<->P 72-hour wall clock run now.** Do not pause it for source-quality failures; score unsupported capture intervals as zero.
2. Treat G<->K lifecycle fingerprinting as non-blocking. The active issue is the Kalshi WS 429 on the bounded q=1 canary, not a policy gate.
3. Preserve exact SUBMITTED / FILLED / SETTLED / REALIZED labeling if any live probe fires; current re-armed run has no verified submission/fill yet.
4. Use the running census to rank actual >=3c q1 capacity and identify the shortest path to additional bounded live probes where capacity is real.
5. Add K<->P breadth only when it can be done without corrupting settlement equivalence or disrupting the running instrumentation.
6. Keep the 80ms floor and economic thresholds fixed. Removing roadmap gates is not permission to rescue weak results by changing the measuring stick.
7. Keep AWS latency/maker-routing work in the isolated chat. Main chat consumes those results only as execution-design inputs.
