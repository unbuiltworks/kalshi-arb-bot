# ARB Gemini BTC5 q10 V5.1 — Claude Code Review Handoff

Date: 2026-08-30

## Objective

Review the current Gemini BTC5 q10 V5.1 lane end to end and align implementation/testing with the ARB operating process in `CLAUDE.md`.

The review should prioritize the shortest safe path to answering the live execution question, not broad refactoring.

## Current refs

Control branch:

`aws-migration-foundation`

Frozen live release ref:

`release/gemini-q10-production-v5-1-final`

Frozen live release SHA:

`ff4bc84b40b5bf79c80406c8e0d7ade77ac58e85`

Do not silently modify the frozen release. Treat it as immutable evidence from the first V5.1 canary. Any implementation change should become a new reviewed/qualified release SHA.

## Current live question

Can V5.1 safely convert a real BTC5 q10 opportunity into a completed pair or bounded rescue after a real first-leg fill, then reconcile and settle without naked residual exposure?

The first V5.1 canary did **not** answer that question because it never reached order entry.

## First V5.1 live canary evidence

GitHub Actions run:

`33326346553`

Live job:

`99297044410`

Observed evidence:

- exact release SHA was pinned correctly;
- 58/58 focused release safety tests passed;
- fresh recovery/account preflight: `CLEAN_NO_OPEN_STATE`;
- account orders: 0;
- account positions: 0;
- available USD at preflight: `$52.3155`;
- BTC spot guard reached `SPOT_READY` / `CALM`;
- qualifier then produced repeated `QUALIFIER_RETRY` events with only `RuntimeError` / `TimeoutError` classification;
- no order entry occurred;
- campaign returned `NO_CANDIDATE`;
- no fill occurred;
- no realized profit or loss resulted from this canary;
- independent dead-man did not intervene because no open campaign reached mutation;
- AWS SSM command status was `Success`;
- GitHub job was red because the workflow later grepped the SSM stdout for the completion marker after SSM output had been truncated by repetitive sidecar logging.

Important classification:

**Safe no-trade canary. Not evidence that V5.1 rescue works live. Not a trading loss. Not realized P&L.**

## Why the first canary was not informative enough

### Qualifier observability

Frozen release file:

`src/gemini_liquidity_reward_live_probe_q10.py`

Current qualifier behavior collapses several distinct rejection reasons into `None` and catches broad exceptions as:

- `error_type=RuntimeError`; or
- `error_type=TimeoutError`.

The log does not preserve enough information to answer whether each poll failed because of:

- active BTC5 market discovery;
- expiry window;
- reward program/event availability;
- nonzero qualifying maker count;
- reward-distance gate;
- price band;
- pair edge;
- websocket depth acquisition;
- Gemini HTTP/JSON transport;
- or rescue feasibility.

That is the main reason a full 10-minute canary could end with `NO_CANDIDATE` while still leaving the root cause uncertain.

### Rediscovered depth problem

A prior incident on 2026-08-27 already demonstrated that a single bounded Gemini depth websocket subscription can transiently fail even when the feed is otherwise usable.

Incident document:

`docs/ARB_SHORT_CRYPTO_DEPTH_PREFLIGHT_INCIDENT_20260827.md`

Historical fixed checkpoint:

`f1c70209a57764695b45fec586859a05d7640770`

Historical fix behavior:

- up to 3 independent fresh websocket subscriptions;
- each attempt remains bounded;
- short retry delay;
- no REST price fallback;
- no stale depth fallback;
- fail closed after final attempt;
- regression tests for retry-then-success and exhaustion.

V5.1 still references the older one-shot `fresh_top()` path during qualification, so this previously solved failure class resurfaced.

## New control-branch review files

### Read-only qualifier diagnostic

`deploy/aws/v51_qualifier_diagnostic.py`

Purpose:

- run against the exact frozen V5.1 image;
- use no live authorization;
- place no orders;
- preserve exact rejection reason and relevant market values;
- retry fresh depth up to three independent subscriptions;
- classify failures as `MARKET_GATE`, `ECONOMIC_GATE`, `SAFETY_GATE`, `VENUE_DATA_ERROR`, `QUALIFIED`, or internal diagnostic error;
- emit one compact final JSON result.

### Reusable depth retry helper

`src/gemini_btc5m_fresh_depth_retry.py`

This is a dependency-injected, fail-closed implementation of the proven August 27 retry behavior.

### Regression tests

`tests/test_gemini_btc5m_fresh_depth_retry.py`

Covers:

- transient failure then success;
- fail closed after three failures;
- invalid configuration rejection.

### Read-only AWS workflow

`.github/workflows/aws-gemini-q10-v5-1-qualifier-diagnostic.yml`

The workflow now executes the committed diagnostic source rather than a giant Python heredoc embedded in YAML.

