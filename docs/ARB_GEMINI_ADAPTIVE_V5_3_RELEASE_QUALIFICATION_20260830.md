# ARB Gemini BTC5 Adaptive Maker V5.3 — Release Qualification

Date: 2026-08-30

## Status

**FROZEN / QUALIFIED FOR A FUTURE SINGLE LIVE CANARY, NOT ARMED**

- Frozen release branch: `release/gemini-adaptive-maker-v5-3-final`
- Frozen release SHA: `1f876470e9e4de43235914379636c4b16d02c8ab`
- Control branch: `aws-migration-foundation`
- AWS hot instance: `i-0b90f0a309153f47e` (`us-east-1`)
- Live order entry performed by V5.3 during this qualification: **NO**
- V5.3 realized P&L during this qualification: **$0**
- Existing live authorization reused: **NO**
- Fresh V5.3 live authorization currently present: **NO**

The frozen branch must not be moved or modified. Any code change requires a new release candidate and the qualification sequence below to be repeated.

## Strategy definition

V5.3 is a BTC5 adaptive maker strategy. It is intentionally **not** a liquidity-reward strategy.

For each eligible BTC5 contract, V5.3:

1. Reads the instrument-specific `quantityMinimum`, `quantityIncrement`, and `priceIncrement`.
2. Computes which integer clips in q=1..10 are valid on that contract grid.
3. Maintains a persistent public depth-5 websocket cache.
4. Searches maker-only YES/NO quote pairs for the **largest valid safe clip**.
5. Requires at least **$0.05 per contract completed-pair profit after conservative maker fees**.
6. Counts **zero liquidity rewards and zero maker rebates** in modeled profit.
7. Requires both possible first-fill paths to be immediately rescuable within an unchanged **$0.30 absolute first-fill rescue-loss cap**.
8. After orders are acknowledged, revalidates rescue safety from a newer book update and removes known owned resting liquidity before evaluating external rescue depth.
9. Uses ambiguity-safe cancel/reconcile and bounded FOK rescue inherited/hardened from V5.2.
10. Persists the selected adaptive quantity and will not accept/recover a residual larger than that quantity.

The 20c–80c Gemini maker-rebate eligibility band is metadata only. It is not an execution-safety gate and rebate dollars are not included in trade-edge P&L.

## Why V5.3 exists

Earlier q10 experiments mixed trade economics with liquidity-reward/rebate eligibility. That could force quotes away from their natural safe maker levels and make q10 appear unsafe even when the underlying trade edge and rescue depth supported it.

A prior read-only trade-edge diagnostic observed a 44c / 88c BTC5 book and found q10 at YES 44c / NO 13c with $4.23 modeled after-fee pair profit and $0.23 worst immediate rescue loss. That showed q10 itself was not categorically unsafe.

V5.3 therefore sizes adaptively based on executable trade economics and rescue safety, not reward-program qualification.

## Contract-grid incident and fix

The first V5.3 public qualification attempt at frozen-candidate SHA `3368c737d372d173cceea9835a89605da8cb8601` returned:

- `READ_ONLY_NO_SAFE_PLAN_WITHIN_WINDOW`
- 171 polls
- 171 × `UNSUPPORTED_CONTRACT_METADATA`
- no order entry

Root cause: the candidate incorrectly required `quantityMinimum == 1` and `quantityIncrement == 1`.

The fix added `src/gemini_adaptive_maker_contract_grid_v5_3.py` and explicit regressions. V5.3 now honors the live instrument grid while still limiting itself to integer clips q=1..10. For example, a 0.01 quantity increment allows q1..q10; a quantity increment of 2 allows q2/q4/q6/q8/q10 only. Invalid/unsupported grids fail closed.

## Focused release qualification

GitHub Actions release test run:

- Run ID: `33333850141`
- Job ID: `99317120184`
- Exact SHA: `1f876470e9e4de43235914379636c4b16d02c8ab`
- Result: **90 passed in 1.73s**

The suite includes the frozen V5.2/V5.1 safety regressions plus V5.3 adaptive policy, depth, executor, failure-injection, and contract-grid tests.

Important failure-injection coverage includes:

- acknowledged maker order whose client-ID row cannot be resolved;
- fill quantity exceeding the selected adaptive clip;
- stale/no post-mutation depth update;
- unresolved maker-completion order;
- crash while maker completion is pending;
- residual larger than selected quantity during recovery;
- own resting liquidity being removed from displayed rescue depth;
- fall-through to the next external depth level;
- dynamic instrument quantity-grid validity.

## AWS public-market qualification

Workflow: `.github/workflows/aws-gemini-v5-3-public-diagnostic.yml`

Run:

- Run ID: `33333898427`
- Job ID: `99317249469`
- Exact release SHA: `1f876470e9e4de43235914379636c4b16d02c8ab`
- Classification: `READ_ONLY_PUBLIC_NO_CREDENTIALS_NO_ORDER_ENTRY`
- Order entry: **false**
- Realized P&L: **$0**

