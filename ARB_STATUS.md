# ARB Control Plane

Last verified: 2026-08-22 01:05 UTC

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
| Crypto 15m BTC/ETH/SOL/XRP | `crypto-15m-sol-live-canary-10bp-50ms` | **LIVE PROBE / REPEATED REALIZED P&L EXISTS** | Deployment `ffed4362-01e4-4d1f-9db7-0cc64a3cdb3f` remains `SUCCESS` on `feature/sol-15m-live-canary-10bp-50ms` commit `75553b9...`. XRP q=1 previously realized **+$0.0888 net**. A new BTC q=1 opportunity at ~00:56 UTC returned `BOTH_FILLED`, then settled at 01:00:16 UTC with **+$0.0331 net**, `venue_outcomes_agree=true`. Crypto cumulative verified REALIZED net is now **+$0.1219**. The supervisor immediately armed `gk15m-08220050-2197-03` for target trade 2 after settlement. | Keep the taker lane live exactly as configured; current transport is substantially healthy and has now proven repeated execution+settlement across XRP and BTC. Do not force trades when economics are absent. Preserve q=1, >=3¢ modeled after-fee profit, double-boundary, 50ms survival, freshness/skew, exact-payload, Gemini-first and settlement gates. |
| Crypto maker | `crypto-maker-arb-shadow-v1` + `crypto-maker-live-probe-q1` | **SHADOW / LIVE PROBE DISARMED** | Maker shadow deployment `6347c9d6-b705-459e-ba65-f2357eb61770` is `SUCCESS` on `feature/maker-arb-shadow-v1` commit `4c290487...`; 20 startup tests passed. The shadow now uses one shared Gemini Prediction `depth` RPC socket plus exact Kalshi REST books and one shared completed-trade poller. One-sided Gemini depth no longer tears down the shared socket; unusable symbols fail closed until a two-sided book appears. First stable sample: BTC 24 active simulated quotes / 1,209 real Kalshi trade prints, ETH/XRP also actively quoting, SOL recovered later; **0 Gemini shared reconnects** in the checked deployment. Target attribution is live: early BTC stale-hedge events occurred only in the 1¢/2¢ cohorts; **0 fills and 0 stale hedges at >=3¢ so far** in the first sampled windows. All output remains `INDEX_MISMATCH_RESEARCH_ONLY`, `execution_eligible=false`, `order_entry=false`. | Maker remains the leading high-frequency research path, but do not arm it yet. Continue measuring >=3¢/5¢ simulated fill frequency, then add continuous same-side/double-boundary settlement protection while a quote rests and prove fill-vs-cancel/hedge races fail closed before any real maker order. |
| Sports manufactured MVE combo | `sports-kp-combo-shadow` | **LIVE PROBE CAPABLE / GENERATOR ACTIVE / NO EXECUTED TRADE THIS CHECKPOINT** | Deployment `cae53261-6060-4d9c-b980-254d4016a3c0` is `SUCCESS` on branch `feature/kp-combo-synthetic-shadow`, stable entrypoint now imports v21. Startup reports `automatic_combo_generation=true`, `auto_q1_requested=true`, `live_gate=true`, `max_live_attempts_per_process=1`. Latest verified cycle saw 31 live Polymarket sports events, 40 Kalshi event-market calls, 1,629 eligible Kalshi component legs, 0 Kalshi 429s, 80 candidate baskets, and 12 successfully created/resolved MVE markets. `SPORTS_MVE_DUPLICATE_EVENT_PREFILTER` rejected 11 invalid nested same-event baskets before creation. No >=3¢ executable manufactured combo and no q=1 order/fill was observed. | Continue manufacturing valid short-horizon baskets and diagnose RFQ quoteability/economics. Do not count MVE creation or RFQs as trades. Same-Kalshi q=1 execution may fire only after fresh conservative >=3¢ after-fee lock and all existing final checks. Cross-venue Poly/Gemini replacements remain ranking-only until exact settlement rules are certified. |
| Direct sports K↔P | `sports-kp-live-executor` | **LIVE READ-ONLY CERTIFICATION SCAN / NOT EXECUTABLE** | Deployment `ad6d8c60-5806-46be-b8c3-cad809d6b35e` is `SUCCESS` on `feature/sports-kp-live-executor-impl` commit `f9dfd286...`. Poly-triggered exact Kalshi depth=1 sync is active. First verified post-fix 30-second summaries showed ~57 exact syncs/window but best after-fee unit profit only **-1.5¢ then -1.0¢**; no certification candidate. There are still 0 execution approvals. | Expand certified market breadth instead of weakening gates. Preserve >=3¢ after-fee, <=50ms skew, <=100ms freshness, persistence, explicit rule fingerprint and final exact depth. |
| MLB prop G↔K | `sports-gk-autonomous-supervisor` | **LIVE RULE-RISK PROBE / REALIZED P&L EXISTS / NOT CERTIFIED GUARANTEED ARB** | Deployment `38ae97c8-742a-4d49-8b70-cff80b6ad87f` is `SUCCESS`. Five historical both-filled settled probes total **+$0.1871 net REALIZED**. Authoritative cumulative rule-risk budget remains **$4.84/$5.00**, only $0.16 remaining. No new G↔K order was authorized this checkpoint. | Do not reset/increase the $5 rule-risk budget without explicit user authorization. Existing hits/HR/TB/K families still lack complete exceptional-event lifecycle equivalence, so they cannot yet graduate to certified guaranteed arb. |
| Weather | `weather-live-probe` | Needs current workstream reconciliation | Service exists and is being handled separately. | Reconcile any prior order before new weather execution. |
| Command/observability | `arb-command-center` | Present | Service exists. | Keep ledger/fill truth authoritative and consolidate operational heartbeat where practical. |
| Railway hygiene | project `sports-arb-autonomous` | Cleanup in progress | Large service inventory remains. | Remove only verified dead/test/superseded services after dependency checks; preserve active/core workstreams. |

