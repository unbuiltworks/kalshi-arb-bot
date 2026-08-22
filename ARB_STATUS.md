# ARB Control Plane

Last verified: 2026-08-22 05:36 UTC

This is the durable ARB handoff. Production state wins over chat history. Before changing a workstream, verify current Railway deployment/runtime + GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective.

## State definitions

- **PAPER/SHADOW**: observe/simulate only; no orders.
- **LIVE PROBE**: bounded real-order capability.
- **EXECUTABLE**: exact counterpart/rules + fresh after-fee economics/depth/capital.
- **EXECUTED**: all required legs confirmed filled.
- **REALIZED**: executed trade settled/reconciled and net P&L confirmed.

Never count stale quotes, mismatched settlement rules, shadow P&L, armed state, attempted orders, MVE creation/RFQs, or unconfirmed fills as realized arbitrage profit.

## Critical crypto incident — 2026-08-22 05:30 UTC

The Gemini/Kalshi 15-minute crypto taker lane is **NOT settlement-equivalent and must remain fail-closed**.

Authoritative ledger incident:
- trade `gk15m-gk15m-08220515-54da-01`;
- asset BTC, q=1;
- both Gemini and Kalshi legs filled;
- actual deployed capital `$0.885300`;
- settlement: Gemini outcome `yes`, Kalshi outcome `no`;
- `venue_outcomes_agree=false`;
- event recorded as `REALIZED_INDEX_MISMATCH`;
- realized net P&L **-$0.885300**.

External rule/source confirmation:
- Kalshi KXBTC15M uses CF Benchmarks BRTI and settles from the simple average of the final 60 one-second RTI observations before expiry.
- Gemini Prediction crypto events expose `sourceDetails` / deprecated `source`; Gemini documentation examples identify the crypto source as Kaiko `GRR-KAIKO_BTCUSD_60S`.
- Therefore Gemini/Kalshi price agreement, matching duration, matching strike direction, and the existing boundary gates do **not** prove identical settlement outcomes.

Immediate safety response:
- PR #111 `Fail closed crypto until settlement equivalence is certified` passed CI and was merged into `feature/sol-15m-live-canary-10bp-50ms` at merge commit `be86387b6159a93d06aa3383a784826b2fdadd58`.
- q=1 postincident boot now requires explicit `CRYPTO_GK_SETTLEMENT_EQUIVALENCE_CERTIFIED=YES`; default is fail-closed.
- Do **not** set that variable until exact Gemini and Kalshi source/index/averaging/rounding/exception rules are independently proven equivalent for the exact traded family.
- q5 promotion is blocked. Do not arm q5 while q1 settlement equivalence is unproven.
- Existing q=1 >=3c after-fee, >=10bp boundary, 50ms survival/skew, freshness, capital reservation, Gemini-first execution, reconciliation, rollover, and hard-halt gates remain unchanged.
- Direct Railway MCP runtime access is currently unavailable in this supervisor session. GitHub received Railway deployment checks for the merged fail-closed commit; latest observed state at this checkpoint was `pending` for `crypto-15m-sol-live-canary-10bp-50ms`. Re-check deployment status first on the next run.

## Crypto bounded-campaign diagnosis / rollover

The prior dormancy root cause was real and has been addressed separately:
- the live executor is a bounded campaign;
- campaign end conditions include `SOL_CANARY_V2_ARM_NOT_AFTER_UTC`, `CRYPTO_CANARY_MAX_TRADES` (default 5), and `CRYPTO_CANARY_MAX_EXECUTION_ATTEMPTS` (default 25);
- PRs #105-#107 added fail-closed diagnosis and supervised continuous rollover;
- PR #109 fixed benign no-claim deadline expiry so a lane merely waiting for an opportunity does not globally halt the campaign;
- do not reuse expired arm tokens or campaign IDs;
- continuous rollover still requires terminal clean local state, no unresolved attempt claim, no reservations/unsettled capital, successful venue reconciliation, and zero resting orders.

This rollover work is no longer the highest-priority crypto blocker. Settlement equivalence is.

## Current realized P&L truth

