# ARB Control Plane

Last verified: **2026-08-28 20:36 UTC / 2026-08-28 16:36 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour census started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. No reset. Data/feed quality may add at most one +24-hour allowance; a lane still unmeasurable after that allowance contributes **$0 deployable capacity**. Realistic-capture floor remains **>=80 ms**.

## Fresh production state — 16:36 ET

- `sports-kp-live-executor`: fresh last-hour Railway metrics confirm sustained runtime activity: average CPU **0.06948**, average RX **0.004283 GB/sample**, average TX **0.002126 GB/sample**. This is runtime-health evidence only and creates no additional economic/capacity credit.
- `sports-generic-counterpart-shadow`: fresh last-hour metrics likewise confirm sustained activity: average CPU **0.03858**, average RX **0.001322 GB/sample**, average TX **0.000347 GB/sample**. Shadow activity never counts as REALIZED P&L or deployable capacity by itself.
- `sports-gk-autonomous-supervisor`: remains active, averaging CPU **0.79105**, RX **0.118415 GB/sample**, TX **0.064410 GB/sample** over the last hour, but no fresh certified two-sided state has displaced the last decoded Gemini **0/570 connected** vs Kalshi **570/570 initialized** state. Runtime/network activity does not rescue the lane's exhausted measurability grace.
- `crypto-live-balance-reconcile-v2`: fresh last-hour metrics remain exactly **0 CPU / 0 memory / 0 RX / 0 TX**. Prior runtime verification showed immediate `ModuleNotFoundError: No module named 'src.gemini_kalshi_probe_transport'`. No authoritative crypto balance/exposure reconciliation exists from this service; G<->K remains disarmed.
- `sports-kp-live-executor` deployment provenance remains unchanged: newest deployment records through Aug 26 are explicitly **SKIPPED**, including delayed-hedge commit `044204d31c98a098a180e726899162180556ef63`; no hidden newer taker build is promoted into decision evidence.
- Canonical weather project `kalshi-weather-bot`: last verified main bot **SUCCESS**, weather shadow **SUCCESS**, weather backtest **SUCCESS**, canonical `weather-live-probe` **CRASHED**. A same-named service inside the sports project showing SUCCESS is not treated as authoritative weather-live evidence.
- Prior authenticated Kalshi account limits remain **advanced tier, 900 bucket / 300 refill**. Last inspected K<->P polling used legacy `api.elections.kalshi.com` at roughly 0.1-second cadence with HTTP 200. Earlier 429 evidence continues to favor aggregate/concurrent request or connection contention over a weak account tier. Do not disrupt unrelated live services solely to improve census telemetry; new census code should prefer Kalshi dedicated external-api/external-api-ws hosts.

## K<->P decision-grade taker evidence

Last decision-grade decoded sample remains:

- raw triggers: **136,904,107**;
- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- >=80 ms realistic-capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth: **2,072.66**, max **22,647.58**;
- average quote skew: **45.53 ms**, max **100 ms**;
- average process latency: **0.234 ms**;
- closed positive signals: **612**;
- average closed lifetime: **174.07 ms**, max **567 ms**;
- source quality: `REST_SNAPSHOT_NOT_WS_LIFETIME`;
- `kalshi_transport=rest_batch`, `read_only=true`, `order_entry=false`.

Comparable sample failed to advance beyond allowed data-quality grace. The stale tail is therefore **$0 deployable-capacity credit**. The valid certified totals slice has **0 >=3c**, hence **$0/day deployable capacity at the fixed 3c taker/taker floor**. Raw-trigger growth is activity evidence only.

After scheduled-start normalization and F5/partial-game exclusion, clean full-game MLB moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c** with substantial q1 depth. Strict player props remain $0 guaranteed capacity because complete settlement/postponement/cancellation/participation/stat-correction equivalence has not been proven.

## K<->P maker/fill/delayed-hedge shadow

Last decision-valid maker evidence remains:

