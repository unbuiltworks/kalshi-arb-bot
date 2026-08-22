# ARB Control Plane

Last verified: 2026-08-22 00:58 UTC

This file is the durable handoff point for the ARB project. Production state wins over old chat history. New work must establish current Railway + GitHub state first, then update this file after meaningful changes.

## State definitions

- **PAPER/SHADOW**: observes or simulates only; no order entry.
- **LIVE PROBE**: bounded real-order capability with explicit safety gates.
- **EXECUTABLE**: exact counterpart/rule match plus fresh after-fee prices and sufficient depth/capital.
- **EXECUTED**: all required legs have confirmed fills.
- **REALIZED**: executed trade has been reconciled/settled and P&L is confirmed.

Only REALIZED P&L counts toward the $480/day objective. Never count stale quotes, settlement-rule mismatches, shadow P&L, armed state, attempted orders, manufactured markets, RFQs, or unconfirmed fills as realized arbitrage profit.

## Current production snapshot

| Workstream | Railway service | Current state | Last verified evidence | Immediate blocker / next action |
| --- | --- | --- | --- | --- |
| Crypto 15m BTC/ETH/SOL/XRP | `crypto-15m-sol-live-canary-10bp-50ms` | **LIVE PROBE / REALIZED P&L EXISTS** | Deployment `ffed4362-01e4-4d1f-9db7-0cc64a3cdb3f` was verified `SUCCESS` on `feature/sol-15m-live-canary-10bp-50ms` commit `75553b9...`. Prior XRP q=1 trade remains **+$0.0888 net REALIZED**. Shared Gemini market-data socket, private execution-WS prewarm, staggered Kalshi REST and near-miss telemetry are deployed. | Current taker transport is substantially healthy; do not force trades when economics are absent. Preserve q=1, >=3¢ modeled after-fee profit, double-boundary, 50ms survival, freshness/skew, exact-payload, Gemini-first and settlement gates. Maker flow remains the leading frequency research path once resting-order race protection is proven. |
| Crypto maker | `crypto-maker-arb-shadow-v1` + `crypto-maker-live-probe-q1` | **SHADOW / LIVE PROBE DISARMED** | Shadow has simulated materially more fills, especially BTC, but reports `INDEX_MISMATCH_RESEARCH_ONLY` / `execution_eligible=false`. Live maker probe remains disarmed. | Add/verify continuous settlement/boundary, freshness and hedge-marketability protection while a maker order rests before any live arm. |
| Sports manufactured MVE combo | `sports-kp-combo-shadow` | **LIVE PROBE CAPABLE / GENERATOR ACTIVE / NO EXECUTED TRADE THIS CHECKPOINT** | Deployment `cae53261-6060-4d9c-b980-254d4016a3c0` is `SUCCESS` on branch `feature/kp-combo-synthetic-shadow`, stable entrypoint now imports v21. Startup reports `automatic_combo_generation=true`, `auto_q1_requested=true`, `live_gate=true`, `max_live_attempts_per_process=1`. Latest verified cycle saw 31 live Polymarket sports events, 40 Kalshi event-market calls, 1,629 eligible Kalshi component legs, 0 Kalshi 429s, 80 candidate baskets, and 12 successfully created/resolved MVE markets. `SPORTS_MVE_DUPLICATE_EVENT_PREFILTER` rejected 11 invalid nested same-event baskets before creation. No >=3¢ executable manufactured combo and no q=1 order/fill was observed. | Continue manufacturing valid short-horizon baskets and diagnose RFQ quoteability/economics. Do not count MVE creation or RFQs as trades. Same-Kalshi q=1 execution may fire only after fresh conservative >=3¢ after-fee lock and all existing final checks. Cross-venue Poly/Gemini replacements remain ranking-only until exact settlement rules are certified. |
| Direct sports K↔P | `sports-kp-live-executor` | **LIVE READ-ONLY CERTIFICATION SCAN / NOT EXECUTABLE** | Deployment `ad6d8c60-5806-46be-b8c3-cad809d6b35e` is `SUCCESS` on `feature/sports-kp-live-executor-impl` commit `f9dfd286...`. Poly-triggered exact Kalshi depth=1 sync is active. First verified post-fix 30-second summaries showed ~57 exact syncs/window but best after-fee unit profit only **-1.5¢ then -1.0¢**; no certification candidate. There are still 0 execution approvals. | Expand certified market breadth instead of weakening gates. Preserve >=3¢ after-fee, <=50ms skew, <=100ms freshness, persistence, explicit rule fingerprint and final exact depth. |
| MLB prop G↔K | `sports-gk-autonomous-supervisor` | **LIVE RULE-RISK PROBE / REALIZED P&L EXISTS / NOT CERTIFIED GUARANTEED ARB** | Deployment `38ae97c8-742a-4d49-8b70-cff80b6ad87f` is `SUCCESS`. Five historical both-filled settled probes total **+$0.1871 net REALIZED**. Authoritative cumulative rule-risk budget remains **$4.84/$5.00**, only $0.16 remaining. No new G↔K order was authorized this checkpoint. | Do not reset/increase the $5 rule-risk budget without explicit user authorization. Existing hits/HR/TB/K families still lack complete exceptional-event lifecycle equivalence, so they cannot yet graduate to certified guaranteed arb. |
| Weather | `weather-live-probe` | Needs current workstream reconciliation | Service exists and is being handled separately. | Reconcile any prior order before new weather execution. |
| Command/observability | `arb-command-center` | Present | Service exists. | Keep ledger/fill truth authoritative and consolidate operational heartbeat where practical. |
| Railway hygiene | project `sports-arb-autonomous` | Cleanup in progress | Large service inventory remains. | Remove only verified dead/test/superseded services after dependency checks; preserve active/core workstreams. |

