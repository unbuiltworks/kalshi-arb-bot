# ARB Gemini BTC5 V5.2 — Live-Public Findings and Strategy Correction

Date: 2026-08-30

## Classification

Read-only evidence only. No credentials were supplied to the public-market diagnostics, no order entry occurred, and realized P&L is $0.

## Exact release evaluated

`release/gemini-q10-production-v5-2`

SHA evaluated:

`8a06f809c583ce16b507988f86d4617fae86ddd1`

This SHA is a V5.2 candidate, not an approved live release.

## Safety/readiness evidence

- Focused release suite: 66/66 passed.
- AWS exact-image recovery readiness: 66/66 passed again inside the built image.
- Fresh authenticated read-only account audit on AWS:
  - `CLEAN_NO_OPEN_STATE`
  - account orders: 0
  - account positions: 0
  - exact residual: true / 0
  - available USD observed: $52.3155
  - dead-man: `IDLE_NO_OPEN_CAMPAIGN`
  - sidecar: `WAITING_FOR_OPEN_CAMPAIGN`
  - recovery/watchdog arms: false
  - order entry: false

## Public qualifier soak

Workflow run: `33330744117`

Over 60 polls of the 19:30 BTC5 window:

- 48 `EXPIRY_WINDOW`
- 5 `DEPTH_UNAVAILABLE_AFTER_RETRIES`
- 7 `REWARD_DISTANCE_GATE`
- 0 qualified candidates

The 5 depth failures each exhausted all three independent 4-second fresh websocket subscriptions. Later polls on the same symbol obtained depth immediately and reached economics.

Conclusion: three reconnect attempts improve a one-shot reader but do not solve the underlying quiet-book behavior. Repeated connect/subscribe/disconnect is the wrong architecture for pre-candidate depth. A persistent hot subscription should be used if this market-data path remains relevant.

Representative successful depth/economics poll:

- bid 0.21 x 100
- ask 0.59 x 100
- base YES quote 0.21
- base NO quote 0.41
- modeled q10 pair edge: $3.72 after maker fees
- reward distance: 0.19 per side
- allowed reward distance: 0.10

This proves the `NO_CANDIDATE` outcome was not equivalent to "no trade edge existed."

## Reward-bound / rescue-safe clip diagnostic

Workflow run: `33331310223`

A second credential-free diagnostic kept one public `GeminiTopCache` connection hot and evaluated the smallest inward quote movement needed to satisfy the 10-cent reward-distance geometry while preserving the existing $0.30 absolute first-fill rescue cap.

At the 19:40 BTC5 window it obtained a fresh book on the first evaluated poll:

- bid 0.13 x 100
- ask 0.46 x 100
- midpoint 0.295
- minimum reward-bound quotes: YES 0.20 / NO 0.61
- both maker-only at that snapshot

Results by quantity:

- q1: safe; pair profit $0.17; worst immediate rescue loss $0.10
- q2: safe; pair profit $0.36; worst immediate rescue loss $0.19
- q3: safe; pair profit $0.54; worst immediate rescue loss $0.29
- q10: unsafe; pair profit $1.82; worst immediate rescue loss $0.93

Maximum rescue-safe clip at that snapshot: q3.

Conclusion: the observed market offered genuine after-fee maker pair edge, but fixed q10 was incompatible with the existing $0.30 rescue invariant. Smaller clips can satisfy trade-edge and rescue constraints.

## Critical Gemini Liquidity Rewards eligibility correction

Current Gemini documentation checked on 2026-08-30:

- Liquidity Rewards Program:
  `https://developer.gemini.com/prediction-markets/liquidity-rewards-program`
- Prediction-market place-order reference:
  `https://developer.gemini.com/rest-api/prediction-markets/order-management/place-order`
- WebSocket stream reference:
  `https://developer.gemini.com/websocket/streams`

Gemini currently states:

1. Liquidity Rewards minimum size is **10 contracts**.
2. Quotes below the minimum size receive zero score.
3. **Post-only orders are excluded from Liquidity Rewards scoring.**
4. Gemini defines WebSocket `timeInForce: MOC` as maker-or-cancel / **post-only**.

The V5.x maker path submits `MOC` orders.

Therefore:

> The current V5.x MOC orders must not be modeled as earning Gemini Liquidity Rewards.

This is independent of whether the orders earn maker rebates after actual maker fills. Maker rebates are a separate program and must be accounted only when actually received/verified.

The q1-q3 safe clips found above also would not meet the Liquidity Rewards 10-contract minimum even if they used an otherwise eligible order mode.

## Strategy consequence

Do not run the prepared V5.2 q10 live workflow.

The live-public evidence does **not** support continuing to optimize for Liquidity Rewards under the current MOC architecture. The useful economic signal is actual after-fee maker-fill pair edge.

Recommended next hypothesis for review/shadow measurement:

### Adaptive MOC maker-trade-edge engine

- no Liquidity Rewards revenue in modeled profitability;
- persistent hot public depth cache;
- MOC/post-only maker orders retained to prevent accidental taker entry;
- dynamically choose clip quantity rather than forcing q10;
- quote improvement/edge spending is allowed only while:
  - completed pair remains above an explicit after-fee trade-profit floor;
  - full possible first fill for the chosen clip remains rescuable inside the unchanged absolute $0.30 loss cap;
  - current external rescue depth is sufficient after subtracting own resting size;
  - price/freshness/capital/session/reconciliation gates pass;
- cumulative matched inventory may be built from repeated bounded clips, but open directional exposure must never exceed the independently rescuable clip cap.

This is a new strategy hypothesis and requires review + read-only/shadow evidence before any live authorization.

## Unresolved implementation review items

Before any new live release:

1. Replace repeated qualifier websocket connects with a persistent pre-candidate cache if the old qualifier remains in use.
2. Review all acked-order lookup paths, including sequential-first-order cancel and maker-grace cancel, so an acknowledged-but-unfindable order can never be interpreted as zero fill before another rescue mutation.
3. Parameterize quantity through execution/recovery/state rather than replacing a hardcoded q10 constant piecemeal.
4. Define the trade-edge floor independently from rewards/rebates.
5. Measure fill probability and realized maker economics in shadow/read-only form before choosing the preferred clip-size/quote-aggression policy.
6. Keep `release/gemini-q10-production-v5-1-final` immutable and treat V5.2 SHA `8a06f809...` as a read-only candidate, not production.
