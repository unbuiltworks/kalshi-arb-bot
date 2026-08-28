# ARB Control Plane

Last verified: **2026-08-28 10:48 UTC / 2026-08-28 06:48 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset. Data/feed quality may add at most one +24-hour allowance; a lane that remains unmeasurable after that allowance contributes **$0 deployable capacity**. The realistic-capture floor remains **>=80 ms** and may not be lowered to rescue the result.

## Fresh production state — 06:48 ET

- `sports-kp-live-executor`: latest deployment remains **SUCCESS**. Fresh 61-sample one-hour Railway metrics show sustained activity: CPU average **0.0683** (current **0.0375**), memory average **0.0438 GB**, network RX average **0.00423 GB/sample**, network TX average **0.00210 GB/sample**. This is positive runtime-health evidence, not economic evidence.
- `sports-generic-counterpart-shadow`: latest deployment remains **SUCCESS**. Fresh one-hour metrics show CPU average **0.0268**, memory average **0.0801 GB**, and nonzero RX/TX across the hour. The instantaneous CPU/network sample was zero, but the 61-sample trace shows recent operation; do not infer collector death from a single idle sample.
- `sports-gk-autonomous-supervisor`: latest deployment remains **SUCCESS**. Fresh one-hour metrics show heavy sustained activity: CPU average **0.7536**, memory average **1.4531 GB**, RX average **0.11925 GB/sample**, TX average **0.06490 GB/sample**. The instantaneous CPU/network sample was zero while memory remained allocated; the one-hour trace confirms recent operation.
- Railway's detailed log agent is currently quota-blocked (`Agent usage limit reached`), so no newer decision counters are promoted from resource metrics alone. Last decoded economics below remain authoritative for this census run.
- `sports-kp-live-executor` active successful deployment is still the **2026-08-25** build at commit `c1b0aaf4e8d54199fd33d7634ea7e1ae0c3ccded` (`Fix OVER/UNDER depth telemetry mapping`). Later Aug. 26 deployment attempts on this service, including delayed-hedge instrumentation, are **SKIPPED**.
- The delayed Polymarket hedge instrumentation commit `044204d31c98a098a180e726899162180556ef63` instead successfully deployed on the separate read-only `sports-generic-counterpart-shadow`. This preserves provenance: maker/delayed-hedge evidence was not silently mixed into the taker runtime.
- Last decoded **09:39:51 UTC** taker heartbeat: `triggers=136,904,107`, `comparable_evaluations=53,237`, `decision_census.observations=1,661`, `read_only=true`, `order_entry=false`.
- Last inspected runtime logs showed repeated `GET .../trade-api/v2/markets/orderbooks` responses at **HTTP 200** using the legacy `https://api.elections.kalshi.com` host, at roughly **0.1-second cadence**. No current 429 appeared in that decoded slice.
- Prior authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for reads and writes. Current evidence continues to favor aggregate/concurrent request or connection contention rather than a weak account tier as the earlier 429 cause.
- Do **not** rewrite, restart, throttle, or migrate the functioning census service solely to improve telemetry before the fixed deadline. New census code should prefer Kalshi's dedicated external-api/external-api-ws hosts.
- `sports-generic-counterpart-shadow`: read-only maker/fill/delayed-hedge telemetry only.
- `sports-gk-autonomous-supervisor`: remains read-only/disarmed for guaranteed execution; last decoded two-sided state is Gemini **0/570 connected** vs Kalshi **570/570 initialized**.
- Old Gemini<->Kalshi cross-index/15-minute crypto execution remains **CRASHED/contained** and must not be reopened from census pressure.

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

The comparable sample has failed to advance for well beyond the allowed data-quality grace. Under the pre-committed rule, the stale tail is **locked at $0 deployable-capacity credit**. The valid certified totals slice itself has **0 >=3c**, hence **$0/day deployable capacity at the fixed 3c taker/taker floor**. Raw-trigger growth is health/activity evidence only and is not misrepresented as fresh economic observations.

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
- delayed usable cohort has **0 depth-insufficient, 0 hedge-missing, 0 full-ladder-missing** failures;
- exact K<->P full-game moneyline postponement/reschedule lifecycle equivalence fails independently;
- `rules_complete=false`, `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

Maker shadow remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. These measurements come from the separate read-only counterpart-shadow deployment, not from the taker executor. Do not spend the remaining census window repairing delayed-WS coverage because even perfect observability would not make the rule-mismatched mechanism guaranteed arb.

Polymarket incentive telemetry last saw **22 active MLB moneyline program rows** and **0 under-target live-pool candidates**; denominator/time-weighted yield/pickoff economics remain incomplete. Incentive capacity credit stays **$0**.

## G<->K sports

Last decision-valid state remains:

- comparable evaluations: **120,836** (frozen);
- historical positive/hot evidence: **6 positive / 6 >=3c hot evaluations, 4 completed lifecycles**, uncertified;
- Gemini symbols: **570**, connected: **0**;
- Kalshi tickers: **570**, initialized: **570/570**;
- rule gate: `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`;
- `read_only=true`, `order_entry=false`.

This lane has remained two-sided-unmeasurable beyond the permitted data-quality grace. Its census contribution is therefore **locked at $0 deployable capacity**. Fresh resource metrics confirm the supervisor process has been active during the last hour, but they do not repair the missing two-sided Gemini state. Do not re-arm or repair connectivity merely to rescue the census. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Authoritative accounting and weather

The last fresh attempt to query the authoritative Neon project `arb-trade-ledger` failed **before SQL execution** with HTTP **402: data transfer quota exceeded**. Accounting is therefore **not advanced speculatively**.

Last successful authoritative reconciliation remains:

- **2026-08-27 ET: 0 ledger events, 0 TRADE_REALIZED, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged TRADE_REALIZED / $0.0000 since 2026-03-01**;
- Caleb Durbin: prior `SETTLED_ONE_LEG`, **-$0.44 REALIZED**.

Seasonal/backtest/shadow weather edge remains separate from verified current realized performance and receives no census credit.

## Current verdict trajectory

Guaranteed/deployable evidence at the fixed 3c floor:

- certified K<->P totals: **0 >=3c => $0/day**;
- clean full-game taker/taker: **0 >=3c => $0/day**;
- stale K<->P comparable tail: grace exhausted => **$0 capacity credit**;
- K<->P maker: weak/negative realistic delayed economics plus lifecycle mismatch => **$0/day guaranteed capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: two-sided unmeasurable beyond grace and disarmed => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at last authoritative reconciliation.

Current certified evidence is therefore strongly in the pre-committed **<$50/day** bucket. Do not move thresholds or declare the final verdict before **16:43 ET today**.

## Highest-value next actions

1. Keep the fixed census running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Do **not** repair stale K<->P comparables, Gemini reconnects, maker delayed-WS coverage, incentive denominator plumbing, or Neon quota solely to extend the census; zero-credit treatment is already determined where applicable.
3. Keep G<->K guaranteed execution disarmed and preserve Caleb's -$0.44 realized loss.
4. Preserve Kalshi account headroom/backoff and do not disrupt unrelated live services. Treat the observed ~0.1s legacy-host polling cadence as contention evidence to address only in future code if the project survives the fixed verdict.
5. Keep weather historical/backtest edge separate from verified live realized P&L.
6. At the fixed deadline, aggregate only rule-certified, realistically executable capacity and apply **<$50 / $50-$149 / >=$150** exactly as committed.