## Sports manufactured-combo checkpoint — 2026-08-22 00:58 UTC

Production had a material configuration contradiction: the combo supervisor was already requesting auto-q1 with the live gate enabled, but `ENABLE_KALSHI_MVE_GENERATOR` was false. It could therefore watch live Polymarket events without manufacturing fresh Kalshi MVE markets. The generator is now enabled.

Changes deployed on `feature/kp-combo-synthetic-shadow`:

- `cf3d0ed6...` — live same-Kalshi q=1 2-5 leg runner now enforces `MVE_LIVE_MIN_LOCKED`, default **$0.03**, using `max(base.MIN_LOCKED, configured_floor)`. It still requires current open MVE, fresh RFQ, q=1 executable component depth, zero pre-existing positions, balance buffer, component FOK fills first, immediate quote refresh before combo acceptance, flatten on incomplete/definitive failure, and hard halt on ambiguous combo acceptance. A fill is `FILLED_NOT_SETTLED`, not REALIZED.
- Production variables now include `ENABLE_KALSHI_MVE_GENERATOR=YES`, `MVE_GENERATOR_EVERY_CYCLES=10`, `MVE_GENERATOR_MAX_CREATIONS_PER_RUN=12`, `MVE_GENERATOR_MAX_EVENT_MARKET_REQUESTS_PER_RUN=48`, `MVE_GENERATOR_MIN_LOCKED=0.03`, and `MVE_LIVE_MIN_LOCKED=0.03`.
- `47b0610b...` — persistent MVE creation-attempt budget telemetry now reports non-200 creation rejects and authoritative durable attempted count. Rejects are not silently refunded; the ledger remains restart-safe/fail-closed.
- `025e53ba...` — diagnostic-only RFQ quote-shape telemetry was added when quotes exist but no usable buy-YES-equivalent quote is selected. It does not alter quote interpretation or execution.
- An existing concurrent v20 change added the America/New_York scheduled-start guard to MLB game prepricing. v21 preserves it.
- `c4588ced...` / `53ce13d7...` — v21 prefilters baskets containing more than one selection from the exact same `event_ticker`. This was driven by verified Kalshi `400 duplicated_legs` responses such as multiple nested NFL-total thresholds where one result logically guarantees another. Different same-game families such as spread + total remain eligible because they use distinct event tickers.

Latest v21 production evidence on deployment `cae53261-6060-4d9c-b980-254d4016a3c0`:

- `SPORTS_MVE_DUPLICATE_EVENT_PREFILTER`: 320 ranked legacy rows examined, **11 invalid duplicate-event baskets rejected before market creation**, 80 valid candidates returned.
- Subsequent sampled MVE creates returned HTTP 200, confirming the known nested-total waste path was removed for those rows.
- `SPORTS_MVE_REQUEST_BUDGET`: 31 live Poly events, 40 Kalshi event-market requests, **0 Kalshi 429s**, 0 request-budget skips.
- `SPORTS_MVE_LEG_DIAGNOSTIC`: 1,662 markets examined, 1,629 eligible legs, 80 candidate baskets, 12 MVE markets created/resolved.
- Poly ranking-only preprice found 69 exact identities and 65 Kalshi components with a cheaper Poly complement in that sample, but these remain `execution_eligible=false` because settlement rules are not certified.
- MLB game ranking-only preprice found 100 priced exact counterpart quotes and 41 lower-cost Poly alternatives, best shadow saving 1.5¢ on one component. This is not executable arb by itself.
- G↔K prop ranking-only pass found 254 exact joins / 92 priced rows, but no improvement and remains `guaranteed_arb=false`.
- Cross-venue combo shadow best sampled all-in was still negative; no positive economic candidate was promoted.
- No new sports q=1 order, both-filled trade, settlement or realized P&L occurred in this checkpoint.