Authoritative `arb-trade-ledger` as of this checkpoint:
- 2026-08-22 ET day (from 04:00 UTC): crypto 4 realized events, net **-$0.697500**.
- The ET-day crypto events include +$0.0450 BTC, +$0.0445 BTC, +$0.0983 XRP, then the -$0.8853 BTC index-mismatch loss.
- All-time `gemini_kalshi_15m_crypto`: **-$0.637700** across 17 realized events.
- Separate `crypto-15m-gemini-kalshi-canary`: **+$0.044500** across 1 realized event.
- Sports G↔K rule-risk realized: **+$0.187100** across 5 events.
- Total authoritative realized net across all tracked strategies: **-$0.406100**.

Do not describe prior Gemini/Kalshi crypto wins as guaranteed-arbitrage evidence; the 05:30 UTC mismatch proves the settlement basis can diverge.

## Crypto maker

`crypto-maker-arb-shadow-v1` remains SHADOW / `INDEX_MISMATCH_RESEARCH_ONLY`, live maker disarmed. This classification was directionally correct and must remain. Do not promote maker Gemini↔Kalshi crypto until settlement-source equivalence is proven or the strategy is explicitly reclassified as non-arbitrage basis risk with a separate approved risk budget.

## Crypto q5 scaling

PR #110 refreshed the q5 package onto the repaired q1 head and was merged before the index-mismatch incident. The package was additive and retained the >=3c/contract, >=10bp boundary, 50ms survival/skew, exact quantity/depth, <=$5 first-canary capital, Gemini-first, flatten, and settlement controls.

**Current status: BLOCKED / DO NOT ARM.** The q1 settlement equivalence failure invalidates promotion to q5 until exact settlement rules are certified or the pair is removed from guaranteed-arb execution.

## Sports manufactured MVE combo

Service `sports-kp-combo-shadow` remains the highest-value live-capable sports manufacturing path. Preserve the >=3c after-fee lock, rule certification, exact final depth, freshness/skew, persistence, and one-attempt limits. MVE creation/RFQ is not a trade. Continue valid same-day/<=24h breadth and faster executable candidate handoff without weakening settlement certification.

## Direct sports K↔P

Service `sports-kp-live-executor` remains read-only certification scan / not executable unless an exact counterpart has certified settlement rules and >=3c after-fee economics. NFL full-game totals previously failed exceptional-event lifecycle equivalence; do not promote line/identity matches alone.

## Sports G↔K props

Service `sports-gk-autonomous-supervisor` remains LIVE RULE-RISK PROBE / not certified guaranteed arb. Historical realized total is +$0.1871. Authoritative rule-risk budget remains $4.84/$5.00. Do not reset or increase without explicit user authorization.

## Weather

Reconcile any unresolved historical weather order before new live execution. Preserve exact settlement-rule certification before trading.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms` (currently fail-closed pending settlement certification), `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows while their workstreams are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, and `sports-alert-reliability-shadow`.

Remove only verified dead/test/superseded services after dependency checks. Never infer deadness from names alone.

## New-chat / next-run protocol

1. Read `ARB_STATUS.md`.
2. Re-check GitHub Railway deployment status for merge commit `be86387b6159a93d06aa3383a784826b2fdadd58`; if direct Railway runtime access is restored, inspect logs/config/state as well.
3. Verify no additional Gemini/Kalshi crypto order/fill occurred after the 05:30 UTC mismatch. If one did, reconcile it before any other action.
4. Keep Gemini/Kalshi q1 and q5 order entry blocked unless exact settlement equivalence is certified.
5. Audit exact Gemini `sourceDetails` for BTC/ETH/SOL/XRP and exact Kalshi series rules; expect non-equivalence unless proven otherwise.
6. Shift highest-value guaranteed-arb work toward same-source/same-rule counterparts, sports certified synthetics, and weather exact-rule opportunities rather than treating cross-index crypto as riskless.
7. Work one blocker through **inspect -> fix -> deploy -> verify -> reconcile -> checkpoint** before widening scope.

After meaningful changes, update this file with UTC verification time, deployment/commit, armed/disarmed state, execution/fill/settlement evidence, and next blocker. Never store credentials, signing material, API keys, or sensitive balances here.
