# ARB Control Plane

Last verified: 2026-08-22 21:00 UTC

This is the durable ARB handoff. Production state wins over chat history. Before changing a workstream, verify current Railway deployment/runtime + GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective.

## State definitions

- **PAPER/SHADOW**: observe/simulate only; no orders.
- **LIVE PROBE**: bounded real-order capability.
- **EXECUTABLE**: exact counterpart/rules + fresh after-fee economics/depth/capital.
- **EXECUTED**: all required legs confirmed filled.
- **REALIZED**: executed trade settled/reconciled and net P&L confirmed.

Never count stale quotes, mismatched settlement rules, shadow P&L, armed state, attempted orders, MVE creation/RFQs, or unconfirmed fills as realized arbitrage profit.

## Current realized P&L truth

Authoritative ledger evidence last reconciled earlier on 2026-08-22:
- 2026-08-22 ET day `gemini_kalshi_15m_crypto`: 8 realized events, net **-$0.536200**.
- Historical BTC backfill `gk15m-gkr-260819-1705-04`: **+$0.032800 REALIZED** only after exact public venue settlement reconciliation.
- No sports or weather `TRADE_REALIZED` event was present in the ledger at the last authoritative snapshot.

No same-Gemini sports combo order was submitted, filled, executed, or realized during the 20:58-21:00 UTC work below.

## Critical crypto incident / containment

Gemini/Kalshi 15-minute crypto is **NOT settlement-equivalent and must remain fail-closed**.

Authoritative incident: BTC trade `gk15m-gk15m-08220515-54da-01` filled both venues but Gemini settled YES while Kalshi settled NO, producing `REALIZED_INDEX_MISMATCH` and **-$0.885300**. Kalshi KXBTC15M uses CF Benchmarks BRTI while Gemini Prediction crypto uses a different settlement source; matching strike/duration/price does not prove identical settlement.

Safety response already merged:
- PR #111 added a boot-level settlement-equivalence arm.
- PR #112 moved the lock into shared `live_probe_authorized()` with code-level settlement equivalence hard-false; env flags alone cannot re-enable q1/q5.
- q5 promotion remains blocked.
- Existing >=3c after-fee, >=10bp boundary, 50ms survival/skew, freshness, capital reservation, Gemini-first execution, reconciliation, rollover, and hard-halt gates remain unchanged.

The old bounded-campaign dormancy diagnosis was real but is no longer the governing crypto blocker. Settlement/threshold equivalence is.

## Same-Kalshi / sports manufacturing

Service `sports-kp-combo-shadow` remains a high-value safe manufacturing path.

Production branch `feature/kp-combo-synthetic-shadow` includes merged PR #117 at `5f6f64a65ff5dada9a4ea29696c969111b432a5d`. v37 scans exact same Kalshi binary markets for `YES + NO = $1`, requires q=1 depth on both sides, applies conservative taker fees, and labels a shadow candidate executable only at **>= $0.03 locked profit**. Same-market identity avoids cross-venue settlement-equivalence risk.

PR #117 added diagnostic-only `SPORTS_SUPERVISOR_TELEMETRY`. At the last authoritative database check there were still no fresh sports telemetry rows, so telemetry/database connectivity remains an observability blocker until direct runtime evidence proves otherwise.

Do not infer an executable candidate until authoritative telemetry or direct runtime evidence shows >=3c locked after conservative fees with exact q=1 depth.

## Same-Gemini sports combo lane — CURRENT

### Historical live-probe incident

A 16:10 UTC q=1 pre-hedge-first probe exposed an execution-recovery bug: two hedges filled before the combo, the combo did not fill, and the old emergency flattener used unsupported `MARKET+IOC`. Recovery was completed with supported SELL `LIMIT+FOK` exits. Final reconciliation showed zero remaining positions/orders and approximately **-$0.06 realized live-probe loss**. This was not arbitrage profit.

The lane was redesigned combo-first so no hedge is bought until the combo fills. Subsequent combo-first q=1/q=3 probes produced no combo fills and no new hedge churn. Gemini also rejected same-event multi-driver F1 combos and a known `too closely correlated` same-match construction; these classes remain excluded.

### Fractional-fill finding — resting maker is now BLOCKED

The prior monitored resting-q1 design is **superseded and must not be re-armed**.

Live Gemini refdata proves combo instruments can have:
- `quantityMinimum = 1`
- `quantityIncrement = 0.01`

Therefore a submitted q=1 resting combo order is not indivisible and can receive sub-contract fills. With Gemini prediction fees conservatively rounded up to a cent, the 0.01-fill branch cannot preserve the required **>=3c/contract guaranteed floor**. Exact fractional hedge increments do not repair the fee-floor problem.

Fresh catalog/liquidity audit:
- active registered combos: 17;
- dynamically valid unresolved combos: 15;
- current watcher symbol set: 31 combo/component instruments;
- most recent refdata liquidity scan found no liquid whole-increment combo instrument;
- resting combo-maker live mode is therefore **BLOCKED** for this guaranteed-floor lane.

Do not revive campaign `gcombo-2608221328-torclb-rest1` or any other resting combo quote merely because q=1 was requested. q=1 does not imply all-or-none on these combo grids.

### Safe candidate architecture — q=1 FOK only

