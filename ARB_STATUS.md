# ARB Control Plane

Last verified: 2026-08-23 02:05 UTC

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

No new Gemini/Kalshi basis-risk order, fill, executed trade, or realized trade was present in the evening campaigns through 02:05 UTC. The prior evening campaign `basis-0822-rest-02` expired with zero order submissions and zero realized trades; its successor dynamic pilot had zero durable claims, zero `ORDER_SUBMITTED`, and zero `TRADE_REALIZED` at the last live check.

No same-Gemini sports combo order was submitted, filled, executed, or realized during the 20:58-21:00 UTC work below.

## Critical crypto incident / containment

Gemini/Kalshi 15-minute crypto is **NOT settlement-equivalent and must remain fail-closed as guaranteed arbitrage**.

Authoritative incident: BTC trade `gk15m-gk15m-08220515-54da-01` filled both venues but Gemini settled YES while Kalshi settled NO, producing `REALIZED_INDEX_MISMATCH` and **-$0.885300**. Kalshi KXBTC15M uses CF Benchmarks BRTI while Gemini Prediction crypto uses a different settlement source; matching strike/duration/price does not prove identical settlement.

Safety response already merged:
- PR #111 added a boot-level settlement-equivalence arm.
- PR #112 moved the lock into shared `live_probe_authorized()` with code-level settlement equivalence hard-false; env flags alone cannot re-enable guaranteed-arb q1/q5.
- q5 promotion remains blocked.
- Existing quote freshness, persistence, capital reservation, execution reconciliation, rollover, and hard-halt gates remain required.

The old bounded-campaign dormancy diagnosis was real but is no longer the governing guaranteed-arb crypto blocker. Settlement equivalence is.

## Gemini/Kalshi 15m basis-risk q1 research — CURRENT

This is a **separate LIVE BASIS-RISK RESEARCH lane**, not guaranteed arbitrage. Do not use it to bypass the shared settlement-equivalence hard lock or promote q5.

Service `crypto-basis-risk-q1-canary` (`9f6e8d88-868a-4a38-9758-e74fceb71ed6`) runs branch `feature/crypto-basis-risk-q1-canary`, restart `NEVER`, via `python -m src.gemini_kalshi_basis_risk_q1_campaign_v2`.

### Kalshi 429 / market-data repair

Authenticated Kalshi WebSocket handshakes continued returning HTTP 429 even after the account API tier was upgraded. The basis child uses a process-local exact authenticated REST transport instead of opening another Kalshi WS:
- exact `GET /trade-api/v2/markets/{ticker}/orderbook?depth=1`;
- default 50ms per-ticker polling, max eight tickers;
- quote timestamp is **HTTP request start**, so network RTT can only make a quote older;
- one-sided books preserve the quoted side and assign zero depth to the missing side; no liquidity is invented;
- REST 429 => no quote + backoff; stale/missing data fails closed;
- downstream <=100ms freshness, <=50ms skew, 50ms persistence, q=1 exact depth and fee gates remain required.

Production validation before the dynamic pilot:
- targeted transport tests: **6/6 passed**;
- read-only production smoke: **20/20 exact books HTTP 200 + parsed**;
- **19/20 <=100ms** using request-start timestamp; warm RTT roughly 18-36ms; one cold-start request was 100.49ms and would be rejected by the existing freshness gate;
- repaired scanner processed hundreds/thousands of live evaluations without Kalshi reconnect failure.

Occasional REST 429 bursts still occur under aggregate account traffic, including in the current dynamic campaign. They fail closed and recover. Do not treat 429/missing-book cycles as executable quotes. If they become the dominant opportunity-loss source, smooth/stagger request bursts rather than relaxing freshness.

### Historical fixed-10bp campaign — SUPERSEDED

Campaign `basis-0822-rest-02` used q=1 / <=$1 / one trade max / $5 hard budget / >=3c quote edge / >=10bp protective threshold separation / <=50ms skew+persistence / <=100ms freshness / exact q1 depth / durable reservation / one unresolved trade max.