It validates/compiles the committed source first, runs the retry unit test, builds the exact frozen release image, mounts the committed diagnostic into that image, and leaves both live-arm variables explicitly `NO`.

## Harness bugs found during this review

Two harness failures should not be confused with trading-system failures:

1. The original live workflow emitted enough repetitive dead-man output that SSM stdout was truncated, so GitHub could not find a completion marker that the remote process had reached.
2. The first version of the new read-only diagnostic workflow used `docker run ... python - <<'PY'` without `-i`, so stdin never reached the container. AWS returned success with empty stdout and GitHub correctly rejected the missing diagnostic marker.

Both are examples of why orchestration logic should execute committed files and use compact durable results rather than long stdout streams.

## Frozen release files Claude should inspect

At `ff4bc84b40b5bf79c80406c8e0d7ade77ac58e85`:

- `src/gemini_liquidity_reward_live_probe_q10.py`
- `src/gemini_q10_production_executor.py`
- `src/gemini_q10_production_executor_v5.py`
- `src/gemini_q10_production_executor_v5_1.py`
- `src/gemini_q10_rescue_policy.py`
- `src/gemini_prediction_book_cache.py`
- `src/gemini_q10_v5_recovery.py`
- `src/gemini_q10_v5_deadman_sidecar.py`
- `src/gemini_q10_v5_recovery_deadman.py`
- `src/btc_spot_toxicity_guard.py`

Relevant focused tests:

- `tests/test_btc_spot_toxicity_guard.py`
- `tests/test_gemini_prediction_book_cache.py`
- `tests/test_gemini_q10_rescue_policy.py`
- `tests/test_gemini_liquidity_reward_dynamic_completion_q10.py`
- `tests/test_gemini_q10_production_executor.py`
- `tests/test_gemini_q10_production_executor_v2.py`
- `tests/test_gemini_q10_production_executor_v5.py`
- `tests/test_gemini_q10_production_executor_v5_1.py`
- `tests/test_gemini_q10_v5_recovery_deadman.py`
- `tests/test_gemini_q10_v5_deadman_sidecar.py`

Live/control workflows to inspect:

- `.github/workflows/aws-gemini-q10-v5-1-live.yml`
- `.github/workflows/aws-gemini-q10-v5-1-release-qualification.yml`
- `.github/workflows/aws-gemini-q10-v5-recovery-readiness.yml`
- `.github/workflows/aws-gemini-q10-v5-sidecar-smoke.yml`
- `.github/workflows/aws-gemini-q10-v5-1-qualifier-diagnostic.yml`

## Required review questions

Claude Code should provide concrete answers to all of these before proposing another live release:

1. What exact code paths produced the `RuntimeError` and `TimeoutError` qualifier retries in the first V5.1 canary?
2. Which of those are expected transient venue-data failures versus bugs in our own discovery/qualification logic?
3. Is integrating the three-attempt fresh-only depth retry into V5.1 qualification safe and sufficient?
4. Should the same retry policy be used for the final immediate pre-mutation read, or should that path remain stricter/different?
5. Does V5.1's standing rescue check prove a <=$0.30 rescue for the **full possible q10 first fill** on both YES and NO?
6. Does that protection hold during the sequential posting gap after YES is accepted but before NO is accepted?
7. Are cancel races, partial fills, websocket event loss, stale cache, FOK rejection, FOK ambiguous ack, and order-history lag reconciled fail-closed?
8. Does any code path permit a new campaign when durable state or account exposure is unresolved?
9. Does any reward estimate/rebate relax a rescue or loss bound? It must not.
10. Are all live P&L/status classifications truthful about submitted, filled, matched, settled, and realized states?
11. Which current tests are purely synthetic/unit tests, and which tests provide real integration evidence against Gemini market/account behavior?
12. What integration test can safely validate candidate discovery and fresh-depth reliability without order entry?
13. What is the smallest new release patch that makes the next canary meaningfully more likely to reach order entry while preserving all current safety limits?
14. How should the live workflow produce a small durable result summary that cannot be lost to SSM stdout truncation?
15. Can dead-man logging be rate-limited to state changes/periodic summaries without reducing recovery safety?

## Expected Claude output

Prefer one focused review document plus small patches.

Recommended deliverable:

`docs/ARB_V5_1_CLAUDE_REVIEW_FINDINGS_20260830.md`

It should contain:

- severity-ranked findings;
- exact file/function references;
- evidence from current code/tests;
- what is already proven versus still unproven;
- proposed minimal patch set;
- tests added/changed;
- release-qualification plan;
- exact condition under which a new live-canary approval should be requested.

Do not arm or run a new live canary during review.

## Process alignment

The authoritative process is now in root `CLAUDE.md`.

Most important rule:

> Do not repeat a failed experiment until the instrumentation can explain the previous failure.

The next release should exist only after this review converts the current ambiguous `NO_CANDIDATE` result into a diagnosable, reproducible gate outcome and closes any rediscovered reliability regressions.
