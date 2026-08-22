# ARB Control Plane

Last verified: 2026-08-22 14:45 UTC

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
- PR #111 added a boot-level settlement-equivalence arm.
- PR #112 moved the incident lock into the shared `live_probe_authorized()` transport guard with code-level settlement equivalence hard-false; environment flags alone cannot re-enable q1/q5.
- Do **not** re-enable until exact source/index/threshold/averaging/rounding/exception rules are independently certified for the exact traded family.
- q5 promotion remains blocked.
- Existing q=1 >=3c after-fee, >=10bp boundary, 50ms survival/skew, freshness, capital reservation, Gemini-first execution, reconciliation, rollover, and hard-halt gates remain unchanged.

Fresh 14:45 UTC evidence:
- direct Railway MCP runtime access is still unavailable in the supervisor session;
- GitHub Railway status on current crypto branch base `a4d458a744baa4c8488ef9ae3b079fdec50d6683` shows `crypto-15m-sol-live-canary-10bp-50ms` **failure**;
- `crypto-live-balance-reconcile-readonly` and `crypto-rollover-optimizer-test` both report **success** on that same commit;
- authoritative ledger has **no new crypto submission after 2026-08-22 06:40:03 UTC**.

The crypto service failure is not a reason to bypass the transport kill. Treat it as contained until runtime access returns and the exact failure can be inspected safely.

## Crypto bounded-campaign diagnosis / rollover

The prior dormancy root cause was real and has been addressed separately:
- the live executor is a bounded campaign;
- campaign end conditions include `SOL_CANARY_V2_ARM_NOT_AFTER_UTC`, `CRYPTO_CANARY_MAX_TRADES` (default 5), and `CRYPTO_CANARY_MAX_EXECUTION_ATTEMPTS` (default 25);
- PRs #105-#107 added fail-closed diagnosis and supervised continuous rollover;
- PR #109 fixed benign no-claim deadline expiry so a lane merely waiting for an opportunity does not globally halt the campaign;
- do not reuse expired arm tokens or campaign IDs;
- continuous rollover still requires terminal clean local state, no unresolved attempt claim, no reservations/unsettled capital, successful venue reconciliation, and zero resting orders.

This rollover work is no longer the highest-priority crypto blocker. Settlement/threshold equivalence is.

## Current realized P&L truth

Authoritative `arb-trade-ledger` as of 14:45 UTC:
- 2026-08-22 ET day (from 04:00 UTC): `gemini_kalshi_15m_crypto` has **6 realized events, net -$0.618600**.
- These include small BTC/XRP/SOL wins offset by the -$0.885300 BTC index-mismatch loss.
- Trade `gk15m-gk15m-08220615-c225-02` (ETH) remains **UNRESOLVED**: two FILL rows + `ORDER_SUBMITTED/BOTH_FILLED` at 06:40:03 UTC, but no settlement/`TRADE_REALIZED` row. Exclude it from P&L.
- No sports or weather realized event has appeared in the ledger during this ET-day window.

Do not describe prior Gemini/Kalshi crypto wins as guaranteed-arbitrage evidence; the settlement and threshold mismatch proves basis risk can dominate the apparent execution lock.

## Crypto research / maker / q5

- `crypto-maker-arb-shadow-v1` remains SHADOW / `INDEX_MISMATCH_RESEARCH_ONLY`; live maker disarmed.
- q5 remains **BLOCKED / DO NOT ARM**.
- PR #116 `Add read-only crypto threshold dominance study` is open and mergeable on `feature/crypto-settlement-basis-v2`; it is public-data-only and does not authorize trading. It specifically studies threshold gaps and orientation safety in addition to settlement-index basis.

## Sports manufactured MVE combo

Service `sports-kp-combo-shadow` remains the highest-value safe sports manufacturing path.