## Crypto frequency checkpoint — 2026-08-22 01:05 UTC

The live taker lane has now produced **two separate verified REALIZED crypto trades across two assets** while preserving the same safety gates.

### XRP trade

- q=1, both legs filled and settled.
- Verified net realized P&L: **+$0.0888**.
- The first earlier XRP opportunity was safely skipped because Gemini's private execution WS handshake returned HTTP 429; Kalshi was not sent. The subsequent opportunity filled both sides and reconciled cleanly.

### BTC trade

- At ~00:55:54 UTC the live scanner produced a certified 5¢ original locked-profit BTC opportunity. Gemini FOK returned `FillOrKillWouldNotFill` with zero fill; Kalshi was correctly not sent. This was a benign `PRIMARY_NOT_FILLED`, not a loss or exposure.
- At ~00:56:01 UTC the next certified BTC opportunity had 3¢ modeled locked profit and passed the existing q=1, boundary, freshness/skew, depth and exact-payload gates.
- `CRYPTO_MICRO_LIVE_RESULT` returned `BOTH_FILLED`: Gemini q=1 at 0.8400 with 0.0100 fee; Kalshi counterpart q=1 at 0.8900 YES-space with 0.006900 fee; no flatten and no hard halt.
- At 01:00:16 UTC `CRYPTO_LIVE_RELIABLE_CAMPAIGN_REALIZED` recorded `net_pnl=0.033100`, `capital=0.966900`, `venue_outcomes_agree=True`.
- The campaign then immediately armed `gk15m-08220050-2197-03`, execution attempt 3, target trade 2.

### Taker transport state

- Shared Gemini prediction+spot market-data socket remains deployed.
- Private Gemini trading WebSocket prewarms after market-data readiness, off the actionable critical path.
- Kalshi exact REST books remain paced/staggered rather than four synchronized fast loops.
- Diagnostic near-miss attribution is deployed but does not modify qualification/order logic.
- Current evidence says valid economics occur in bursts; the right frequency strategy is to keep this live lane continuously scanning while adding safe opportunity breadth rather than weakening the 3¢ floor.

### Maker shadow recovery and target attribution

- Prior maker-shadow aggregate fill counts mixed 1¢/2¢/3¢/5¢ target cohorts and therefore could overstate relevance to the live 3¢ floor.
- `ccfcd702...` / `a631fe60...` / `1285e232...` added per-target fill/profit/stale-hedge attribution and routed the existing Railway maker shadow through that wrapper.
- `acf909256...` replaced four rate-limited Kalshi WebSocket market-data connections with exact Kalshi REST orderbooks plus one shared completed-trades REST poller. Actual trade prints are deduplicated by trade ID; no order transport was added.
- `e7387be3527e71199240293aa9e1fff63d42a5d9` replaced per-asset Gemini public bookTicker connections with one shared Gemini Prediction `depth` RPC socket.
- `4c2904873d36c356b698feb336db60f986fe7199` stopped a single one-sided/empty Gemini depth response from tearing down the entire shared socket. An unusable symbol now simply skips that snapshot; no synthetic bid/ask is inferred.
- Railway deployment `6347c9d6-b705-459e-ba65-f2357eb61770` is `SUCCESS`; 20 startup tests passed and the checked runtime had zero `MAKER_SHADOW_GEMINI_SHARED_RECONNECT` rows.
- First stable 10-20s samples showed BTC/ETH/XRP actively opening/resting simulated quotes and large real Kalshi trade-print counts. BTC reached 24 active simulated quotes and 1,209 public trade prints in the sampled heartbeat.
- Early BTC stale-hedge attribution was **3 at 1¢ and 3 at 2¢**, with **0 stale hedges at >=3¢**; however there were also **0 simulated fills at >=3¢** in the first sampled windows. That is not enough evidence to promote maker live.
- Maker remains strictly **SHADOW** / `INDEX_MISMATCH_RESEARCH_ONLY`; the separate live-maker service remains disarmed.

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

1. Increase **valid executable opportunity breadth**: continuously scan current 15-minute crypto, prove safe maker flow, investigate additional genuinely matched short-duration crypto expiries/venues, manufactured same-Kalshi MVE baskets, direct K↔P game/spread/total, props, live events, NFL/other sports, and later certified sportsbook counterparts.
2. Convert identity joins into **settlement-rule-certified** counterparts. Never promote a ranking-only cheaper leg into execution merely because its price is better.
3. Prove repeated q=1 fills and settlement/reconciliation per lane.
4. Scale quantity only after final displayed depth and cross-venue capital reservation support it: q=5 -> q=10/q=25+ rather than blind size increases.
5. Aggregate multiple independent certified lanes. The target is measured in realized net dollars/day, not theoretical opportunity count.

Crypto taker has now proven repeated q=1 settlement across XRP and BTC. Maker flow still appears structurally promising for frequency, but only >=3¢ target fills with continuously safe hedge/boundary state are relevant to live promotion; 1¢/2¢ simulated activity must not be used to justify arming.

## Realized P&L truth

- Crypto 15m verified REALIZED net P&L: **+$0.1219** (+$0.0888 XRP, +$0.0331 BTC).
- Sports G↔K verified settled REALIZED net P&L: **+$0.1871**, but classified as rule-risk rather than certified guaranteed arbitrage.
- Total verified realized live P&L tracked across those two workstreams: **+$0.3090**.

Do not describe the full $0.3090 as certified guaranteed-arbitrage P&L; the sports portion remains rule-risk.

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