It found real quote candidates but submitted **0 orders** and realized **0 trades** before its arm deadline. The fixed >=10bp threshold-separation gate was materially above current Gemini-vs-Kalshi threshold geometry:
- ETH example: 7c quote lock, ~2.218bp gap, wrong protective orientation -> correctly blocked.
- BTC examples: repeated 3.0-4.6c quote locks, ~0.688bp gap, wrong protective orientation -> correctly blocked.
- XRP example: 3c quote lock, ~2.093bp gap, **correct protective orientation**, blocked solely because <10bp; the official settlements later resolved NO/NO on both venues.

Read-only settlement-basis profile on 236 matched settled windows / 232 complete transitions found:
- maximum observed absolute threshold gap by asset: BTC **1.519bp**, ETH **4.166bp**, SOL **4.514bp**, XRP **6.213bp**;
- current sample had **0 >=10bp transitions**;
- 131 transitions had >=1bp gaps, 65 had >=2bp, 24 had >=3bp, and two XRP transitions were in 5-10bp;
- **0 observed threshold-safe-direction dominance breaches** across the 232 complete transitions;
- one SOL venue-outcome disagreement occurred in the 1-2bp bucket, but it was not adverse to the threshold-safe orientation.

These broad historical results alone were not sufficient to lower the live gate. Quote-conditioned evidence was required next.

### Quote-conditioned exact-candidate reconciliation — COMPLETE

The old exact-revalidation shadow had five distinct Aug. 18 BTC windows where a real q=1 quote candidate survived the 50ms exact-price/depth revalidation. A new read-only reconciler direct-fetched the exact official Gemini historical event and Kalshi market for each recorded pair and resolved **5/5**.

Read-only reconciliation deployment: `crypto-threshold-dominance-oneoff` deployment `6ab4f4ae-2e3c-4e11-a416-c53eb65bbdaa`; final reconciliation code commit `1c9b30b43c54470293c2b6ca6584c2afc5978df7` on `feature/crypto-settlement-basis-v2`.

Results:
- 18:15 BTC: 4c observed lock, 1.5952bp threshold gap, candidate orientation was **opposite** the protective direction, final YES/YES.
- 18:45 BTC: **3c observed lock**, 0.35356bp gap, candidate **matched** protective direction, final NO/NO.
- 19:00 BTC: 7c observed lock, 1.40603bp gap, candidate orientation was **opposite** protective direction, final YES/YES.
- 19:15 BTC: **7c observed lock**, 0.83417bp gap, candidate **matched** protective direction, final NO/NO.
- 20:00 BTC: **10c observed lock**, 0.18409bp gap, candidate **matched** protective direction, final NO/NO.

Quote-conditioned summary:
- exact candidates resolved: **5/5**;
- protective-direction candidates: **3**;
- adverse double-losses among those 3: **0**;
- favorable double-wins: **0**;
- normal one-winner settlements: **3**;
- combined observed quote locks on the 3 protective candidates: **20c**.

These were historical quote observations, **not fills and not realized P&L**. The sample is small, so it supports only a highly bounded pilot, not a general profitability claim.

### Dynamic q=1 first-probe pilot — LIVE NOW

User approved continuing the evidence-backed basis-risk path. Instead of immediately using the broad shadow threshold near 3c, the first dynamic live pilot was intentionally made more conservative on economics.

Current campaign: **`basis-0822-dyn-01`**, arm deadline **2026-08-23 04:30 UTC (12:30 AM ET)**.

Current live policy:
- q=1;
- max one trade;
- first-tranche capital <= **$1**;
- approved hard research-loss budget remains **$5**; not increased/reset;
- candidate direction must equal the threshold-dominance protective orientation;
- venue threshold separation >= **0.5bp**;
- modeled locked quote profit >= **$0.07**;
- <=50ms entry/current quote skew;
- 50ms exact-price/depth persistence;
- <=100ms quote freshness;
- exact q1 displayed depth;
- durable Postgres capital reservation before order submission;
- one unresolved trade maximum;
- existing same-side/distance-to-boundary safety remains: spot must remain at least **10bp away from both settlement thresholds** during the positive episode;
- shared guaranteed-arbitrage settlement-equivalence lock remains unchanged;
- `guaranteed_arb=false`;
- q5 remains blocked.

