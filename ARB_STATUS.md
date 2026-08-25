# ARB Control Plane

Last verified: **2026-08-25 10:04 UTC / 2026-08-25 06:04 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Current scoreboard

- Caleb Durbin one-leg Gemini exposure remains realized at **gross -$0.42 / net -$0.44**; do not shadow it away.
- Settlement reconciler previously confirmed `open_reconcilable=0` and `one_leg_open=0`.
- Prior historical proof remains approximately **+$0.2679** across seven earlier settled-compatible Gemini<->Kalshi sports trades; keep historical proof separate from Caleb.
- Same-Gemini crypto most recently showed roughly 2,647 certified q1 pairs / 275 books and **0 immediate >=3c FOK/FOK locks**.
- **2026-08-25 ET so far: 0 authoritative `TRADE_REALIZED` rows / $0.00 realized net** in the central Neon ledger as re-queried this run.
- Same-Polymarket census zeros remain same-venue near-verdict evidence only; they are not K<->P evidence.

## Fixed edge-census protocol

Instrumentation deadline: **Wednesday 2026-08-26**. Then collect **72 hours** and publish verdict **Sunday 2026-08-30**. Data-quality/feed failure may add at most 24 hours; an unmeasurable lane after that counts as **$0 deployable capacity**. Do not extend because results are interesting or disappointing.

Pre-committed project verdict thresholds, which must not move after seeing results:

- **<$50/day** realistically deployable locked-edge capacity: end dedicated arb engineering and archive the project.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward the $480/day aspiration.

Required measurements: >=1c/>=2c/>=3c after fees, exact executable depth, p50/p90/max lifetime, quote skew, overlap-adjusted capacity, and latency-floor-filtered realistic capture. Provisional latency floor is 80ms and may rise to measured production p95 but may not be lowered to rescue capacity. Maker adverse-selection evidence uses public trade-print proxy, not live census orders.

Same-venue Gemini/Polymarket/Kalshi zero results are near-verdict evidence; do not spend engineering time manufacturing same-venue ladder violations.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

The service is now running the **actual read-only event-driven K<->P collector**, `src.polymarket_kalshi_shadow_v5`, on Railway deployment `9daf422f-8851-42e3-bf39-ac00faec3a37`, currently **SUCCESS**. The production path has no order-entry authority.

Strict discovery remains strong: **45 Polymarket MLB total candidates, 154 Kalshi candidates and 42 exact cross-venue matches**, with validated examples showing zero-second scheduled-start skew and common MLB result source.

This run resolved three production wiring defects:

1. The old 120-second outer readiness timeout was restarting the read-only collector every ~2 minutes while Kalshi's internal WS client was already backing HTTP 429 off 15s/30s. That self-amplified handshake churn. The outer startup window is now **3600s**, preserving the existing exponential backoff instead of resetting it.
2. The collector had inherited the legacy `api.elections.kalshi.com` websocket URL. PR **#137** moved this census lane only to `wss://external-api-ws.kalshi.com/trade-api/ws/v2`; no unrelated Kalshi service or global config was changed.
3. Two stale branch dependencies caused immediate runtime import failures (`Match` imported from the wrong module, then missing `src.sports_kp_shadow`). PRs **#138/#139** repaired those read-only imports. The production predeploy gate now performs both compile **and actual module-import checks**, and the scoped Railway Docker suite passed **37/37**.

The dedicated-host test is decisive: **Kalshi still returns HTTP 429 on `external-api-ws`**. Fresh runtime sequence on the stable process:

- 10:03:17 UTC: dedicated-host WS handshake rejected HTTP 429 -> existing backoff **15s**.
- 10:03:32 UTC: next handshake rejected HTTP 429 -> backoff **30s**.
- 10:04:02 UTC: next handshake rejected HTTP 429 -> backoff remains **30s**.

Crucially, the process stayed alive between those attempts instead of Railway restarting it. Therefore the legacy hostname was **not** the root cause. The remaining Kalshi blocker is websocket-handshake/account-wide connection contention or endpoint/account policy, not weak account tier and not generic REST depletion.

There is now a separate Polymarket feed-quality blocker. On the newest run, many unauthenticated REST book-seed calls to `api.polymarket.us/v1/markets/<slug>/book` returned **401 Unauthorized** while a small subset returned 200. Only **6 of the 42 strict pairs** had seedable two-sided Polymarket books, and the Polymarket WS subscribed to those 6. The live Polymarket stream itself is healthy for that subset (accepted updates continue), but the census currently lacks adequate book coverage.

