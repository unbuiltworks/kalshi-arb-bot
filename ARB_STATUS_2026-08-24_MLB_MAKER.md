# ARB MLB Maker Checkpoint — 2026-08-24

Production state wins over this checkpoint. Only settled/reconciled realized P&L counts toward the $480/day objective.

## Workstream

Polymarket US same-venue strict MLB player-prop maker -> taker hedge feasibility.

Classification: **SHADOW / LIVE RULE-RISK RESEARCH — NOT GUARANTEED ARBITRAGE**.

## Current production shadow

Railway project: `sports-arb-autonomous`

Existing reused service: `sports-kp-combo-shadow`

Source branch: `feature/kp-combo-synthetic-shadow`

Start command: `python -m src.sports_poly_mlb_live_maker_shadow`

No new Railway service was created. The shadow path has no order-entry authorization and must not be counted as executed or realized P&L.

## Safety findings

Raw Polymarket US MLB prop rules show:

- batter markets require the listed player to be in the starting lineup and record a plate appearance; otherwise settlement may use last fair market price;
- strikeout markets require the listed player to be the starting pitcher; otherwise settlement may use last fair market price;
- delayed/postponed/suspended games can also fall into a last-fair-market-price branch when the timing condition is not met.

Therefore a pregame low-threshold YES + high-threshold NO ladder is **not guaranteed complementary settlement**. Even after participation, suspension/delay LFMP remains residual rule risk.

The maker branch participation gate was hardened on 2026-08-24:

- batter families: confirmed starter + >=1 plate appearance;
- pitcher strikeouts: confirmed starting pitcher + >=1 batter faced (not merely one pitch).

## Continuous fillability shadow

New module: `src/sports_poly_mlb_live_maker_shadow.py`

Key commits:

- `ff083648f98423197495e7a4d2575e819457975a` — harden rule-equivalence pitcher gate to batter faced
- `23c33b611c83e597c323d03f2080cd222d5b037d` — update MLB Stats participation proof
- `248fc62b6a42a7bad84f4988cb4ec43b18701bb3` — add continuous participation-gated maker fillability shadow
- `f0095ece2e44acd45eb0366cac10b0ca6d5e5121` — trigger Railway source deployment
- `a0ad254349ebe395abba0eb2115716f5979f2c6e` — pace participation polling and compact telemetry

Architecture:

1. Slow broad strict-ladder discovery.
2. Official MLB Stats API participation proof.
3. Only participation-ready candidates enter exact-book hot polling.
4. Require market-reported `orderPriceMinTickSize` and `minimumTradeQty`; do not hard-code a live tick.
5. Require q=1 hedge depth.
6. Recompute the most aggressive maker quote that preserves >=$0.03 after the current taker hedge and current Polymarket US fee/rebate math.
7. Track distance to touch and whether a later ask reaches/crosses the prior hypothetical maker quote. A crossed shadow quote is fillability evidence only, not a fill.
8. No order entry; no realized P&L.

Pacing after `a0ad254...`:

- broad discovery: 180 seconds by default;
- pregame/participation refresh: 5 seconds by default;
- once any candidate is participation-ready: exact-book hot loop 2 seconds by default.

## Immediate live snapshot

First successful continuous deployment: `6aaf5293-ca3a-463d-b4cf-bb55bb511056`.

Build validation: 37 existing sports tests passed.

Initial 2026-08-24 snapshot:

- near-term sports events scanned: 525
- strict MLB prop markets: 400
- books fetched: 389
- semantic ladders: 133
- pregame maker directions preserving >=3c: 10
- one tick from touch: 0
- families: 8 total-bases directions, 2 strikeout directions
- participation-ready: 0
- post-participation safe >=3c: 0
- crossed prior shadow quote proxy: 0
- orders sent: 0
- realized P&L from this maker lane: $0

At the time of this snapshot the relevant MLB games had not started, so zero participation-ready candidates is the expected fail-closed result.

## Promotion gate

Do **not** arm a q=1 live maker order merely because a pregame shadow row has >=3c modeled edge.

A first live maker canary remains blocked until all of the following are demonstrated/audited:

- official participation proof is present;
- actual post-participation quote is near-touch/fillable;
- market tick and lot/minimum quantity are validated from live market metadata;
- q=1 hedge depth remains available at final revalidation;
- private fill/accept/reject detection and maker lifecycle are audited;
- maker fill triggers immediate bounded taker/FOK-or-equivalent hedge behavior;
- capital is reserved for both legs before mutation;
- residual LFMP rule risk is explicitly classified/budgeted without silently increasing or resetting an existing risk budget;
- ambiguous/failed hedge behavior hard-halts and uses a bounded recovery path;
- final P&L is not counted until settlement/reconciliation.

## Other current blockers to remember

- NCAAF Gemini/Kalshi pregame moneyline has a hard postponement-rule mismatch (Gemini 7 days vs Kalshi 48 hours), so it is not guaranteed arbitrage.
- Broad tennis/sports maker counts remain quarantined until market-kind/orientation semantics are separately certified.
- Crypto basis lane remains separate from this sports workstream and must not be rearmed merely because sports maker research is progressing.

## Next action

Use the deployed participation-gated shadow to determine whether today's strict MLB ladders actually become near-touch/fillable after official participation. If they do, audit the private maker fill -> immediate hedge transport and promote one bounded q=1 live rule-risk canary. If they remain multiple ticks away and never show fillability evidence, stop treating this lane as the primary path to more realized trades and redirect engineering effort toward higher-conversion certified lanes.
