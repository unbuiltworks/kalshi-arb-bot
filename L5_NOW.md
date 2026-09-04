# L5 NOW — Fast Recovery State

**Purpose:** This is the first file ChatGPT/Claude should read when resuming L5 work after a new chat, context-limit error, handoff, or interruption. It is deliberately short. Do not reconstruct current state from old chat transcripts when this file plus live GitHub/Railway checks can answer it.

**Snapshot:** 2026-09-03 ~21:56 America/New_York (2026-09-04 ~01:56 UTC)

## Primary objective

Reverse-engineer Polymarket trader `l5zn1bwom8etsk`, reproduce the sequential decision process as faithfully as public data allows, validate it, then deploy adapted versions only where executable conditions support positive realized expectancy.

Historical target: **$480/day realized net profit**. Target only; never assume achieved.

## Current priority

**Polymarket live sports first.** There are many live sporting events, so lack of opportunity supply is not an acceptable default explanation. The immediate job is to ingest the full live sports universe, capture L5-relevant market state/evidence, rank usable markets, and validate the sports version of the L5 engine. Do not drift back to Gemini, generic arbitrage, or unrelated strategies unless evidence makes that the highest-impact path.

## Current GitHub state

Repository: `unbuiltworks/kalshi-arb-bot`

- **Active operational sports line:** `feature/l5-transfer-backtest` at `365bd1ac64871d97e7f0938de611970fd72a7cd2`.
- `feature/l5-sports-live-universe-hotfix` at `59263edda5cd5d2865eb6c05a62167ed70560b78` is the merge base of that current line; `feature/l5-transfer-backtest` is **2 commits ahead**.
- Those two follow-up commits:
  - raised the authoritative live-sports runner default to **64 seats** while preserving explicit `PMUS_MAX_MARKETS` as the operator rate-safety knob and kept everything **DISARMED**; suite reported **475 green**;
  - aligned the deploy-safety workflow with the authoritative `src.pmus_sports_live_collector` CMD and isolation invariant.
- Earlier sports work on the lineage includes the authoritative all-live sports collector, full live-sports coverage/stale-incentive exclusion tests, full-slate deployment behavior, and rotating-surplus identification by selection order.
- Historical/canonical L5 research branch: `feature/l5-canonical` at `c6b357675410a988a0d906438d4e875eb6320964` when last checked.
- GitHub issue **#167** remains the canonical consolidation mission.
- `L5_STATUS.md` on the canonical lineage contains deeper historical facts, behavioral reconstruction, replay coverage, and strict profit-accounting definitions. It is useful background but is older than this operational snapshot.
- Always re-fetch `main` and the active branch head before making changes; this file is a pointer, not a substitute for current Git state.

## Current Railway state

Project: `arb-ev-wallet-shadow`
Production environment: `f3642d27-26dc-4e21-9170-bc76290c076f`

Relevant service snapshot:

- `l5-ops-research-live` — **SUCCESS**
- `l5-transfer-supervisor-shadow` — **SUCCESS**
- `pmus-ws-evidence-recorder` — **SUCCESS**
- `pmus-spring-l5-probe` — **SUCCESS**
- `pmus-q1-executor` — **SUCCESS deployment**
- `pmus-sports-forward-collector` — **SUCCESS**, deployment `8e4df7f1-38b3-44aa-85e9-a09b7fa02948`, from `feature/l5-transfer-backtest@365bd1ac`

The authoritative sports collector deployment is therefore no longer the blocker it was at the start of this snapshot.

**Important:** Railway deployment success does NOT mean live trading is armed. Current sports collector commit explicitly says everything stays **DISARMED**. Before any future order-capable action, separately verify arming keys, current account/exposure, durable state, risk caps, and exact executor preflight. Never infer `armed` from `SUCCESS`.

## Current bottleneck

The sports bottleneck has moved from deployment toward **verifying actual live-universe capture and evidence quality/readiness**. The collector now has the authoritative all-live sports path with a >=64 default market capacity.

Near-term sequence:

1. verify the live collector is actually seeing the broad current sports slate, not merely running successfully;
2. confirm the evidence recorder preserves required book/flow/state for those markets;
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