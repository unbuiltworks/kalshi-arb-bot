# ARB Control Plane

Last verified: **2026-08-25 09:56 UTC / 2026-08-25 05:56 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Current scoreboard

- Caleb Durbin one-leg Gemini exposure remains realized at **gross -$0.42 / net -$0.44**; do not shadow it away.
- Settlement reconciler previously confirmed `open_reconcilable=0` and `one_leg_open=0`.
- Prior historical proof remains approximately **+$0.2679** across seven earlier settled-compatible Gemini<->Kalshi sports trades; keep historical proof separate from Caleb.
- Same-Gemini crypto most recently showed roughly 2,647 certified q1 pairs / 275 books and **0 immediate >=3c FOK/FOK locks**.
- **2026-08-25 ET so far: 0 authoritative `TRADE_REALIZED` rows / $0.00 realized net** in the central Neon ledger as re-queried at 09:54 UTC.
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

The repo/service wiring now points at the **actual read-only K<->P event-driven collector**, `src.polymarket_kalshi_shadow_v5`. Strict discovery most recently found **45 Polymarket MLB total candidates, 154 Kalshi candidates and 42 exact cross-venue matches**. Polymarket seeded **42/42** current books and its websocket subscribed successfully. The K<->P verdict clock is still **not valid** because Kalshi has not reached live-book readiness.

Fresh runtime inspection exposed two separate feed-quality problems:

1. **The 120-second outer startup timeout was self-amplifying Kalshi websocket churn.** Kalshi's own stream already backs HTTP 429 off to 15s then 30s, but after 120s the v5 process raised `startup readiness timeout`, Railway `restartPolicy=ALWAYS` restarted it, and a fresh websocket handshake sequence began. This was repeating roughly every two minutes with `poly_connected=True poly_books=42/42 kalshi_books=0/42`.
2. **The K<->P Kalshi websocket was still inheriting the repo's legacy production WS host** (`wss://api.elections.kalshi.com/trade-api/ws/v2`) from `config.settings`, even though discovery REST already uses Kalshi's dedicated external API host. The K<->P collector had therefore not yet implemented the pre-committed preference for the dedicated external websocket endpoint.

PR **#137** was merged as commit `006f0822738d5a19e62f594bdbf757998c08fb9f`. It is deliberately read-only and makes two census reliability changes only:

- use `wss://external-api-ws.kalshi.com/trade-api/ws/v2` by default for this K<->P collector, with a census-specific env override if ever needed;
- extend only the bounded startup-readiness window from 120s to **3600s**, so the existing exponential 15s/30s Kalshi backoff remains alive instead of being reset by Railway every ~2 minutes.

No order authority, fee math, edge threshold, quote-age threshold, rule certification, depth requirement, websocket backoff, or unrelated Kalshi service was changed. The module still contains no order-entry path.

Repository CI for #137 reports **427 passed / 1 failed**. The sole failure is the unrelated existing `test_pitcher_must_be_starter_and_throw_pitch` MLB rule-equivalence assertion; neither changed file is involved. The production service also retains a targeted predeploy Python compile gate. The replacement Railway deployment `c81714a2-3575-4bc0-a38a-05e0959f2d63` is currently **BUILDING**; do not claim `EVENT_READY` until runtime proves Kalshi books initialize on the dedicated host.

