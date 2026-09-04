# L5 NOW — Fast Recovery State

**Purpose:** This is the first file ChatGPT/Claude should read when resuming L5 work after a new chat, context-limit error, handoff, or interruption. It is deliberately short. Do not reconstruct current state from old chat transcripts when this file plus live GitHub/Railway checks can answer it.

**Snapshot:** 2026-09-03 ~21:55 America/New_York (2026-09-04 ~01:55 UTC)

## Primary objective

Reverse-engineer Polymarket trader `l5zn1bwom8etsk`, reproduce the sequential decision process as faithfully as public data allows, validate it, then deploy adapted versions only where executable conditions support positive realized expectancy.

Historical target: **$480/day realized net profit**. Target only; never assume achieved.

## Current priority

**Polymarket live sports first.** There are many live sporting events, so lack of opportunity supply is not an acceptable default explanation. The immediate job is to ingest the full live sports universe, capture L5-relevant market state/evidence, rank usable markets, and validate the sports version of the L5 engine. Do not drift back to Gemini, generic arbitrage, or unrelated strategies unless evidence makes that the highest-impact path.

## Current GitHub state

Repository: `unbuiltworks/kalshi-arb-bot`

- `main` checked at `ed53f79037554c1ce24379ca140954f7d7801db1` (`ops: fail open generic deploy when Railway secrets are unset`).
- **Active sports work:** `feature/l5-sports-live-universe-hotfix` checked at `59263edda5cd5d2865eb6c05a62167ed70560b78`.
- Recent sports-branch work includes:
  - authoritative all-live sports collector;
  - tests locking full live-sports coverage and stale-incentive exclusion;
  - full-slate prefix default in the deployment wrapper;
  - rotating-surplus identification by selection order.
- Historical/canonical L5 research branch: `feature/l5-canonical`, checked at `c6b357675410a988a0d906438d4e875eb6320964`.
- GitHub issue **#167** remains the canonical consolidation mission.
- `L5_STATUS.md` on the canonical/sports lineage contains the deeper historical facts, behavioral reconstruction, replay coverage, and strict profit-accounting definitions. It is useful background but is older than this operational snapshot.

## Current Railway state

Project: `arb-ev-wallet-shadow`
Production environment: `f3642d27-26dc-4e21-9170-bc76290c076f`

Relevant service snapshot:

- `l5-ops-research-live` — **SUCCESS**
- `l5-transfer-supervisor-shadow` — **SUCCESS**
- `pmus-ws-evidence-recorder` — **SUCCESS**
- `pmus-spring-l5-probe` — **SUCCESS**
- `pmus-q1-executor` — **SUCCESS deployment**
- `pmus-sports-forward-collector` — **DEPLOYING** at snapshot time

**Important:** Railway deployment success does NOT mean live trading is armed. Before any order-capable action, separately verify arming keys, current account/exposure, durable state, risk caps, and exact executor preflight. Never infer `armed` from `SUCCESS`.

## Current bottleneck

The working hypothesis is that the sports bottleneck is **market discovery / universe coverage / evidence capture / readiness logic**, not absence of live sports markets. The current sports hotfix is aimed directly at that.

Near-term sequence:

1. finish/verify full live sports ingestion;
2. confirm the recorder sees the actual live universe and preserves the required book/flow/evidence state;
3. rank markets using L5-relevant conditions (two-sided flow, spread/depth, movement, time remaining, executable economics);
4. shadow the L5-style policy across eligible sports;
5. compare action-by-action against the behavioral model where applicable;
6. only then advance a bounded live probe when strict executable evidence clears the gate.

## Strict accounting / safety

Use status labels exactly:

`historical replay -> shadow -> live probe -> submitted -> filled -> settled -> realized`

Never call modeled/shadow/theoretical profit realized. Realized requires authoritative fills, settlement, and confirmed cashflow.

Keep separate:
- matched-pair economics;
- directional residual P&L;
- spread capture;
- maker rebates;
- liquidity rewards;
- fees;
- settlement/redemption cashflows.

Do not weaken risk limits to manufacture activity. Do not recommend evading Polymarket geographic/platform restrictions.

## Fresh-chat recovery protocol

When the user says **"Continue L5"**, **"pick up where we left off"**, or a chat hits its context limit:

1. Read this file first.
2. Re-fetch the heads of `main` and the active branch above; if another L5 branch is newer/material, identify it rather than assuming this snapshot is current.
3. Check Railway status for `arb-ev-wallet-shadow`, especially the L5/PMUS services above.
4. Check issue #167 only for mission/decisions that are not already reflected here.
5. Read deeper `L5_STATUS.md` only when historical/research detail is needed.
6. Continue from the live state. **Do not ask the user to paste the previous chat.**
7. After a material change in active branch, deployment, blocker, live-probe status, or realized P&L, update this file with a new timestamp.

## Context-budget rule

Keep chats lean:

- Do not paste full logs, giant test outputs, entire issue bodies, or old handoffs unless required for a decision.
- Summarize deltas and cite the commit/deployment/source of truth.
- Store durable state/results in GitHub, not only in chat.
- Prefer one canonical pipeline and one current handoff file over new parallel implementations/status documents.

This file is an operational pointer, not a replacement for source data, tests, ledgers, or authoritative exchange evidence.