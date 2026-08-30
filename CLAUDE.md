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

A frozen live release is immutable evidence. Do not silently modify it after a canary. If code changes, create and qualify a new release SHA/ref.

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

For the current Gemini BTC5 q10 lane the key question is:

> Can a real BTC5 opportunity pass qualification, enter q10 maker orders, experience a real first-leg fill, complete or rescue within the bounded loss invariant, reconcile cleanly, settle, and produce verifiable realized P&L without repeating a naked-leg loss?

A test campaign that only proves "the bot stayed safe and did not trade" is useful safety evidence, but it does **not** answer the full question above.

### 7. Live authorization boundaries

Real-money execution requires fresh explicit user authorization for the exact bounded action being taken. Authorization for one canary does not authorize another canary or changed code.

Never broaden q, duration, campaign count, continuous mode, loss limits, venues, or strategy behavior under an older approval.

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

## Current Gemini BTC5 q10 V5.1 lane — review baseline

As of 2026-08-30:

- Frozen executable release ref: `release/gemini-q10-production-v5-1-final`
- Frozen release SHA: `ff4bc84b40b5bf79c80406c8e0d7ade77ac58e85`
- Control branch: `aws-migration-foundation`
- AWS region: `us-east-1`
- AWS hot host: `i-0b90f0a309153f47e`

The frozen V5.1 live workflow is intentionally bounded to one q10 BTC5 campaign with continuous mode off and the existing V5.1 safety limits.

### Most recent live-canary evidence

The first V5.1 q10 canary:

- passed 58/58 focused release safety tests;
- preflighted clean with 0 orders / 0 positions and $52.3155 available at that moment;
- submitted **no orders**;
- produced **no fills**;
- produced **$0 realized P&L**;
- returned `NO_CANDIDATE` after repeated qualifier `RuntimeError` / `TimeoutError` events;
- left no demonstrated trading loss;
- produced a misleading red GitHub workflow because orchestration searched truncated SSM stdout for a completion marker.

This run is safety evidence, not execution/rescue proof.

### Known issue rediscovered

On 2026-08-27 an earlier Gemini BTC5 lane already established that a single bounded `fresh_top()` websocket subscription can miss fresh depth transiently. A three-independent-attempt fresh-only retry was built and tested at historical commit:

`f1c70209a57764695b45fec586859a05d7640770`

That retry preserves fail-closed behavior and uses no REST/stale fallback.

V5.1's qualifier still calls the older one-shot helper, so the same failure class resurfaced.

### New reviewable control-branch files

- `deploy/aws/v51_qualifier_diagnostic.py`
  - read-only, no credentials required, no order entry;
  - classifies every rejection and preserves exact bounded error detail;
  - uses three independent fresh-depth attempts;
  - emits one compact final result.

- `src/gemini_btc5m_fresh_depth_retry.py`
  - reusable dependency-injected fresh-only retry helper.

- `tests/test_gemini_btc5m_fresh_depth_retry.py`
  - retry-then-success, fail-closed exhaustion, and argument validation.

- `.github/workflows/aws-gemini-q10-v5-1-qualifier-diagnostic.yml`
  - runs committed diagnostic source against the exact frozen V5.1 release image;
  - no live arming;
  - no credentials required by the diagnostic;
  - avoids the prior stdin/heredoc harness failure.

- `docs/ARB_V5_1_CLAUDE_REVIEW_HANDOFF_20260830.md`
  - review checklist, evidence, open questions, and required process.

## Claude Code review request

When reviewing this lane, do not merely run tests and report green/red. Independently inspect the frozen release implementation and answer:

1. Why did V5.1's qualifier emit repeated `RuntimeError`/`TimeoutError` rather than a useful rejection reason?
2. Is the proven three-attempt fresh-only depth retry safe to integrate into V5.1 qualification without weakening freshness or rescue invariants?
3. Are there any other historical fixes in this repo that V5/V5.1 accidentally regressed or bypassed?
4. Does V5.1 truly prove full-q10 rescue capacity before *any possible first fill*, including the sequential YES-before-NO posting window?
5. Are partial fills, cancel races, ambiguous acks, stale books, websocket reconnects, and FOK reconciliation fail-closed?
6. Can watchdog/sidecar telemetry be rate-limited while retaining enough evidence for recovery?
7. Can live orchestration rely on a compact durable result artifact/file rather than grepping potentially truncated SSM stdout?
8. Which tests are unit safety tests versus tests that actually exercise the end-to-end question?
9. What is the smallest next release change that improves the probability that the next explicitly approved canary reaches a real qualifying opportunity without weakening safety?
10. What evidence would be required after that canary to classify it as submitted, filled, matched, settled, and realized?

Document findings in the repo. Prefer a small, auditable patch over a rewrite.

## Key rule for all future work

**Do not repeat a failed experiment until the instrumentation can explain the previous failure.**

The purpose of testing is not to accumulate green checks. It is to reduce uncertainty about whether this system can safely convert executable opportunity into realized net dollars/day.
