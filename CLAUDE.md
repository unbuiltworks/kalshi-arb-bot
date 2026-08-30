# ARB — Autonomous Sports / Prediction-Market Arbitrage

This repository is the master engineering workspace for the ARB system. The old description of this repo as a single-platform Kalshi bot is obsolete.

## Primary objective

Build toward **at least $480/day in realized net profit**, while measuring honestly whether available executable opportunity supports that target.

Optimization priority:

1. safe realized dollars/day;
2. reliable execution and reconciliation;
3. measurable executable opportunity;
4. only then refactoring or additional research.

Do not optimize for theoretical edge, shadow P&L, number of alerts, or number of tests passing if those metrics do not answer whether we can safely execute and realize profit.

## Scope

ARB spans:

- Gemini prediction markets / short-duration crypto markets;
- Kalshi;
- Polymarket;
- FanDuel / sportsbooks where relevant;
- Odds API / market-data feeds;
- AWS low-latency infrastructure;
- Railway services that remain active;
- opportunity discovery and counterpart certification;
- settlement-rule equivalence;
- order execution and fill detection;
- rescue / flatten logic;
- risk controls and capital reservation;
- alerts, observability, reconciliation, and realized P&L.

Keep unrelated executors, especially the separate SOL executor, out of this architecture and out of ARB accounting.

## Accounting / status vocabulary

Never blur these states:

- **paper/shadow** — no venue mutation;
- **candidate** — passes modeled gates only;
- **executable** — fresh executable quotes, compatible settlement rules, sufficient depth/capital, and all live gates pass;
- **submitted** — at least one real order sent;
- **filled** — venue-confirmed fill(s);
- **matched/locked** — all required legs filled such that economic outcome is locked, but settlement may still be pending;
- **settled** — venue settlement complete;
- **realized** — settled P&L reconciled against actual fills, fees, rewards/rebates actually received, and account state.

Do **not** count stale quotes, mismatched settlement rules, modeled rewards, shadow results, unconfirmed fills, or open matched positions as realized arbitrage profit.

## Engineering operating contract

### 1. Repo first

All code used for a diagnostic, test, deployment, launcher, recovery path, or live canary must exist in Git before it is relied upon.

Avoid large executable Python programs embedded in GitHub Actions YAML, chat messages, CloudShell snippets, or one-off SSM commands. Workflows should orchestrate committed code.

### 2. Exact-SHA execution

AWS/Railway execution must identify the exact Git SHA being run. A workflow may live on a control branch while an executable strategy is pinned to a separate frozen release ref, but that split must be explicit.

Do not assume branch HEAD equals production.

### 3. Frozen release discipline

A frozen live release is immutable evidence. Do not silently modify it after qualification or a canary. If code changes, create and qualify a new release SHA/ref.

### 4. Establish live state before changing anything

Before rebuilding, deploying, arming, or proposing a replacement:

- inspect current Git refs;
- inspect current AWS/Railway state;
- inspect current account orders/positions/reservations;
- inspect latest relevant workflow/deployment results;
- determine what has already been built/tested/learned.

Preserve working infrastructure and do not disrupt unrelated services.

### 5. Read-only diagnosis before repeated live attempts

If a live canary does not answer the intended question, do not keep repeating the same canary. First make the diagnostic answer *why*.

A no-trade diagnostic must distinguish at least:

- MARKET_GATE;
- ECONOMIC_GATE;
- SAFETY_GATE;
- VENUE_DATA_ERROR;
- INTERNAL/HARNESS_ERROR;
- QUALIFIED.

For each rejection, record the exact gate and the relevant fresh market values. For transport failures, record endpoint/context, exception type, bounded error message, and timing.

### 6. Tests must answer the business/execution question

Passing unit tests is necessary but not sufficient. Every live workstream should state the question it is trying to answer.

For the current Gemini BTC5 adaptive-maker lane the key question is:

> Can a real BTC5 opportunity pass qualification, select the largest safe valid clip up to q10, enter maker-only orders, experience a real first-leg fill, complete or rescue within the bounded loss invariant, reconcile cleanly, settle, and produce verifiable realized P&L without repeating a naked-leg loss?