## Direct sports K↔P checkpoint

`f9dfd28669e989760070a44d70feeab031f7efe5` changes quote synchronization only. On a Polymarket-triggered update, if the strict fresh pair is unavailable, the scanner may request one exact Kalshi depth=1 book subject to a global 250ms floor. Recomputed pairs still require <=100ms book age, <=50ms cross-venue skew, >=3¢ after-fee edge, persistence, rule confidence and final exact-depth revalidation.

The first verified post-change summaries showed the exact sync firing about 57 times/30 seconds, while current market economics were negative: best after-fee unit profit -0.0150 and then -0.0100. Therefore no order/certification candidate was justified. Earlier telemetry had briefly reached +0.0250, still below the unchanged +0.03 floor.

Direct K↔P still has **0 approved execution contracts**. Known MLB total rule audits have shown semantic identity without exact settlement equivalence due exceptional-event/source/completion differences, so do not blanket-approve totals or props.

## G↔K rule-risk / ledger truth

The current supervisor supports hits, home runs, total bases and pitcher strikeouts with MLB participation proof plus the existing q=1 >=3¢, <=50ms skew, <=100ms freshness, exact-price survival, dual-FOK, capital reservation and hard-halt transport.

It is intentionally **not certified guaranteed arbitrage** because complete Gemini vs Kalshi exceptional-event lifecycle equivalence has not been proven. The $5 cumulative experimental rule-risk ceiling must not reset by restart/date.

Verified settled G↔K probes:

- `sports-gk:sup-0820-013627-27`: +$0.0379 net
- `sports-gk:sup-0820-014520-28`: +$0.0379 net
- `sports-gk:sup-0820-015029-29`: +$0.0379 net
- `sports-gk:sup-0820-015052-30`: +$0.0379 net
- `sports-gk:sup-0819-233943-15`: +$0.0355 net

Total sports G↔K verified REALIZED net P&L: **+$0.1871**. Budget used remains **$4.84/$5.00**.

## Path to meaningful contribution toward $480/day

q=1 is a proof rung, not the economic destination. The path is:

1. Increase **valid executable opportunity breadth**: manufactured same-Kalshi MVE baskets, direct K↔P game/spread/total, props, live events, NFL/other sports, and later certified sportsbook counterparts.
2. Convert identity joins into **settlement-rule-certified** counterparts. Never promote a ranking-only cheaper leg into execution merely because its price is better.
3. Prove repeated q=1 fills and settlement/reconciliation per lane.
4. Scale quantity only after final displayed depth and cross-venue capital reservation support it: q=5 -> q=10/q=25+ rather than blind size increases.
5. Aggregate multiple independent certified lanes. The target is measured in realized net dollars/day, not theoretical opportunity count.

Current sports evidence says the biggest immediate limiter is not the ability to submit larger orders. It is the number of **valid, quoteable, rule-safe profitable baskets/counterparts**. Manufacturing is now active and wasting fewer create slots, so the next sports work should focus on RFQ quoteability/ranking and family-level settlement certification while leaving all current execution gates intact.

## Realized P&L truth

- Crypto 15m verified REALIZED net P&L: **+$0.0888**.
- Sports G↔K verified settled REALIZED net P&L: **+$0.1871**, but classified as rule-risk rather than certified guaranteed arbitrage.
- Total verified realized live P&L tracked across those two workstreams: **+$0.2759**.

Do not describe the full $0.2759 as certified guaranteed-arbitrage P&L; the sports portion remains rule-risk.

## Railway cleanup checkpoint

Preserve as CORE: `crypto-15m-sol-live-canary-10bp-50ms`, `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows while their workstreams remain active, including `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, `sports-alert-reliability-shadow`, and `crypto-maker-arb-shadow-v1`.

Remove only verified dead/test/superseded services after dependency checks. Do not infer deadness from names alone.

## New-chat startup protocol

1. Read `ARB_STATUS.md`.
2. Query current Railway deployment + runtime logs for the specific service.
3. Query the relevant GitHub branch/commit and compare it with Railway's deployed commit.
4. State the exact rung: PAPER/SHADOW, LIVE PROBE, EXECUTABLE, EXECUTED, or REALIZED.
5. Work one blocker through **inspect -> fix -> deploy -> verify -> reconcile -> checkpoint** before expanding scope.

If production disagrees with this file, production wins and this file must be updated.

## Checkpoint protocol

After a meaningful change record UTC verification time, service/deployment ID, deployed commit/branch, armed/disarmed state, heartbeat/candidate/execution/fill/reconciliation result, and the next blocker. Never store API keys, private signing material, credentials, or sensitive balances here.
