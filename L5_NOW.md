# L5 NOW — Fast Recovery State

**Purpose:** First recovery file for any new L5 chat. Re-check live GitHub/Railway state before acting; do not reconstruct from old transcripts.

**Snapshot:** 2026-09-03 ~22:50 America/New_York (2026-09-04 ~02:50 UTC)

## Primary objective
Reverse-engineer Polymarket trader `l5zn1bwom8etsk`, reproduce the sequential decision process, validate it, then deploy adapted versions only where executable conditions support positive realized expectancy. Historical target: **$480/day realized net profit**; target only, never assumed.

## Current priority
**Polymarket live sports first.** The sports shadow system is now operational across the broad live slate. Do not drift to Gemini/generic arbitrage unless evidence makes that higher impact.

## Current GitHub state
Repository: `unbuiltworks/kalshi-arb-bot`

- Active sports line: `feature/l5-transfer-backtest`.
- Current head at this checkpoint: `65b508e0af6c9acf14354153a0081c96e0576d53`.
- Parent `1d268962f00d97382559fa3324f57e6a24de21e5` is Claude's permanent PMUS `offers` ask-side parser fix + regressions; reported full suite **479 green**.
- `65b508e0` adds a read-only status hotfix so q=1 depth reads the collector's actual `bid_touch_qty` field instead of nonexistent `bid_qty`; it does not loosen the >=1c economic gate.
- Collector image packages the hotfix module.
- GitHub issue #167 remains canonical for implementation/research decisions.

## Current Railway state
Project `arb-ev-wallet-shadow`, production env `f3642d27-26dc-4e21-9170-bc76290c076f`.

Relevant services:
- `pmus-sports-forward-collector`: **SUCCESS** on deployment `f6f86b36-566e-4f27-95e0-8e0bc1d19c87`; persistent `/state/pmus-sports` volume mounted; restart policy ALWAYS.
- `pmus-ws-evidence-recorder`: **SUCCESS**; Claude raised baked market cap 40 -> 64 on `1d268962` and Railway deployed it successfully.
- `l5-ops-research-live`: SUCCESS; do not disturb.
- `l5-transfer-supervisor-shadow`: SUCCESS; do not disturb.
- `pmus-q1-executor`: SUCCESS deployment, but do not infer armed.
- `pmus-spring-l5-probe`: SUCCESS deployment but **NO volume mount is present in its service config** at this checkpoint; this remains a live-probe infrastructure blocker.

## Tonight's breakthrough
The broad sports collector was already discovering the live universe, but readiness was falsely stuck at `ask_side_served_pct=0.0` because the base collector parsed `book["asks"]` while PMUS serves sell depth under `book["offers"]`.

That is now fixed. Fresh post-fix status immediately showed served asks (including multiple markets at 100%), proving the venue was not actually bid-only.

A second false readiness gate was found: `pmus_status_report.analyze_market()` checked `bid_qty`, while collector rows contain `bid_touch_qty`. `65b508e0` corrects this interpretation without weakening the economic threshold.

## Fresh live sports status after both corrections
From the first clean status cycle on deployment `f6f86b36...`:
- `markets_tracked`: **1502** in retained tape
- 15m `markets_with_data`: **383**
- fresh selected markets since restart: **64**
- 15m statuses: **11 SHADOW_CANDIDATE**, **372 NOT_READY**, **0 Q1_GATE_CLEARED**
- Example shadow candidate `aec-setkameua-pitser-kimdmy-2026-09-04`: two-sided inferred flow, ask-side served **35.2%**, 1 theoretical matched slot, 0 one-sided holds.
- Example MLB market `atc-mlb-stl-lad-2026-09-03-f5-stl`: two-sided inferred flow, ask-side served **55.6%**, 1 theoretical matched slot.
- Several fresh markets showed ask-side served **100%** immediately after restart.

Everything above is **shadow/theoretical**, not realized profit.

## Immediate blocker / next action
The major discovery/data-schema blockers are cleared. The next evidence gate is genuine repetition:
1. strict readiness requires **>=2 qualifying 5-minute matched slots** with two-sided flow, q=1 depth, served ask evidence, and no collector gaps;
2. first fresh cycle had only one qualifying matched slot on the leading candidates, so `Q1_GATE_CLEARED=0` is currently correct;
3. keep the >=1c after-known-fees bound fail-closed; do not lower it to manufacture a candidate;
4. before any bounded live probe, also resolve the missing persistent `/state/pmus-spring` volume and re-check all current spring/category/operator arming assertions.

## Safety / accounting
Status ladder: `historical replay -> shadow -> live probe -> submitted -> filled -> settled -> realized`.
Never call modeled/shadow/theoretical profit realized. Do not arm or submit merely because a Railway service is SUCCESS.

## Fresh-chat protocol
When user says **Continue L5**:
1. read this file;
2. re-fetch active branch head and latest relevant Railway deployments;
3. read newest PMUS sports collector status lines;
4. check whether any market/category now has >=2 strict qualifying slots and `Q1_GATE_CLEARED`;
5. verify spring persistent volume and current execution preflight before any live-probe action;
6. continue immediately without asking user to paste old chat.
