# ARB Control Plane

Last verified: 2026-08-22 16:44 UTC

This is the durable ARB handoff. Production state wins over chat history. Before changing a workstream, verify current Railway deployment/runtime + GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective.

## State definitions

- **PAPER/SHADOW**: observe/simulate only; no orders.
- **LIVE PROBE**: bounded real-order capability.
- **EXECUTABLE**: exact counterpart/rules + fresh after-fee economics/depth/capital.
- **EXECUTED**: all required legs confirmed filled.
- **REALIZED**: executed trade settled/reconciled and net P&L confirmed.

Never count stale quotes, mismatched settlement rules, shadow P&L, armed state, attempted orders, MVE creation/RFQs, or unconfirmed fills as realized arbitrage profit.

## Critical crypto incident / containment

Gemini/Kalshi 15-minute crypto is **NOT settlement-equivalent and must remain fail-closed**.

Authoritative incident: BTC trade `gk15m-gk15m-08220515-54da-01` filled both venues but Gemini settled YES while Kalshi settled NO, producing `REALIZED_INDEX_MISMATCH` and **-$0.885300**. Kalshi KXBTC15M uses CF Benchmarks BRTI while Gemini Prediction crypto uses a different settlement source; matching strike/duration/price does not prove identical settlement.

Safety response already merged:
- PR #111 added a boot-level settlement-equivalence arm.
- PR #112 moved the lock into shared `live_probe_authorized()` with code-level settlement equivalence hard-false; env flags alone cannot re-enable q1/q5.
- q5 promotion remains blocked.
- Existing >=3c after-fee, >=10bp boundary, 50ms survival/skew, freshness, capital reservation, Gemini-first execution, reconciliation, rollover, and hard-halt gates remain unchanged.

Fresh 16:44 UTC evidence:
- direct Railway runtime access is unavailable in this supervisor session;
- authoritative ledger still shows **no new Gemini/Kalshi crypto submission after 2026-08-22 06:40:03 UTC**;
- previously unresolved ETH trade `gk15m-gk15m-08220615-c225-02` is now fully reconciled: both venue outcomes YES, settlement source `EXACT_PUBLIC_VENUE_ENDPOINTS`, venue outcomes agree, and `TRADE_REALIZED` at 16:08:09 UTC for **+$0.049600**;
- do not use the old bounded-campaign diagnosis or any service failure as a reason to bypass the settlement-equivalence transport kill.

## Crypto bounded-campaign diagnosis / rollover

The prior dormancy root cause was real and has been addressed separately:
- live executor is a bounded campaign;
- campaign end conditions include `SOL_CANARY_V2_ARM_NOT_AFTER_UTC`, `CRYPTO_CANARY_MAX_TRADES` default 5, and `CRYPTO_CANARY_MAX_EXECUTION_ATTEMPTS` default 25;
- PRs #105-#107 added fail-closed diagnosis and supervised continuous rollover;
- PR #109 fixed benign no-claim deadline expiry so a lane merely waiting for an opportunity does not globally halt the campaign;
- never reuse expired arm tokens or campaign IDs;
- rollover requires terminal clean local state, no unresolved attempt claim, no reservations/unsettled capital, successful venue reconciliation, and zero resting orders.

This is no longer the governing crypto blocker. Settlement/threshold equivalence is.

## Current realized P&L truth

Authoritative `arb-trade-ledger` as of 16:44 UTC:
- 2026-08-22 ET day (from 04:00 UTC): `gemini_kalshi_15m_crypto` has **7 realized events, net -$0.569000**.
- The former unresolved ETH trade is included only now because exact public venue settlement reconciled both legs and produced +$0.049600.
- No sports or weather realized event has appeared in the ledger during this ET-day window.

Do not describe prior Gemini/Kalshi crypto wins as guaranteed-arbitrage evidence; proven settlement/index basis risk can dominate the apparent execution lock.

## Crypto research / maker / q5

- `crypto-maker-arb-shadow-v1`: SHADOW / `INDEX_MISMATCH_RESEARCH_ONLY`; live maker disarmed.
- q5: **BLOCKED / DO NOT ARM**.
- PR #116 `Add read-only crypto threshold dominance study` remains research-only; it cannot authorize trading.

## Sports manufactured MVE / same-Kalshi partition

Service `sports-kp-combo-shadow` remains the highest-value safe manufacturing path.

Production branch `feature/kp-combo-synthetic-shadow` includes merged PR #117 at `5f6f64a65ff5dada9a4ea29696c969111b432a5d`. GitHub's Railway deployment check for `sports-kp-combo-shadow` is **SUCCESS**. Stable entrypoint remains supervisor v40; v40 -> v39 -> v38 -> v37, and v37 calls the exact same-market probe before continuing.