Important terminology: the old runtime log `basis_entry_min=2bp` is a public-spot proxy-margin research check, **not** the Gemini-vs-Kalshi threshold-separation gate. The 10bp double-boundary gate is a separate near-strike safety requirement and remains deliberately stricter than that proxy check. Neither means the dynamic venue-threshold gap reverted to 10bp.

Dynamic policy code/deploy chain:
- `src/gemini_kalshi_basis_risk_dynamic_policy.py` commit `fdce459b338805fdb631475be99816de7781ca5e`;
- `src/gemini_kalshi_basis_risk_q1_child_v4.py` commit `9962119b2a050c09c9fe7c71d4ee5796f9a336c7`;
- campaign launches v4 child and retains v2 durable controls + v3 gate telemetry;
- parent process-local 0.5bp policy fix commit **`da3658ccb3cc808366cc49e3e95f4945e2ba7fd1`**;
- current Railway deployment **`2c3fd92c-7c39-41c2-b8bf-2e041064b2d0`**.

Verified current boot:
- `unresolved_prior_trades=0`;
- fresh persistent arm `basis-0822-dyn-01-01` created;
- dynamic child logs `min_gap_bps=0.5`, `min_locked_profit=0.07`, q=1, capital limit $1, approved budget $5, `guaranteed_arb=false`;
- Gemini private REST/auth WS and Kalshi production auth preflights passed;
- both venues had >=$1 available for the probe;
- exact BTC/ETH/SOL/XRP books are scanning;
- at the latest live check, current window summaries had **0 positive/threshold evaluations**, **0 durable capital claim**, **0 ORDER_SUBMITTED**, and **0 TRADE_REALIZED**.

Do not call the current campaign arbitrage. If it trades, classify it as a bounded **LIVE BASIS-RISK PROBE**. Even a dual fill is **FILLED_NOT_SETTLED** until both official venue outcomes settle and authoritative net P&L is reconciled.

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

Branch `feature/gemini-combo-mm-v1` contains a dynamic same-Gemini FOK path:
- `src/gemini_combo_mm_fok_preflight.py`: read-only active-catalog economics for both exact conjunction covers.
- `src/gemini_combo_mm_fok_watch.py`: continuous read-only WebSocket watcher; no authenticated order entry.
- `src/gemini_combo_mm_fok_live_v1.py`: bounded q=1 combo-first LIMIT+FOK executor with dynamic canonical-leg certification, settled-PASS removal, variable leg counts, exact q1 depth, <=50ms final receive skew, >=3c conservative after-fee floor, balance/account guards, unexpected-partial hard halt, bounded `LIMIT+FOK` recovery, and post-recovery reconciliation.
- `src/gemini_combo_mm_fok_entry.py`: fail-closed production entrypoint. DISARMED boots are public-only; when genuinely armed it replaces per-symbol active-order REST scans with one unfiltered `/v1/prediction-markets/orders/active` call filtered locally. A full 100-order page fails closed.

Why the entrypoint was added: an earlier disarmed deployment crashed with `Gemini time-based nonce rate exceeded` because it queried active orders separately for every combo/component symbol. This was an observability/preflight bug, not a trading loss. It is now fixed without weakening active-order checks.

Validation at ~20:59 UTC:
- Railway test service `gemini-combo-mm-test-v1`: **15 passed**.
- Disarmed executor service `gemini-combo-mm-canonicalize-v1` (`efe9ef29-18b2-4908-9953-11d7b8ca3a67`) booted successfully on commit `79ff8f5823354bf8a803b7ff4be9d73e41b8d902` with `armed=false`, 17 registered combos, 15 valid dynamic candidates, private preflight skipped, `order_entry=false`, `realized=false`, restart `NEVER`.
- Read-only watcher `gemini-combo-mm-fok-preflight-v1` (`71b403d8-77dc-49ab-837a-ea628026f084`) refreshed the same 17-combo catalog / 15 valid combos / 31 symbols and reported **0 active executable signals** on its fresh heartbeat.