A test campaign that only proves "the bot stayed safe and did not trade" is useful safety evidence, but it does **not** answer the full question above.

### 7. Live authorization boundaries

Real-money execution requires fresh explicit user authorization for the exact bounded action being taken. Authorization for one canary does not authorize another canary or changed code.

Never broaden q, duration, campaign count, continuous mode, loss limits, venues, or strategy behavior under an older approval.

Authorization to research, code, diagnose, qualify, freeze, or prepare a launcher is **not** authorization to trade.

### 8. Safety / recovery

Before order entry:

- account and local state must be reconciled;
- required capital must be available/reserved;
- settlement rules must be compatible where cross-venue;
- fresh market data only;
- rescue/flatten capacity must be proven for the maximum possible first fill;
- loss and daily stops must be armed.

After execution:

- reconcile orders and positions;
- identify residual exposure explicitly;
- do not call matched positions realized before settlement;
- recover first if residual/open state exists; do not start a new campaign.

### 9. Observability must not break orchestration

Do not let verbose logs cause false workflow failures. Success/failure markers needed by orchestration should be written to compact result files or emitted in bounded output that cannot be lost to SSM/GitHub truncation.

Watchdogs/sidecars should log state transitions or rate-limited heartbeats, not unbounded repetitive lines.

### 10. Secrets

Never print, commit, or expose API keys, private keys, tokens, or credentials. Use existing secret stores/env files and preserve their boundaries.

## Required process reference

Before changing or reviewing a live-capable strategy, read:

- `docs/ARB_RELEASE_AND_LIVE_CANARY_PROCESS.md`

That document is the default evidence/release discipline: repo first, exact SHA, deterministic failure-injection tests, credential-free public-market qualification, authenticated disarmed readiness, immutable freeze, inert launcher, fresh one-use authorization, and account-level post-canary reconciliation.

## Current Gemini BTC5 adaptive maker V5.3 — review baseline

As of 2026-08-30:

- Frozen release branch: `release/gemini-adaptive-maker-v5-3-final`
- Frozen release SHA: `1f876470e9e4de43235914379636c4b16d02c8ab`
- Control branch: `aws-migration-foundation`
- AWS region: `us-east-1`
- AWS hot host: `i-0b90f0a309153f47e`
- Qualification evidence: `docs/ARB_GEMINI_ADAPTIVE_V5_3_RELEASE_QUALIFICATION_20260830.md`
- Live launcher: `.github/workflows/aws-gemini-adaptive-v5-3-live.yml`
- Live trigger path: `deploy/aws/gemini-adaptive-v5-3-live.trigger`
- Current trigger file: **absent**
- V5.3 live orders/fills: **none yet**
- V5.3 realized P&L: **$0**

### What changed from V5.1/V5.2

V5.3 is **not** a liquidity-reward admission strategy. It does not count expected liquidity rewards or maker rebates in modeled P&L, and the maker-rebate price band is not treated as an execution gate.

V5.3 instead:

- maintains a persistent public depth-5 cache;
- honors live instrument `quantityMinimum`, `quantityIncrement`, and `priceIncrement`;
- considers only valid integer clips q1..q10 for the instrument grid;
- chooses the **largest safe valid clip**;
- requires >=5c/contract completed-pair profit after conservative maker fees;
- preserves the $0.30 absolute first-fill rescue-loss limit;
- simulates/removes known owned resting liquidity before claiming external rescue depth;
- requires newer post-mutation depth before relying on own-order-subtracted rescue checks;
- persists selected quantity as the campaign exposure bound;
- treats acknowledged-but-unresolved orders, fill/reconcile contradictions, pending completion/rescue state, and oversized residuals as ambiguity/hard-halt conditions rather than clean zero-fill outcomes;
- inherits the hardened V5.2 FOK/recovery/dead-man behavior.

### Frozen qualification evidence

Focused release test run:

- Run `33333850141`, job `99317120184`
- Exact SHA `1f876470e9e4de43235914379636c4b16d02c8ab`
- **90 passed**

Credential-free public AWS diagnostic:

