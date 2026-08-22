# ARB Control Plane

Last verified: 2026-08-22 01:26 UTC

This is the durable ARB handoff. Production state wins over chat history. Before changing a workstream, verify current Railway deployment/runtime + GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective.

## State definitions

- **PAPER/SHADOW**: observe/simulate only; no orders.
- **LIVE PROBE**: bounded real-order capability.
- **EXECUTABLE**: exact counterpart/rules + fresh after-fee economics/depth/capital.
- **EXECUTED**: all required legs confirmed filled.
- **REALIZED**: executed trade settled/reconciled and net P&L confirmed.

Never count stale quotes, mismatched settlement rules, shadow P&L, armed state, attempted orders, or unconfirmed fills as realized arbitrage profit.

## Current production snapshot

### Crypto 15m BTC/ETH/SOL/XRP — LIVE PROBE / REPEATED REALIZED P&L

Railway service: `crypto-15m-sol-live-canary-10bp-50ms`

Latest verified deployment: `bc7764ce-f58a-4cc2-96f8-305f1c5de4f8`

GitHub branch/head: `feature/sol-15m-live-canary-10bp-50ms` @ `578c987bdcf36d0a4f23222146db48af3785b76d`

Build: **48 tests passed**.

Current transport architecture:
- one **public** Gemini WebSocket carries BTC/ETH/SOL/XRP Prediction `depth` RPC market data plus public spot `bookTicker` basis streams;
- Gemini authenticated handshake capacity is reserved for one persistent private trading WebSocket;
- the private trading socket prewarms after market-data readiness and has a continuous off-critical-path self-healing watchdog;
- a q=1 Gemini Prediction REST `fill-or-kill` fallback exists only when the private WS is unhealthy; only a synchronous proven full fill may proceed to Kalshi; timeout/5xx/malformed/partial/unexpected states are `UNKNOWN` and hard-halt before Kalshi;
- emergency Gemini flatten also has a bounded REST FOK fallback if the private WS is unavailable;
- Kalshi uses exact REST orderbooks, active cadence 180ms; passive next-window double-buffer feeds use 850ms cadence with 50ms activation checks to avoid rollover throttling;
- temporarily one-sided Gemini depth skips only that symbol snapshot and does not tear down the shared socket or invent liquidity.

Latest runtime verification:
- `CRYPTO_GEMINI_PUBLIC_MARKET_DATA_INSTALLED market_data_auth=false ... private_auth_reserved_for_execution=true`;
- public Gemini shared market-data socket connected immediately with transport `PUBLIC_SHARED_WS_DEPTH_RPC_PLUS_SPOT_AUTH_RESERVED_FOR_EXECUTION`;
- BTC/ETH/SOL/XRP all reached `CRYPTO_HOT_READY_BOTH_BOOKS`;
- private authenticated trading WS reached `CRYPTO_GEMINI_TRADING_WS_PREWARM_READY attempts=1 ... continuous_watchdog=true`;
- current fresh bounded campaign `gk15m-08220125-e495` is armed with one child, max 5 completed trades / 25 execution attempts / hard budget <=$5;
- startup reconciliation found no resting orders on either venue;
- no new ACTIONABLE/BOTH_FILLED/REALIZED event was observed after this final deployment as of this checkpoint.

Safety gates remain unchanged:
- q=1;
- >=$0.03 modeled after-fee locked profit;
- >=10bp double-boundary canary requirement;
- 50ms survival delay;
- <=250ms quote freshness;
- <=50ms cross-venue quote skew;
- exact displayed price/depth and exact-payload economics;
- Gemini first, terminal result before Kalshi;
- one unresolved trade at a time;
- ambiguity hard-halts/reconciles;
- settlement required before REALIZED.

Important pre-fix evidence: at ~01:10:52 UTC an ETH q=1 opportunity with **$0.04 modeled locked profit** passed all market/rule/freshness/boundary gates but was safely missed because the old private Gemini WS failed its pre-send health check. Gemini filled 0; Kalshi was not sent. This was the direct motivation for the transport hardening above.