Observed live BTC5 contract:

- Ticker: `BTC05M2608302040`
- Symbol: `GEMI-BTC05M2608302040-UP`
- Seconds to expiry at selection: `276.67`
- Quantity minimum: `0.01`
- Quantity increment: `0.01`
- Price increment: `0.01`
- Valid integer clips: q1 through q10

Observed public depth snapshot:

- YES bids: 41c × 100, 32c × 400
- YES asks: 61c × 100, 70c × 400
- Snapshot age: ~0.102 ms at evaluation

Selected V5.3 plan:

- Quantity: **q10**
- YES maker quote: **41c**
- NO maker quote: **39c**
- Modeled completed-pair profit after conservative maker fees: **$1.90**
- Required edge floor: **$0.50** total (5c × 10)
- Worst immediate first-fill rescue loss: **$0.22**
- Rescue-loss limit: **$0.30**
- YES-only-posted simulated rescue safe: **true**
- Both-orders-posted standing rescue safe: **true**
- Known owned resting depth removed before standing rescue evaluation: **true**
- Liquidity rewards modeled: **false**
- Maker rebates modeled: **false**

This is the key live-public evidence that the frozen V5.3 policy can find a real q10 trade-edge plan and preserve the rescue invariant after simulating its own resting orders.

## AWS authenticated disarmed readiness

Workflow: `.github/workflows/aws-gemini-v5-3-readiness.yml`

Run:

- Run ID: `33333927235`
- Job ID: `99317322238`
- Exact release SHA: `1f876470e9e4de43235914379636c4b16d02c8ab`
- Result: **success**
- Exact-image tests on AWS: **90 passed in 2.26s**

Account/recovery state:

- Recovery audit: `CLEAN_NO_OPEN_STATE`
- Open state: false
- Residual quantity: 0
- Active known orders: 0
- Account orders: 0
- Account positions: 0
- Available USD: **$52.3155**
- Dead-man: `IDLE_NO_OPEN_CAMPAIGN`
- Sidecar: `WAITING_FOR_OPEN_CAMPAIGN`
- Live armed: false
- Recovery armed: false
- Watchdog armed: false
- Order entry: false
- Realized P&L: $0

## Frozen live launcher

Workflow: `.github/workflows/aws-gemini-adaptive-v5-3-live.yml`

The workflow is present on `aws-migration-foundation` but is **inert**. It has no `workflow_dispatch` trigger. It can run only when this separate file is created/changed:

`deploy/aws/gemini-adaptive-v5-3-live.trigger`

The trigger must contain all four exact fields:

```text
PINNED_COMMIT=1f876470e9e4de43235914379636c4b16d02c8ab
APPROVAL_ID=<fresh unique one-use id>
CLASSIFICATION=FRESH_USER_APPROVED_ONE_ADAPTIVE_BTC5_V5_3_CAMPAIGN
LIVE_AUTHORIZATION=EXPLICIT_USER_APPROVAL_ADAPTIVE_BTC5_V5_3
```

The trigger file must **not** be created until a fresh explicit user authorization for one V5.3 live canary has been given after this qualification.

Launcher controls:

- exact frozen release SHA assertion;
- one-use approval-specific state directory;
- exact 90-test safety suite before mutation;
- fresh authenticated flat-account preflight;
- adaptive q limited to valid q1..q10;
- continuous mode OFF;
- maximum one campaign;
- $0.30 max rescue loss;
- $1.00 max campaign loss;
- $3.00 max daily loss;
- independent V5.3 dead-man sidecar;
- executor hard lifetime bound;
- compact success output to avoid SSM stdout-truncation false-red failures;
- detailed executor/sidecar logs retained on EC2 and tailed only on failure;
- post-campaign read-only residual/account reconciliation;
- settlement wait for matched positions;
- final requirement: zero orders, zero positions, zero residual.

## P&L classification

Nothing in this qualification is realized trading profit.

- Unit/integration tests: test evidence only.
- Public diagnostic plan: executable-model evidence only; no orders submitted.
- Account readiness: read-only evidence only.
- V5.3 live fills: **none yet**.
- V5.3 realized P&L: **$0**.

A future canary is not realized arbitrage profit until actual fills are confirmed, positions reconcile, settlement completes, and realized dollars are measured from venue/account evidence.

## Next permitted action

The next permitted live action is exactly **one V5.3 adaptive BTC5 canary**, and only after a fresh explicit user approval. The approval must not be inferred from earlier V5.1/V5.2 approvals or from authorization to build/test/freeze V5.3.

After that canary, classify the result separately as:

1. no candidate / no order;
2. orders submitted but zero fill;
3. first-leg fill + maker completion;
4. first-leg fill + bounded FOK rescue;
5. matched pair pending settlement;
6. settled realized P&L;
7. ambiguity/hard-halt requiring account reconciliation.

Do not advance sizing/continuity based solely on modeled edge or a no-trade safety run.