Branch `feature/gemini-combo-mm-v1` now contains a dynamic same-Gemini FOK path:
- `src/gemini_combo_mm_fok_preflight.py`: read-only active-catalog economics for both exact conjunction covers:
  - `C(YES) + SUM(complements) >= 1`
  - `C(NO) + SUM(selected required outcomes) >= 1`
- `src/gemini_combo_mm_fok_watch.py`: continuous read-only WebSocket watcher; no authenticated order entry.
- `src/gemini_combo_mm_fok_live_v1.py`: bounded q=1 combo-first LIMIT+FOK executor with dynamic canonical-leg certification, settled-PASS removal, variable leg counts, exact q1 depth, <=50ms final receive skew, >=3c conservative after-fee floor, balance/account guards, unexpected-partial hard halt, bounded `LIMIT+FOK` recovery, and post-recovery reconciliation.
- `src/gemini_combo_mm_fok_entry.py`: fail-closed production entrypoint. DISARMED boots are public-only; when genuinely armed it replaces per-symbol active-order REST scans with one unfiltered `/v1/prediction-markets/orders/active` call filtered locally. A full 100-order page fails closed.

Why the entrypoint was added: an earlier disarmed deployment crashed with `Gemini time-based nonce rate exceeded` because it queried active orders separately for every combo/component symbol. This was an observability/preflight bug, not a trading loss. It is now fixed without weakening active-order checks.

Validation at ~20:59 UTC:
- Railway test service `gemini-combo-mm-test-v1`: **15 passed**.
- Disarmed executor service `gemini-combo-mm-canonicalize-v1` (service `efe9ef29-18b2-4908-9953-11d7b8ca3a67`) booted successfully on commit `79ff8f5823354bf8a803b7ff4be9d73e41b8d902` with:
  - `armed=false`
  - active catalog HTTP 200 / 17 registered combos
  - 15 valid dynamic candidates
  - `private_preflight=SKIPPED_DISARMED`
  - `order_entry=false`
  - `realized=false`
  - restart policy `NEVER`
- Read-only watcher service `gemini-combo-mm-fok-preflight-v1` (service `71b403d8-77dc-49ab-837a-ea628026f084`) refreshed the same 17-combo catalog, 15 valid combos / 31 symbols, and reported **0 active executable signals** on its fresh heartbeat. `order_entry=false`.

Current classification:
- resting combo maker: **BLOCKED**;
- WebSocket FOK watcher: **SHADOW / READ-ONLY ACTIVE**;
- q=1 FOK executor: **LIVE-CAPABLE CODE, DISARMED**;
- current executable signal count: **0**;
- new orders: **0**;
- new fills: **0**;
- new executed trades: **0**;
- new realized sports arb P&L: **$0**.

Do not arm the FOK executor just to test it when there is no executable signal. A future live promotion still requires a fresh >=3c q1 signal, exact canonical certification, final <=50ms book skew/depth, clean account state, sufficient capital for all legs, and the existing bounded recovery path. Separate FOK orders are not atomic across legs; that residual execution risk must remain explicit.

## Direct sports K↔P

`sports-kp-live-executor` remains read-only certification scan unless exact counterpart settlement rules and >=3c after-fee economics are proven. NFL full-game totals previously failed exceptional-event lifecycle equivalence; identity/line matches alone are insufficient.

## Sports G↔K props

`sports-gk-autonomous-supervisor` remains LIVE RULE-RISK PROBE / not certified guaranteed arb. Historical realized total +$0.1871. Authoritative rule-risk budget remains **$4.84/$5.00**. Do not reset/increase without explicit user authorization.

## Weather

Reconcile any unresolved historical weather order before new live execution. Preserve exact settlement-rule certification.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms` (fail-closed), `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows/workstream services while their lanes are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, `sports-alert-reliability-shadow`, `gemini-extreme-combo-live-q1`, `gemini-combo-mm-fok-preflight-v1`, `gemini-combo-mm-test-v1`, and the disarmed FOK executor service currently named `gemini-combo-mm-canonicalize-v1`.

Remove only verified dead/test/superseded services after dependency checks.

## Next-run protocol

1. Read `ARB_STATUS.md`, then inspect current Railway/GitHub production state before mutation.
2. Keep Gemini/Kalshi crypto q1/q5/maker blocked unless exact settlement + threshold equivalence is independently certified.
3. For same-Gemini sports combos, keep **resting maker mode blocked**. Never infer indivisibility from q=1.
4. Verify the read-only FOK watcher is receiving all current symbol snapshots and whether `active_executable_signals` is still 0.
5. If a >=3c q1 FOK signal appears, re-run exact canonical metadata, final <=50ms q1 depth/economics, positions, active orders, and capital reservation immediately before any bounded live promotion.
6. Any future live FOK campaign must use a fresh unique campaign + matching arm token + short boot expiry, restart `NEVER`, and cleared arm variables after boot. Never reuse an expired campaign.
7. Classify a fully filled cover as **FILLED_NOT_SETTLED** until every component settles and the authoritative ledger reconciles net P&L.
8. Keep candidate filtering for same-event and known highly-correlated Gemini combinations fail-closed.
9. Highest-value same-Kalshi blocker remains proving `sports-kp-combo-shadow` runtime telemetry/database connectivity, then using authoritative same-market >=3c evidence for bounded live promotion.
10. Continue sports breadth and exact-rule weather opportunities without weakening certification. Work one blocker through inspect -> fix -> deploy -> verify -> reconcile -> checkpoint before widening scope.

Never store credentials, signing material, API keys, or sensitive balances here.