The K<->P 72-hour verdict clock remains invalid until this service reaches `EVENT_READY` and produces both-venue `EVENT_SUMMARY`/`EVENT_SHADOW` telemetry. V5 records after-fee economics, q/depth, quote ages, cross-receive skew, trigger->calc latency and OPEN/MOVE/CLOSE lifetimes. It remains **full-game totals only**, so before the Wednesday instrumentation deadline the lane still needs semantic-deduped MLB moneyline/spread plus strict rule-certifiable props using the same event-driven measurement standard. Participation-conditional or rule-mismatched rows remain excluded.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Latest deployment remains **SUCCESS** and the event-driven read-only hot shadow is active. Fresh heartbeat evidence around 09:34 UTC showed approximately **60.1M triggers/evaluations, 27,002 comparable evaluations, 51 hot >=3c evaluations and 33 OPEN / 33 CLOSE events**, with zero active signal at that instant. The process had **272 Gemini symbols and 272 initialized Kalshi tickers**. These hot observations are **not executable-capacity evidence yet** because the live rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`; order entry is false.

The same process also performs large batched Kalshi orderbook REST refreshes roughly every five minutes and those sampled requests were returning HTTP 200. That is useful account-level evidence: Kalshi is not globally unavailable, and another active consumer can maintain a healthy Kalshi market-data path while K<->P websocket handshakes are 429ing. Do not stop G<->K solely to improve census telemetry.

G<->K live mutation remains disarmed. Caleb's -$0.44 one-leg loss remains authoritative realized accounting. Do not re-arm without a separate bounded-risk decision and fresh proof of zero open/unresolved exposure.

### Kalshi account-limit / 429 diagnosis

Authenticated `/account/limits` evidence remains:

- `usage_tier`: **advanced**
- read bucket capacity: **900**
- read refill rate: **300**
- write bucket capacity: **900**
- write refill rate: **300**
- REST host: `external-api.kalshi.com`

This rules out a weak account tier as the simple explanation. K<->P REST discovery succeeds with HTTP 200 while its legacy-host websocket handshakes returned HTTP 429. At the same time, the G<->K hot shadow maintains 272 initialized Kalshi tickers and periodically issues large orderbook REST batches successfully. The remaining diagnosis is therefore **websocket-handshake/account-wide connection contention or endpoint/scheduler behavior**, not generic REST depletion.

Do not lower exponential backoff or disrupt unrelated services solely to improve census telemetry. First verify whether the dedicated `external-api-ws` deployment reaches readiness while preserving the same account-wide load.

### Same-Gemini crypto

Keep zero-order-entry census only. Prior snapshot remains ~2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks. Treat this as near-verdict evidence, not a prompt to manufacture same-venue violations.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains a known **CRASHED** bounded-campaign case. Do not restart it as guaranteed arbitrage: settlement-index non-equivalence remains the stronger blocker. The safe rollover repair can remain review-ready but is not a reason to spend census time reopening this lane.

## Weather side question

The central arb Neon ledger has **no weather-tagged trade events since 2026-03-01**, so it contributes **$0 verified realized weather P&L** from that source. The separate Railway project `kalshi-weather-bot` is reachable, but exposed runtime records still do not provide authoritative March-to-date settlement/P&L history.

The current weather live probe has been fail-closed on a finalized target market (`order_entry=false`). Historical/backtest/seasonal edge remains categorically separate and cannot be credited as current realized performance.

## Next highest-value actions

1. **Verify deployment `c81714a2-3575-4bc0-a38a-05e0959f2d63` on the dedicated Kalshi websocket host.** It must reach `EVENT_READY` with live Kalshi books; if it still receives 429, preserve backoff and measure handshake timing rather than restarting every two minutes.
2. Correlate K<->P websocket failures with the already-active G<->K Kalshi consumer and other account-wide Railway consumers. The account is advanced 900/300, so determine whether there is a concurrent-connection/handshake constraint before changing pacing.
3. Before Wednesday, extend event-driven K<->P measurement from totals to semantic-deduped MLB ML/spread and strict rule-certified props; persist explicit >=1c/>=2c/>=3c bins, depth, skew, OPEN/CLOSE lifetime and >=80ms realistic-capture capacity. Do not count participation-conditional or rule-mismatched rows.
4. Continue G<->K hot-shadow measurement while order entry remains disarmed; only lifecycle-rule-certified rows can contribute deployable-capacity estimates.
5. Continue weather record search only for authoritative settled P&L; until found, weather contributes $0 to the census decision.
6. Preserve the fixed verdict thresholds and deadline. If K<->P remains unmeasurable after the single allowed +24h data-quality extension, count it as $0 deployable capacity rather than extending research.