- Run `33333898427`, job `99317249469`
- Exact SHA `1f876470e9e4de43235914379636c4b16d02c8ab`
- Current Gemini BTC5 contract metadata: quantity min `0.01`, quantity increment `0.01`, price increment `0.01`
- Valid integer clips: q1..q10
- Observed book: 41c bid / 61c ask, 100 contracts at both top levels
- Selected plan: q10, YES 41c / NO 39c
- Modeled completed-pair profit after conservative maker fees: **$1.90**
- Required edge floor: $0.50
- Worst immediate rescue loss: **$0.22** <= $0.30 limit
- YES-only-posted simulation safe: true
- Both-orders-posted standing rescue safe: true
- Owned displayed depth removed before rescue evaluation: true
- Liquidity rewards modeled: false
- Maker rebates modeled: false
- Order entry: false
- Realized P&L: $0

Authenticated disarmed AWS readiness:

- Run `33333927235`, job `99317322238`
- Exact SHA `1f876470e9e4de43235914379636c4b16d02c8ab`
- Exact-image **90 passed**
- Recovery audit: `CLEAN_NO_OPEN_STATE`
- Account orders: 0
- Account positions: 0
- Available USD: $52.3155 at qualification
- Dead-man: idle
- Sidecar: waiting
- Live/recovery/watchdog arms: false
- Order entry: false
- Realized P&L: $0

### Live launcher state

The launcher is prepared but inert.

It has no generic `workflow_dispatch` path for live mutation. It runs only if `deploy/aws/gemini-adaptive-v5-3-live.trigger` is created/changed on the control branch with:

```text
PINNED_COMMIT=1f876470e9e4de43235914379636c4b16d02c8ab
APPROVAL_ID=<fresh unique one-use id>
CLASSIFICATION=FRESH_USER_APPROVED_ONE_ADAPTIVE_BTC5_V5_3_CAMPAIGN
LIVE_AUTHORIZATION=EXPLICIT_USER_APPROVAL_ADAPTIVE_BTC5_V5_3
```

Do **not** create that file or infer approval from earlier V5.1/V5.2 canaries. The next live V5.3 canary requires fresh explicit user authorization after this frozen qualification.

The launcher is bounded to one campaign, continuous mode off, adaptive q <= 10, $0.30 rescue loss, $1 campaign loss, $3 daily loss, independent V5.3 dead-man, bounded executor lifetime, and final account/residual reconciliation.

## Claude Code review request

Review the frozen V5.3 release and the two process/evidence documents, not just the old V5.1 incident.

Primary questions:

1. Does instrument-grid handling correctly prevent submission of a quantity invalid under `quantityMinimum` / `quantityIncrement` while still allowing q1..q10 where valid?
2. Can the adaptive quote planner ever cross/take liquidity despite the maker-only/MOC intent?
3. Does the depth-5 own-order subtraction correctly handle improved quotes, same-price external liquidity, partial fills, and fall-through to the next external level?
4. After the first YES order is acknowledged, is there any path that can post NO without first proving the updated book/rescue invariant?
5. Are acknowledged-but-unresolved YES/NO/grace/FOK orders fail-closed without duplicate mutation?
6. Is selected adaptive quantity the true bound through cancellation, reconciliation, maker grace, FOK rescue, durable recovery, and dead-man intervention?
7. Can partial fills or a partially completed maker-grace order produce an incorrect residual cost basis during crash recovery?
8. Does the 15-second dead-man threshold still avoid racing legitimate REST/FOK/reconcile work under worst-case bounded timeouts?
9. Is the inert live workflow's total SSM/GitHub timeout budget sufficient for candidate wait + executor + dead-man + settlement reconciliation, and can its compact output still false-red?
10. What exact evidence should the first explicitly approved V5.3 live canary collect to decide whether to keep q adaptive up to 10, reduce size, improve quote placement, or abandon this lane?

Document any new findings in the repo. Prefer small, auditable fixes. Any change to frozen release behavior requires a new release candidate and the full qualification sequence again.

## Key rule for all future work

**Do not repeat a failed experiment until the instrumentation can explain the previous failure.**

The purpose of testing is not to accumulate green checks. It is to reduce uncertainty about whether this system can safely convert executable opportunity into realized net dollars/day.