Current classification:
- resting combo maker: **BLOCKED**;
- WebSocket FOK watcher: **SHADOW / READ-ONLY ACTIVE**;
- q=1 FOK executor: **LIVE-CAPABLE CODE, DISARMED**;
- current executable signal count at last check: **0**;
- new orders: **0**;
- new fills: **0**;
- new executed trades: **0**;
- new realized sports arb P&L: **$0**.

Do not arm the FOK executor just to test it when there is no executable signal. A future live promotion still requires a fresh >=3c q1 signal, exact canonical certification, final <=50ms q1 depth/economics, clean account state, sufficient capital for all legs, and the existing bounded recovery path. Separate FOK orders are not atomic across legs; that residual execution risk must remain explicit.

## Direct sports K↔P

`sports-kp-live-executor` remains read-only certification scan unless exact counterpart settlement rules and >=3c after-fee economics are proven. NFL full-game totals previously failed exceptional-event lifecycle equivalence; identity/line matches alone are insufficient.

## Sports G↔K props

`sports-gk-autonomous-supervisor` remains LIVE RULE-RISK PROBE / not certified guaranteed arb. Historical realized total +$0.1871. Authoritative rule-risk budget remains **$4.84/$5.00**. Do not reset/increase without explicit user authorization.

## Weather

Reconcile any unresolved historical weather order before new live execution. Preserve exact settlement-rule certification.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms` (fail-closed), `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows/workstream services while their lanes are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, `sports-alert-reliability-shadow`, `gemini-extreme-combo-live-q1`, `gemini-combo-mm-fok-preflight-v1`, `gemini-combo-mm-test-v1`, the disarmed FOK executor service currently named `gemini-combo-mm-canonicalize-v1`, and the active `crypto-basis-risk-q1-canary` while the bounded pilot is running.

Remove only verified dead/test/superseded services after dependency checks.

## Next-run protocol

1. Read `ARB_STATUS.md`, then inspect current Railway/GitHub production state before mutation.
2. Keep guaranteed-arb Gemini/Kalshi crypto q1/q5/maker blocked unless exact settlement + threshold equivalence is independently certified. Treat `crypto-basis-risk-q1-canary` separately as bounded basis-risk research only.
3. First inspect campaign **`basis-0822-dyn-01`** and authoritative basis-risk ledger. Do not create another campaign while it has an unresolved claim/trade. Current arm deadline is 04:30 UTC.
4. The explicitly bounded first-probe policy is **protective orientation + >=0.5bp threshold separation + >=7c modeled locked quote profit**, with q=1/<=1 USD/one trade max/$5 hard research budget and all existing 50ms persistence/skew, <=100ms freshness, exact-depth, 10bp double-boundary and durable reservation gates. Do not loosen it further from chat history alone.
5. If the dynamic pilot submits/fills, classify it as LIVE BASIS-RISK PROBE / FILLED_NOT_SETTLED until official Gemini + Kalshi settlements reconcile. Only then write authoritative REALIZED P&L.
6. If REST 429 bursts materially suppress qualifying evaluations, stagger/smooth exact-book requests rather than weakening freshness or reintroducing an unauthorized WebSocket path.
7. For same-Gemini sports combos, keep **resting maker mode blocked**. Never infer indivisibility from q=1.
8. Verify the read-only FOK watcher is receiving all current symbol snapshots and whether `active_executable_signals` is still 0.
9. If a >=3c q1 FOK signal appears, re-run exact canonical metadata, final <=50ms q1 depth/economics, positions, active orders, and capital reservation immediately before any bounded live promotion.
10. Any future live FOK campaign must use a fresh unique campaign + matching arm token + short boot expiry, restart `NEVER`, and cleared arm variables after boot. Never reuse an expired campaign.
11. Continue sports breadth and exact-rule weather opportunities without weakening certification. Work one blocker through inspect -> fix -> deploy -> verify -> reconcile -> checkpoint before widening scope.

Never store credentials, signing material, API keys, or sensitive balances here.