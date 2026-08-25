# ARB Control Plane

Last verified: **2026-08-25 06:48 UTC / 2026-08-25 02:48 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Current scoreboard

- Caleb Durbin one-leg Gemini exposure remains realized at **gross -$0.42 / net -$0.44**; do not shadow it away.
- Settlement reconciler previously confirmed `open_reconcilable=0` and `one_leg_open=0`.
- Prior historical proof remains approximately **+$0.2679** across seven earlier settled-compatible Gemini<->Kalshi sports trades; keep historical proof separate from Caleb.
- Same-Gemini crypto most recently showed roughly 2,647 certified q1 pairs / 275 books and **0 immediate >=3c FOK/FOK locks**.
- Previously observed K<->P strict-prop taker snapshots had **0 >=1c, 0 >=2c and 0 >=3c after-fee locks** among books with usable quotes, but the intended K<->P census has now been found miswired in production; do not treat same-Polymarket snapshots as K<->P evidence.
- Recent Polymarket maker attempts were zero-fill. Maker-indicated rows are not arbitrage.

## Fixed edge-census protocol

Instrumentation deadline: **Wednesday 2026-08-26**. Then collect **72 hours** and publish verdict **Sunday 2026-08-30**. Data-quality/feed failure may add at most 24 hours; an unmeasurable lane after that counts as **$0 deployable capacity**. Do not extend because results are interesting or disappointing.

Pre-committed project verdict thresholds, which must not move after seeing results:

- **<$50/day** realistically deployable locked-edge capacity: end dedicated arb engineering and archive the project.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward the $480/day aspiration.

Required locked-edge measurements are >=1c/>=2c/>=3c after fees, exact executable depth, p50/p90/max lifetime, quote skew, overlap-adjusted capacity, and latency-floor-filtered realistic capture. Provisional latency floor is 80ms and may rise to measured production p95 but may not be lowered to rescue capacity. Maker adverse-selection evidence uses public trade-print proxy, not live census orders.

Same-venue Gemini/Polymarket/Kalshi zero results are near-verdict evidence; do not spend engineering time manufacturing same-venue ladder violations.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

**Production drift/root cause found 02:33-02:48 ET.** Repo branch `feature/sports-kp-live-executor-impl` had already been changed to start `python -m src.sports_poly_mlb_edge_census --interval-seconds 15`, but Railway retained an older service-level override starting `python -m src.sports_poly_structural_live_supervisor_verified` with `restartPolicy=NEVER`.

The Railway override was corrected to match the branch's intended zero-order census command, predeploy compile gate restored, `restartPolicy=ALWAYS`, and deployment `25cea885-e1f2-4522-a31a-58d6a5a58824` reached **SUCCESS** at 06:36:48 UTC. Fresh CPU/network metrics confirm the replacement process is active.

However, the first replacement log exposed a deeper census wiring error: `src.sports_poly_mlb_edge_census` is a **same-Polymarket monotone-threshold census**, not a true Kalshi<->Polymarket cross-venue scanner. Its first snapshot reported `strict_markets=0`, `strict_pairs=0`, `books_requested=0`, and 0/0/0 in >=1c/>=2c/>=3c bins, with `order_entry=false`. Those zeros are same-venue near-verdict evidence only and **must not be credited as K<->P census data**.

A real event-driven read-only K<->P implementation already exists in `src/polymarket_kalshi_shadow_v5.py`: it strict-matches exact MLB participants, threshold, scheduled start within five minutes and MLB result source; seeds REST only at startup; then uses Polymarket/Kalshi book updates, after-fee two-leg economics, quote ages/skew and OPEN/MOVE/CLOSE lifetime telemetry with no order-entry code. Current v5 scope is **full-game totals only**, so it is useful but still insufficient for the pre-committed lane by itself.

Separately, `src/three_venue_mlb_price_scan.py` contains read-only normalized cross-venue MLB pricing across Gemini/Polymarket/Kalshi with semantic counterpart keys and q1 after-fee locks; it can supply broader full-game identity/pricing primitives, but its current snapshot path uses up to 5-second quote skew and is not decision-grade lifetime/capture telemetry.