Fresh 14:45 UTC evidence:
- active branch `feature/kp-combo-synthetic-shadow` is at PR #104 head `9a1169cba7360b22e6b835e575d18f4b8bbc1254`;
- GitHub Railway status for `sports-kp-combo-shadow` is **success**;
- the stable Railway entrypoint is now supervisor **v40**;
- v40 is read-only and adds signed-auth probing against the Polymarket exchange/RFQ host, then runs v39-v31;
- v37 already scans exact same-Kalshi-market `YES + NO = $1` partitions and labels a shadow candidate executable only when q=1 YES ask + NO ask + conservative taker fees leaves **>= $0.03 locked profit** with depth on both sides;
- because both legs are the exact same Kalshi binary market, this path has no cross-venue settlement-equivalence problem;
- authoritative Neon currently contains only the `trade_events` table and there are still **0 sports supervisor telemetry rows after 11:30 UTC**, so a post-deploy manufacturing/same-market cycle is not independently proven from the ledger.

Preserve the >=3c after-fee lock, exact same-market/rule identity, exact final depth, freshness/skew where applicable, persistence, capital reservation, one-attempt, flatten/hard-halt, and settlement accounting. Shadow scan output is not a trade.

### Same-market observability milestone (not yet promoted)

Feature branch `feature/sports-same-market-ledger-telemetry` now adds best-effort non-P&L ledger telemetry to the existing v37 same-market scan:
- commit `a6bd1ba7de18467ab85892c0c3606ceb638bdd30`: telemetry implementation;
- commit `20637a61f1b4df907951c8919c540a51fbb81c4c`: isolation tests;
- telemetry records scan counts plus best locked profit/ticker/all-in cost/depth and cannot authorize orders or count as realized P&L;
- telemetry failures are swallowed and cannot change scanning/execution behavior.

GitHub PR creation for this branch was blocked by the action safety layer in the 14:45 UTC supervisor run. Do not bypass that protection by force-moving the production branch. Resume promotion only through a normal reviewed/green path.

## Direct sports K↔P

Service `sports-kp-live-executor` remains read-only certification scan / not executable unless an exact counterpart has certified settlement rules and >=3c after-fee economics. NFL full-game totals previously failed exceptional-event lifecycle equivalence; do not promote line/identity matches alone.

## Sports G↔K props

Service `sports-gk-autonomous-supervisor` remains LIVE RULE-RISK PROBE / not certified guaranteed arb. Historical realized total is +$0.1871. Authoritative rule-risk budget remains $4.84/$5.00. Do not reset or increase without explicit user authorization.

## Weather

Reconcile any unresolved historical weather order before new live execution. Preserve exact settlement-rule certification before trading.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms` (currently fail-closed), `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows while their workstreams are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, and `sports-alert-reliability-shadow`.

Remove only verified dead/test/superseded services after dependency checks. Never infer deadness from names alone.

## New-chat / next-run protocol

1. Read `ARB_STATUS.md`.
2. Retry direct Railway runtime access. If restored, inspect crypto failure state but **do not re-arm** around the settlement/threshold transport kill.
3. Reconcile unresolved ETH `gk15m-gk15m-08220615-c225-02` before counting any result.
4. Verify no new Gemini/Kalshi crypto order/fill appeared after 06:40:03 UTC.
5. Keep Gemini/Kalshi q1/q5/maker order entry blocked unless exact settlement + threshold equivalence is certified.
6. Highest-value safe path: prove the current sports v40 cycle is running and surface the v37 same-market YES+NO best locked profit. If the telemetry feature can be promoted normally, use it; do not bypass GitHub safety controls.
7. If an actual same-market candidate shows >=3c locked after conservative fees with exact q=1 depth and fresh state, then build/validate a bounded same-Kalshi q=1 execution canary with settlement reconciliation before any live arm.
8. Continue same-day/<=24h sports MVE/RFQ breadth and weather exact-rule opportunities without weakening certification.
9. Work one blocker through **inspect -> fix -> deploy -> verify -> reconcile -> checkpoint** before widening scope.

After meaningful changes, update this file with UTC verification time, deployment/commit, armed/disarmed state, execution/fill/settlement evidence, and next blocker. Never store credentials, signing material, API keys, or sensitive balances here.