- simulated q1 fills: **2,166**;
- immediate maker-fee cumulative bins: **1,378 >=1c / 1,076 >=2c / 871 >=3c**;
- immediate shadow P&L: **+$128.6480** — descriptive only;
- median quote age at fill: **994 ms**;
- only **134 / 2,166 (6.19%)** have decision-usable delayed hedge observations;
- **2,032** are WS-unhealthy/unobservable and receive $0 capacity credit;
- at **80 ms**: **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- delayed usable cohort: **0 depth-insufficient, 0 hedge-missing, 0 full-ladder-missing**;
- exact K<->P full-game moneyline postponement/reschedule lifecycle equivalence fails independently;
- `rules_complete=false`, `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

Maker shadow remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. Do not repair delayed-WS coverage merely to rescue the census because rule-equivalence failure independently prevents guaranteed-arb certification.

Polymarket incentive capacity remains **$0**: last telemetry saw **22 active MLB moneyline program rows** and **0 under-target live-pool candidates**, while denominator/time-weighted yield/pickoff economics remain incomplete.

## G<->K sports

Last decision-valid state:

- comparable evaluations: **120,836** (frozen);
- historical positive/hot evidence: **6 positive / 6 >=3c hot evaluations, 4 completed lifecycles**, uncertified;
- Gemini symbols: **570**, connected: **0**;
- Kalshi tickers: **570**, initialized: **570/570**;
- rule gate: `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`;
- `read_only=true`, `order_entry=false`.

This lane remained two-sided-unmeasurable beyond the permitted grace and therefore contributes **$0 deployable capacity**. Keep it disarmed. Caleb Durbin remains **-$0.44 REALIZED** and may not be reclassified away.

## Authoritative accounting and weather

Fresh balance reconciliation is blocked by the read-only reconciler import regression above. The last Neon ledger query attempt also failed **before SQL execution** with HTTP **402 data transfer quota exceeded**. Do not advance accounting speculatively.

Last successful authoritative reconciliation:

- **2026-08-27 ET: 0 ledger events, 0 TRADE_REALIZED, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged TRADE_REALIZED / $0.0000 since 2026-03-01**;
- Caleb Durbin: prior `SETTLED_ONE_LEG`, **-$0.44 REALIZED**.

Seasonal/backtest/shadow weather edge remains separate from current verified realized performance and receives no census credit.

## Current verdict trajectory

Guaranteed/deployable evidence at the fixed 3c floor:

- certified K<->P totals: **0 >=3c => $0/day**;
- clean full-game taker/taker: **0 >=3c => $0/day**;
- stale K<->P comparable tail: grace exhausted => **$0**;
- K<->P maker: weak/negative realistic delayed economics plus lifecycle mismatch => **$0/day guaranteed capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: unmeasurable beyond grace and disarmed => **$0**;
- weather verified realized since March: **$0** at last authoritative reconciliation.

Current certified evidence remains strongly in the pre-committed **<$50/day** bucket. Do not move thresholds or declare the final verdict before **16:43 ET today**. The 16:36 ET runtime pulse produced no new certified executable economics and therefore no change in capacity credit.

## Highest-value next actions

1. Keep the fixed census running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Preserve G<->K disarm and Caleb's **-$0.44 REALIZED** loss.
3. Do not treat runtime health, raw-trigger growth, or shadow activity as economic evidence.
4. Do not repair stale K<->P comparables, Gemini reconnects, maker delayed-WS coverage, incentive denominator plumbing, balance reconciler, or Neon quota solely to extend the census; zero-credit treatment is already determined where applicable.
5. Preserve Kalshi account headroom/backoff and avoid disruption of unrelated live services. If the project survives the verdict, future census code should migrate to dedicated external-api/external-api-ws hosts.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. At **16:43 ET**, aggregate only rule-certified, realistically executable capacity and apply **<$50 / $50-$149 / >=$150** exactly as committed.