v37 scans exact same Kalshi binary markets for `YES + NO = $1`, requires q=1 depth on both sides, applies conservative taker fees, and labels a shadow candidate executable only at **>= $0.03 locked profit**. Same-market identity avoids the cross-venue settlement-equivalence issue.

PR #117 added diagnostic-only `SPORTS_SUPERVISOR_TELEMETRY` carrying scan counts, >=3c candidate count, best locked profit/ticker/all-in/depth. It cannot authorize orders or count as realized P&L.

Fresh 16:44 UTC evidence:
- Railway-linked deployment remains **SUCCESS**;
- authoritative Neon still contains only `trade_events`; `sports_mve_rfq_feedback` has not been created;
- there are still **0 `SPORTS_SUPERVISOR_TELEMETRY` rows** after PR #117 deployment;
- code inspection confirms v40 should reach v37 and v37 should call telemetry after each same-market probe;
- telemetry writer returns False silently if `DATABASE_URL` is absent and swallows database exceptions. With direct Railway variables/logs unavailable, missing/misconfigured ledger connectivity is now a leading observability hypothesis, not a proven root cause.

Do not infer an executable candidate until an authoritative telemetry row or direct runtime evidence proves a cycle and shows >=3c locked after conservative fees with exact q=1 depth.

## Same-Gemini extreme combo live canary

Dedicated one-shot service `gemini-extreme-combo-live-q1` (service id `4399e98f-ce33-4a74-99f2-900f7615beab`) exists on `feature/three-venue-sports-router`, code `3d8f242f5572df8970fbed892e8a5989d94fd362`, restart policy `NEVER`.

Campaign `gcombo-2608221121-a1` targeted combo `GEMI-CMB-260823-8EC8FBCF7988E34C-CMB`, q=1, maker BUY YES $0.90, target lock >=$0.03, TTL 120s. Same-Gemini rule basis is product-settlement combo plus exact component complements; canary pre-hedges exact components before maker combo and flattens/hard-halts on failure/ambiguity.

15:24 UTC armed attempt was correctly `LIVE_GATE_BLOCKED`: both exact hedge outcomes had no executable offer, therefore **0 orders, 0 fills, $0 realized**. Do not reuse that campaign ambiguously. If future exact hedges return with depth/economics, use a fresh unique one-shot campaign.

## Direct sports K↔P

`sports-kp-live-executor` remains read-only certification scan unless exact counterpart settlement rules and >=3c after-fee economics are proven. NFL full-game totals previously failed exceptional-event lifecycle equivalence; identity/line matches alone are insufficient.

## Sports G↔K props

`sports-gk-autonomous-supervisor` remains LIVE RULE-RISK PROBE / not certified guaranteed arb. Historical realized total +$0.1871. Authoritative rule-risk budget remains $4.84/$5.00. Do not reset/increase without explicit user authorization.

## Weather

Reconcile any unresolved historical weather order before new live execution. Preserve exact settlement-rule certification.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms` (fail-closed), `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows while their workstreams are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, `sports-alert-reliability-shadow`, and `gemini-extreme-combo-live-q1`.

Remove only verified dead/test/superseded services after dependency checks.

## Next-run protocol

1. Read `ARB_STATUS.md` and retry direct Railway runtime inspection first.
2. Keep Gemini/Kalshi crypto q1/q5/maker blocked unless exact settlement + threshold equivalence is independently certified.
3. Verify no new crypto submission occurred after 06:40:03 UTC; ETH `gk15m-gk15m-08220615-c225-02` is now reconciled +$0.049600 and should no longer be treated as unresolved.
4. Highest-value sports blocker: inspect `sports-kp-combo-shadow` runtime variables/logs. Specifically verify whether `DATABASE_URL` is present/correct and why PR #117 telemetry has emitted zero rows despite v40 deployment success.
5. Once telemetry/runtime cycle is proven, use same-market best locked profit/candidate count to decide whether a bounded same-Kalshi q1 canary is justified. Preserve >=3c, exact identity, final q1 depth, freshness, capital reservation, one-attempt, hard-halt, and settlement accounting.
6. Recheck same-Gemini combo books only with a fresh unique campaign if both exact hedges are available and economics still preserve >=3c; never reuse `gcombo-2608221121-a1` as a fresh campaign.
7. Continue <=24h sports breadth and exact-rule weather opportunities without weakening certification.
8. Work one blocker through inspect -> fix -> deploy -> verify -> reconcile -> checkpoint before widening scope.

Never store credentials, signing material, API keys, or sensitive balances here.