Because Kalshi has not initialized a single matched live book and Polymarket usable coverage is currently only 6/42, **the K<->P 72-hour verdict clock remains INVALID**. Do not extrapolate any edge-bin result from this degraded feed.

V5 already records after-fee economics, executable q/depth, quote ages, cross-receive skew, trigger->calc latency and OPEN/MOVE/CLOSE lifetimes once both venue books are ready. It still covers **full-game totals only**; before the Wednesday instrumentation deadline the lane also needs semantic-deduped MLB moneyline/spread plus strict rule-certified props using the same event-driven standard. Participation-conditional and rule-mismatched rows remain excluded.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Latest deployment remains **SUCCESS** and the event-driven read-only hot shadow is active. Fresh heartbeat evidence this run showed approximately **60.1M triggers/evaluations, 27,002 comparable evaluations, 51 hot >=3c evaluations and 33 OPEN / 33 CLOSE events**, with zero active signal at that instant. The process had **272 Gemini symbols and 272 initialized Kalshi tickers**.

These rows are not deployable-capacity evidence yet because the live rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`; order entry is false.

The same process maintains a healthy Kalshi market-data path and sampled large REST orderbook refreshes returned HTTP 200. That account-level evidence matters: Kalshi is not globally unavailable while the new K<->P WS handshakes are 429ing. Do **not** stop G<->K solely to improve census telemetry.

G<->K live mutation remains disarmed. Caleb's -$0.44 one-leg loss remains authoritative realized accounting.

### Kalshi account-limit / 429 diagnosis

Authenticated `/account/limits` evidence remains:

- `usage_tier`: **advanced**
- read bucket capacity: **900**
- read refill rate: **300**
- write bucket capacity: **900**
- write refill rate: **300**
- REST host: `external-api.kalshi.com`

This rules out a weak account tier as the simple explanation. K<->P REST discovery continues to receive HTTP 200 from `external-api.kalshi.com`, G<->K maintains 272 initialized Kalshi tickers, and the dedicated `external-api-ws` K<->P handshake still returns 429. The remaining diagnosis is therefore **WS handshake/concurrent-connection contention or account/endpoint-specific connection policy**.

Do not lower exponential backoff or disrupt unrelated services solely to improve census telemetry. The next account-level test is to inventory active continuous Kalshi websocket consumers and correlate K<->P handshake availability with natural connection turnover, not forced shutdowns.

### Same-Gemini crypto

Keep zero-order-entry census only. Prior snapshot remains ~2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks. Treat this as near-verdict evidence, not a prompt to manufacture same-venue violations.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains a known **CRASHED** bounded-campaign case. Do not restart it as guaranteed arbitrage: settlement-index non-equivalence remains the stronger blocker. The safe rollover repair is not a reason to spend census time reopening this lane.

## Weather side question

The central arb Neon ledger has **no weather-tagged trade events since 2026-03-01**, so it contributes **$0 verified realized weather P&L** from that source. The separate Railway project `kalshi-weather-bot` is reachable, but exposed runtime records still do not provide authoritative March-to-date settlement/P&L history.

The current weather live probe has been fail-closed on a finalized target market (`order_entry=false`). Historical/backtest/seasonal edge remains categorically separate and cannot be credited as current realized performance.

## Next highest-value actions

1. **Repair Polymarket book-seed coverage without adding order authority.** Determine the correct authenticated/public read path for the 42 strict K<->P books; current 6/42 coverage is not decision-grade.
2. Keep the dedicated Kalshi WS process alive under its existing 15s/30s backoff and observe whether it acquires a connection without Railway-induced restart churn. Correlate failures with active account-wide WS consumers; do not disrupt unrelated services merely for telemetry.
3. Once both books are ready, persist explicit >=1c/>=2c/>=3c bins, depth, skew, OPEN/CLOSE lifetime and >=80ms realistic-capture capacity. The clock starts only from decision-grade measurement, not from deployment `SUCCESS`.
4. Before Wednesday, add event-driven MLB ML/spread and strict rule-certified props with semantic dedupe. Participation-conditional or rule-mismatched rows remain excluded.
5. Continue G<->K hot-shadow measurement while disarmed; only lifecycle-rule-certified rows can contribute deployable-capacity estimates.
6. Continue weather record search only for authoritative settled P&L; until found, weather contributes $0 to the census decision.
7. Preserve the fixed verdict thresholds and deadline. If K<->P remains unmeasurable after the single allowed +24h data-quality extension, count it as $0 deployable capacity rather than extending research.
