# ARB Control Plane

Last verified: 2026-08-22 17:29 UTC

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

## Same-Gemini sports combo live lane

Dedicated isolated service `gemini-extreme-combo-live-q1` (service id `4399e98f-ce33-4a74-99f2-900f7615beab`) runs on `feature/three-venue-sports-router`, restart policy `NEVER`. This lane uses same-Gemini combo settlement plus exact component complements, so the conservative payoff identity is `AB + (1-A) + (1-B) >= 1`, including scalar component settlements. It is separate from the uncertified Gemini/Kalshi crypto settlement-basis lane.

### Earlier blocked campaign

Campaign `gcombo-2608221121-a1` targeted combo `GEMI-CMB-260823-8EC8FBCF7988E34C-CMB`, q=1, maker BUY YES $0.90, target lock >=$0.03. The 15:24 UTC attempt was correctly `LIVE_GATE_BLOCKED`: both exact hedge outcomes had no executable offer, therefore **0 orders, 0 fills, $0 realized**. Do not reuse that campaign.

### 16:10 UTC live-probe incident and recovery

A later q=1 pre-hedge-first probe on the Albon + Morikawa candidate exposed an execution-recovery bug:
- YES Albon hedge filled q=1 at `$0.02` plus `$0.01` fee;
- YES Morikawa hedge filled q=1 at `$0.03` plus `$0.01` fee;
- the combo maker order at `$0.89` did not fill and was canceled;
- the old emergency flattener submitted `MARKET+IOC`, which Gemini prediction markets rejected as `InvalidOrderType`.

The lane was immediately disarmed and reconciled. Bounded incident recovery then used supported SELL `LIMIT+FOK` exits:
- Albon sold q=1 at `$0.01` plus `$0.01` fee;
- Morikawa sold q=1 at `$0.02` plus `$0.01` fee.

Independent final reconciliation showed **0 remaining positions and 0 active orders** on both hedges and the combo. Gemini available USD moved from `$62.5655` before the incident to `$62.5055` after recovery, implying approximately **-$0.06 realized live-probe loss**. This is a probe/recovery loss, not arbitrage profit, and was not yet represented as a sports `TRADE_REALIZED` event in the authoritative ARB ledger at the last ledger snapshot.

The recovery bug is fixed for this lane: prediction-market exits use marketable `LIMIT+FOK`, not `MARKET+IOC`.

### Combo-first redesign / no-fill proof

The executor was redesigned to place the combo maker order **before** buying any hedge. If the combo does not fill, it cancels with zero hedge churn. If it fills, exact complement hedges are purchased immediately with bounded FOK orders.

Verified live evidence:
- single q=1 combo-first attempt: no combo fill, canceled, **0 hedge orders / $0 new loss**;
- bounded five-candidate q=1 rotation: exactly five maker orders, all canceled after their windows, **0 fills and 0 hedge orders**; the user-visible repeated place/cancel notifications came from this intentional rotation, not from filled trades;
- q=3 quantity-aware research showed fee amortization can support higher maker prices, but partial-fill economics make long-lived q>1 less suitable for the guaranteed >=3c lane;
- a q=3 Toronto/Columbus live probe at `$0.92` passed fresh quantity-aware preflight (`$2.91` all-in / `$0.09` locked total) but did not fill during 120 seconds and canceled with **0 hedge orders / $0 new loss**;
- Gemini production rejected same-event multi-driver F1 combos (`at most one contract per event`) and rejected a same-match Toronto total+spread construction as `too closely correlated`; these structures must be excluded from future candidate ranking.

Gemini's documented combo RFQ WebSocket/quote methods are **not currently enabled in production**, so production execution currently must use normal combo order books rather than solicited RFQs.

### Current live state — monitored resting q=1 order

Current implementation commit `15d83d6235dbcc32f3c33aeba6498e21e32229e8` adds a long-lived, monitored **single q=1 maker order** instead of cancel/repost rotation. Fresh deployment trigger commit `e26a732fd6a1b60d50829662bdc23c01bb699aaf`; armed Railway deployment `dd0482fe-6733-41ec-8367-a57e36ce38a7` is running campaign `gcombo-2608221328-torclb-rest1`.

Candidate:
- combo `GEMI-CMB-260823-8EC8FBCF7988E34C-CMB`;
- q=1 maker BUY YES at `$0.90`;
- exact complements are Toronto/Miami total `GEMI-MLS-2608222330-TOR-MIA-REGT-O0` BUY NO and Columbus/Nashville spread `GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2` BUY YES;
- target floor remains **>= $0.03 after fees**;
- one resting order only, maximum lifetime 1800 seconds, hedge books revalidated every 1 second;
- no hedge is bought unless the combo fills;
- if either exact hedge loses q=1 depth, exceeds the 2c target, or the conservative lock drops below 3c, the maker combo is canceled immediately;
- environment arm variables were cleared immediately after the armed process booted, preventing replay on any future deploy while the current process keeps its in-memory one-shot authorization.

Authoritative live runtime heartbeat at `2026-08-22 17:29:34 UTC`, after ~60.8 seconds open:
- `order_entry=true`;
- both exact hedge asks still `$0.02`, depth `100` each;
- conservative q=1 taker fee `$0.01` per hedge;
- combo conservative maker fee `$0.01`;
- total all-in `$0.97`;
- locked floor `$0.03`;
- `hedge_state.ok=true`.

This is currently a **LIVE PROBE with one resting maker order**, not an executed trade and not realized P&L. It remains live until the combo fills, hedge economics deteriorate and trigger cancellation, or the 1800-second safety TTL expires.

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

1. Read `ARB_STATUS.md` and inspect current Railway runtime before mutating any lane.
2. Keep Gemini/Kalshi crypto q1/q5/maker blocked unless exact settlement + threshold equivalence is independently certified.
3. Verify no new crypto submission occurred after 06:40:03 UTC; ETH `gk15m-gk15m-08220615-c225-02` is reconciled +$0.049600 and should no longer be treated as unresolved.
4. For the current same-Gemini sports lane, inspect campaign `gcombo-2608221328-torclb-rest1` first. Do not redeploy/re-arm or create another maker order until its active/filled/canceled state and any resulting positions are reconciled.
5. If the current combo fills, verify both exact q=1 hedges filled and classify only `FILLED_NOT_SETTLED` until every component settles. If it canceled for stale hedge economics or TTL, verify zero positions and choose a fresh structurally valid candidate rather than reusing the campaign.
6. Do not return to short cancel/repost carousels unless there is a specific fill-probability reason; prefer one monitored resting q=1 order to reduce notification noise and hedge churn.
7. Improve candidate filtering by excluding Gemini same-event and known `too closely correlated` pair classes before creation/ranking.
8. Highest-value same-Kalshi blocker remains proving `sports-kp-combo-shadow` runtime telemetry / `DATABASE_URL`, then using authoritative same-market >=3c evidence for any bounded live promotion.
9. Continue <=24h sports breadth and exact-rule weather opportunities without weakening certification.
10. Work one blocker through inspect -> fix -> deploy -> verify -> reconcile -> checkpoint before widening scope.

Never store credentials, signing material, API keys, or sensitive balances here.
