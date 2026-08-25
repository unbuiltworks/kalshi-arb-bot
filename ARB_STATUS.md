# ARB Control Plane

Last verified: **2026-08-25 14:41 UTC / 2026-08-25 10:41 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Fixed edge-census protocol

Instrumentation deadline: **Wednesday 2026-08-26**. Then collect **72 hours** and publish verdict **Sunday 2026-08-30**. Data-quality/feed failure may add at most 24 hours; an unmeasurable lane after that counts as **$0 deployable capacity**. Do not extend because results are interesting or disappointing.

Pre-committed project verdict thresholds, which must not move after seeing results:

- **<$50/day** realistically deployable locked-edge capacity: end dedicated arb engineering and archive the project.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward the $480/day aspiration.

Required measurements: >=1c/>=2c/>=3c after fees, exact executable depth, p50/p90/max lifetime, quote skew, overlap-adjusted capacity, and latency-floor-filtered realistic capture. Provisional latency floor is 80ms and may rise to measured production p95 but may not be lowered to rescue capacity. Maker adverse-selection evidence uses public trade-print proxy, not live census orders.

Same-venue Gemini/Polymarket/Kalshi zero results are near-verdict evidence; do not spend engineering time manufacturing same-venue ladder violations.

## Current authoritative scoreboard

- **2026-08-25 ET:** fresh direct Neon query at 10:40 ET confirms **0 `TRADE_REALIZED` rows / $0.0000 realized net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** last verified direct Neon query confirms **0 weather-tagged `TRADE_REALIZED` rows / $0.0000 verified realized weather P&L**. Seasonal/backtest edge remains excluded.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

PR **#142** (`feature/kp-kalshi-rest-census-fallback`) is now **merged** after two GitHub Actions test runs completed **SUCCESS**. The change is read-only: websocket remains the default, while explicit `KALSHI_REST_BOOKS_FALLBACK=1` switches only the Kalshi census book store to authenticated batched REST snapshots. It has a hard **>=100 ms interval floor**, batches up to 100 tickers/request, bounded retry/backoff, emits existing update events, and contains no order transport.

Railway initially **SKIPPED** merge commit `a20fcba4b2efb30a9d462085629b38296177c801` because `src/eventing_books.py` was missing from the service watch patterns. That production wiring bug is fixed: `src/eventing_books.py` is now watched, with `restartPolicy=ALWAYS` preserved.

Production variables now explicitly include:

- `KALSHI_REST_BOOKS_FALLBACK=1`
- `KALSHI_REST_BOOKS_INTERVAL_MS=100`

Replacement Railway deployment **`13bed315-8fcd-4221-a5f2-911124d15401` is SUCCESS** on commit `a20fcba4...`. The active command remains:

`python -m src.polymarket_kalshi_shadow_v5 --max-quote-age-ms 100`

The predeploy gate compiles `polymarket_kalshi_shadow_v5.py`, `polymarket_kalshi_shadow_v4.py`, and `eventing_books.py`. The service variable list confirms the REST fallback variables are attached to production.

Fresh runtime-log inspection is temporarily blocked by Railway Agent quota (`Agent usage limit reached`), so **do not yet claim `EVENT_READY`, exact Kalshi snapshot counts, or any >=1c/>=2c/>=3c census bins from the new deployment**. The 72-hour K<->P verdict clock remains **INVALID until both sides are proven healthy and measurable**.

The last verified pre-fallback inventory/feed state remains:

- 45 Polymarket MLB total candidates
- 172 Kalshi candidates
- 42 exact cross-venue matches
- 42/42 Polymarket books seeded successfully through `gateway.polymarket.us`
- Polymarket websocket connected
- separate Kalshi websocket 0/42 because repeated HTTP 429 handshakes

Authenticated Kalshi `/account/limits` remains advanced tier, **900 capacity / 300 refill** for both reads and writes. At 42 matched tickers, the new REST census uses one <=100-ticker batch per >=100 ms cycle, intentionally avoiding the failing second websocket handshake while leaving the healthy G<->K connection untouched.

K<->P still needs event-driven coverage for full-game MLB **moneylines, spreads and totals**, plus strict player props only where lifecycle/participation/result-source equivalence can be proven. Semantic mirrors must be deduped. Participation-conditional or rule-mismatched rows never count as guaranteed arb.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Railway deployment `030a6f3d-f40c-48ba-8337-d53285cff801` remains the latest known **SUCCESS** deployment and the production command remains the read-only event-driven `src.gemini_kalshi_mlb_prop_hot_shadow` collector. The last verified feed state had **272 Kalshi tickers / 272 initialized** while K<->P's separate websocket was rejected.

Recent positive evaluations are census/shadow observations only. The explicit execution blocker remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`; G<->K remains disarmed and Caleb's -$0.44 remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** in fresh Railway project state. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is real, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

Authoritative central-ledger evidence remains **0 weather-tagged realized trades / $0 verified realized P&L since March 1, 2026**. The separate weather project may have historical/seasonal or backtest evidence, but none of that may be substituted for realized P&L without an authoritative settlement record.

## Highest-value next actions

1. **Do not start the K<->P 72-hour clock yet.** Verify the new REST-fallback deployment actually initializes Kalshi snapshots and reaches measurable two-sided state first.
2. At the first available runtime-log window, capture fallback transport, request/error/snapshot counters, exact initialized tickers, `EVENT_READY`, and >=1c/>=2c/>=3c after-fee observations. REST-sampled lifetimes must be labeled at their >=100 ms sampling resolution rather than presented as websocket lifetimes.
3. Persist q1 depth, quote skew, OPEN/CLOSE lifetimes and >=80 ms realistic-capture capacity once both books are healthy.
4. Before Wednesday, broaden K<->P to semantic-deduped MLB moneylines/spreads/totals plus strict rule-certified props. Participation-conditional/rule-mismatched rows remain excluded.
5. Continue G<->K read-only census. Its current hot observations remain non-deployable until full lifecycle/rule fingerprint certification is satisfied.
6. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
7. Keep authoritative accounting direct: as of this checkpoint Aug 25 ET remains **0 realized trades / $0.00**, and weather since March remains **$0 verified realized**.