Therefore the K<->P 72-hour verdict clock is **not valid yet**. Before Wednesday's instrumentation deadline the actual cross-venue collector must cover semantic-deduped full-game MLB moneyline/spread/total plus strict rule-certifiable props and persist >=1c/>=2c/>=3c bins, q1+ depth, quote skew, OPEN/CLOSE lifetime and >=80ms realistic-capture capacity. Participation-conditional or rule-mismatched rows remain excluded.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Latest deployment remains **SUCCESS** and the event-driven read-only hot shadow is active. Current start command uses `--hot-profit 0.01`, 50ms max quote skew, 100ms hot-book age and 10s heartbeat. Source inspection confirms it records positive OPEN/MOVE/CLOSE events, close `lifetime_ms`, quote ages and cross-venue skew and imports no order transport. G<->K live mutation remains disarmed and Caleb's realized one-leg loss remains in accounting.

Do not re-arm G<->K unless the reconciled zero-open-exposure state remains conclusively true and a separate bounded-risk decision explicitly authorizes it. Census collection requires no live order authority.

### Kalshi account-limit diagnosis — RESOLVED

The authenticated account-limits probe was already successfully deployed at 03:00 UTC on service `sports-gk-account-preflight-now`. Its authoritative log reports:

- `usage_tier`: **advanced**
- read bucket capacity: **900**
- read refill rate: **300**
- write bucket capacity: **900**
- write refill rate: **300**
- host: **external-api.kalshi.com**
- `order_entry=false`

This materially changes the 429 diagnosis: the account is **not on a low Kalshi tier**. Any recurrent 429s must now be investigated as aggregate account-wide contention, burst behavior, endpoint-specific limits, or local scheduler behavior rather than assuming insufficient account tier. Do not reduce safety/backoff or stop unrelated live services solely for census telemetry. New census code should continue preferring Kalshi dedicated `external-api` / `external-api-ws` hosts where supported.

Next account-level step is to inventory concurrent Railway Kalshi consumers and correlate request bursts/429 timestamps against the 300-refill/900-capacity account envelope before changing pacing.

### Same-Gemini crypto

Keep zero-order-entry census only. Prior snapshot remains ~2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks. Treat this as near-verdict evidence, not a prompt to manufacture same-venue violations.

### Old Gemini<->Kalshi 15m crypto

Fresh project status still reports `crypto-15m-sol-live-canary-10bp-50ms` **CRASHED**. The old executor remains a known bounded-campaign crash case and must not be restarted as guaranteed arbitrage: settlement-index non-equivalence remains the stronger blocker. The safe rollover repair can remain review-ready but is not a reason to spend census time reopening this lane.

## Weather side question

The central arb Neon ledger has **no weather-tagged trade events since 2026-03-01**, so it contributes **$0 verified realized weather P&L** from that source. The separate Railway project `kalshi-weather-bot` is reachable, but current runtime logs do not contain authoritative March-to-date settlement/P&L history.

Fresh separate-project verification: `weather-live-probe` is currently **CRASHED fail-closed** after passing 22 tests because its target market is finalized; the runtime explicitly logged `WEATHER_LIVE_PROBE_BLOCKED`, `order_entry=false`, `reason=market_not_open:finalized`. The main weather bot's exposed logs from Aug 19-24 show model-update monitoring with `EMERGENCY STOP ACTIVE` repeatedly skipping triggers, not authoritative realized trades.

Therefore realized weather P&L since March remains **$0 verified from the records currently exposed**. Historical/backtest/seasonal edge remains categorically separate and cannot be credited to current realized performance unless a separate authoritative trade/settlement record is found.

## Next highest-value actions

1. **Fix the actual K<->P census path before the Wednesday deadline.** Reuse the existing strict K<->P event-driven totals scanner and broader normalized MLB identity/pricing primitives, but add/route a zero-order collector covering full-game ML/spread/total plus strict certifiable props with explicit >=1c/>=2c/>=3c, depth, skew, OPEN/CLOSE lifetime and >=80ms capture telemetry. Do not count the current same-Polymarket census as K<->P.
2. Inventory concurrent Railway Kalshi API consumers and correlate bursts/429s against the now-known **advanced 900-capacity / 300-refill** read/write account limits before changing pacing/backoff. Do not disrupt unrelated services.
3. Verify G<->K hot-shadow OPEN/MOVE/CLOSE and latency telemetry is durably captured while order entry stays disarmed.
4. Continue weather record search only for authoritative settled P&L. Until found, weather contributes $0 to the census decision.
5. Preserve the fixed verdict thresholds and deadline. If K<->P remains unmeasurable after the single allowed +24h data-quality extension, count it as $0 deployable capacity rather than extending research.
