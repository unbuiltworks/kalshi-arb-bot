# ARB Control Plane

Last verified: **2026-08-25 08:50 UTC / 2026-08-25 04:50 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Current scoreboard

- Caleb Durbin one-leg Gemini exposure remains realized at **gross -$0.42 / net -$0.44**; do not shadow it away.
- Settlement reconciler previously confirmed `open_reconcilable=0` and `one_leg_open=0`.
- Prior historical proof remains approximately **+$0.2679** across seven earlier settled-compatible Gemini<->Kalshi sports trades; keep historical proof separate from Caleb.
- Same-Gemini crypto most recently showed roughly 2,647 certified q1 pairs / 275 books and **0 immediate >=3c FOK/FOK locks**.
- **2026-08-25 ET so far: 0 authoritative `TRADE_REALIZED` rows / $0.00 realized net** in the central Neon ledger as of 08:50 UTC.
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

**04:46-04:50 ET root cause and correction:** Railway's visible service configuration had been changed to `python -m src.polymarket_kalshi_shadow_v5 --max-quote-age-ms 100`, but a forced redeploy still emitted `SPORTS_POLY_EDGE_CENSUS_*` logs from the same-Polymarket census. The reason was repo config precedence: `railway.structural.json` on `feature/sports-kp-live-executor-impl` still hard-coded `src.sports_poly_mlb_edge_census`, so every deploy silently reverted the service-level command.

Commit `c5a87c2f10da79b89e92aae30279efb94bbf654c` fixes the branch's Railway config itself. Deployment `6e9c660a-482d-4326-99ca-aedf31f90afd` then started the **actual read-only K<->P v5 collector**. This is verified from runtime, not merely a deployment badge:

- `EVENT_START shadow_only=true ... max_quote_age_ms=100`
- Polymarket teams/events and Kalshi `KXMLBTOTAL` discovery all returned HTTP 200.
- strict discovery found **45 Polymarket candidates, 154 Kalshi candidates, 42 exact matched totals**, with zero missing-start/source/ambiguity rejections.
- validated examples had **0-second scheduled-start skew** and common `result_source=MLB`.
- one-time Polymarket REST seed produced current books for **42/42** strict matches and the Polymarket websocket subscribed to all 42.

The first true K<->P startup then exposed the current feed blocker: the Kalshi cross-book websocket was rejected with **HTTP 429**, backed off 15 seconds, was rejected again, backed off 30 seconds, and the original startup window expired at `poly_connected=True poly_books=42/42 kalshi_books=0/42 seeded=42/42 excluded=0`.

This is now direct account-level evidence that the K<->P census problem is **Kalshi websocket availability/connection contention**, not missing semantic counterparts or Polymarket coverage. It also reinforces that prior same-Polymarket 0/0/0 snapshots must not be credited to K<->P.

Commit `fb0a7517f0317b29b8edf588bf28e72f40c56344` keeps the existing Kalshi exponential backoff unchanged and extends only the read-only v5 startup readiness window to **120 seconds** (`--startup-timeout-s 120`). It does not increase request rate, reduce backoff, stop another service, add order authority, or loosen quote-age/economics rules. Replacement deployment `c6e2c29e-474b-40ee-9788-dcd1a61749e7` is **SUCCESS** and is running v5; its first Kalshi WS connection at 08:49:40 UTC again received HTTP 429 and entered the preserved 15-second backoff.

The K<->P 72-hour verdict clock is therefore **still not valid yet**. A valid totals sublane requires `EVENT_READY` plus live `EVENT_SUMMARY`/`EVENT_SHADOW` telemetry from both venues. V5 already records after-fee plan economics, q/depth, quote ages, cross-receive skew, trigger->calc latency and OPEN/MOVE/CLOSE lifetimes. It remains **full-game totals only**, so before the Wednesday instrumentation deadline the lane still needs semantic-deduped MLB moneyline/spread plus strict rule-certifiable props using the same event-driven measurement standard. Participation-conditional or rule-mismatched rows remain excluded.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Latest known deployment remains **SUCCESS** and the event-driven read-only hot shadow is active. Current command uses a 1c census threshold, 50ms max quote skew, 100ms hot-book age and 10s heartbeat. It records positive OPEN/MOVE/CLOSE events, close lifetime, quote ages and cross-venue skew and imports no order transport.

G<->K live mutation remains disarmed. Caleb's -$0.44 one-leg loss remains authoritative realized accounting. Do not re-arm without a separate bounded-risk decision and fresh proof of zero open/unresolved exposure.

### Kalshi account-limit / 429 diagnosis

Authenticated `/account/limits` evidence remains:

- `usage_tier`: **advanced**
- read bucket capacity: **900**
- read refill rate: **300**
- write bucket capacity: **900**
- write refill rate: **300**
- dedicated host: `external-api.kalshi.com`

The fresh true K<->P v5 runtime hit HTTP 429 on the **Kalshi websocket at startup**, twice in the first attempt and again immediately on the extended-timeout deployment. Since the account is advanced, do not attribute this to a weak account tier. The remaining diagnosis is aggregate account-wide concurrent connection/request bursts, endpoint-specific connection limits, or scheduler behavior. Do not lower backoff or disrupt unrelated services solely to improve census telemetry.

### Same-Gemini crypto

Keep zero-order-entry census only. Prior snapshot remains ~2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks. Treat this as near-verdict evidence, not a prompt to manufacture same-venue violations.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains a known **CRASHED** bounded-campaign case. Do not restart it as guaranteed arbitrage: settlement-index non-equivalence remains the stronger blocker. The safe rollover repair can remain review-ready but is not a reason to spend census time reopening this lane.

## Weather side question

The central arb Neon ledger has **no weather-tagged trade events since 2026-03-01**, so it contributes **$0 verified realized weather P&L** from that source. The separate Railway project `kalshi-weather-bot` is reachable, but exposed runtime records still do not provide authoritative March-to-date settlement/P&L history.

The current weather live probe has been fail-closed on a finalized target market (`order_entry=false`). Historical/backtest/seasonal edge remains categorically separate and cannot be credited as current realized performance.

## Next highest-value actions

1. **Get the true K<->P totals collector through `EVENT_READY` without raising Kalshi request/connection pressure.** The current blocker is repeated Kalshi websocket HTTP 429 while Polymarket is 42/42 ready. The 120-second readiness window now gives the existing exponential backoff room to work.
2. Inventory concurrent Railway Kalshi consumers and correlate active connection/request bursts against the advanced 900-capacity / 300-refill account envelope; determine whether websocket connection churn or aggregate API demand is the source before changing pacing.
3. Before Wednesday, extend the event-driven K<->P census from totals to semantic-deduped MLB ML/spread and strict rule-certified props; persist explicit >=1c/>=2c/>=3c bins, depth, skew, OPEN/CLOSE lifetime and >=80ms realistic-capture capacity. Do not count participation-conditional or rule-mismatched rows.
4. Verify G<->K hot-shadow OPEN/MOVE/CLOSE telemetry remains active while order entry stays disarmed.
5. Continue weather record search only for authoritative settled P&L; until found, weather contributes $0 to the census decision.
6. Preserve the fixed verdict thresholds and deadline. If K<->P remains unmeasurable after the single allowed +24h data-quality extension, count it as $0 deployable capacity rather than extending research.