Verified crypto REALIZED P&L:
- XRP q=1: **+$0.0888 net**.
- BTC q=1: **+$0.0331 net**.
- Crypto cumulative: **+$0.1219 net REALIZED**.

Next crypto priority: leave this transport stable and let it trade. Increase opportunity breadth rather than weakening gates. Highest-value research lanes are exact 5-minute counterpart/rule certification and the maker >=3c cohort.

### Crypto maker — SHADOW / LIVE MAKER DISARMED

Railway shadow: `crypto-maker-arb-shadow-v1`, latest verified deployment `6347c9d6-b705-459e-ba65-f2357eb61770`, branch `feature/maker-arb-shadow-v1` @ `4c290487...`.

Shadow now uses one shared public Gemini depth socket plus exact Kalshi REST books and one shared completed-trade poller. Target attribution separates 1c/2c/3c/5c cohorts. Early repaired samples had stale-hedge hits only in 1c/2c; **0 stale hedges but also 0 fills at >=3c** in the first checked >=3c sample. All output remains `INDEX_MISMATCH_RESEARCH_ONLY`, `execution_eligible=false`, `order_entry=false`.

Do not arm maker until >=3c fill evidence is material and continuous settlement/boundary/freshness/hedge-marketability protection is proven while quotes rest.

### Crypto 5-minute expansion — CERTIFICATION TARGET ONLY

Gemini currently exposes BTC 5-minute Prediction contracts. Kalshi's current crypto UI exposes a 5M lane, but exact API series/ticker + settlement equivalence have not yet been certified. Do not infer equivalence from ticker/UI naming. Audit exact expiry, source/index, averaging window, exceptional-event behavior, and outcome agreement before execution. If certified, 5M can materially multiply short-window opportunities without lowering the 3c floor.

### Sports manufactured MVE combo

Service `sports-kp-combo-shadow`: generator active / live-probe capable, but no new verified executable/filled combo at this checkpoint. Preserve >=3c after-fee + final component/RFQ/depth/rule gates. Do not count MVE creation or RFQs as trades.

### Direct sports K↔P

Service `sports-kp-live-executor`: live read-only certification scan; latest known approved execution contracts = 0. Preserve >=3c after-fee, <=50ms skew, <=100ms freshness, persistence, explicit rule fingerprint, and final exact depth. Expand certified breadth instead of weakening gates.

### Sports G↔K props

Service `sports-gk-autonomous-supervisor`: **LIVE RULE-RISK PROBE / REALIZED P&L EXISTS / NOT CERTIFIED GUARANTEED ARB**.

Five historical settled probes total **+$0.1871 net REALIZED**. Authoritative cumulative experimental rule-risk budget remains **$4.84/$5.00**. Do not reset/increase the $5 ceiling without explicit user authorization. Existing families have not proven complete exceptional-event lifecycle equivalence.

### Weather

Service `weather-live-probe`: reconcile any prior unresolved order before new live execution.

## Realized P&L truth

- Crypto 15m: **+$0.1219 net REALIZED**.
- Sports G↔K: **+$0.1871 net REALIZED**, classified rule-risk rather than certified guaranteed arb.
- Total verified live REALIZED tracked: **+$0.3090**.

Do not describe the full $0.3090 as certified guaranteed-arbitrage profit because the sports portion remains rule-risk.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms`, `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows while their workstreams are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, and `sports-alert-reliability-shadow`.

Remove only verified dead/test/superseded services after dependency checks. Never infer deadness from names alone.

## New-chat startup protocol

1. Read `ARB_STATUS.md`.
2. Query the current Railway deployment/runtime for the target service.
3. Query the relevant GitHub branch/head and compare it with Railway.
4. State the exact rung: SHADOW, LIVE PROBE, EXECUTABLE, EXECUTED, or REALIZED.
5. Work one blocker through **inspect -> fix -> deploy -> verify -> reconcile -> checkpoint** before widening scope.

After meaningful changes, update this file with UTC verification time, deployment/commit, armed/disarmed state, execution/fill/settlement evidence, and next blocker. Never store credentials, signing material, API keys, or sensitive balances here.
