# ARB Control Plane

Last verified: **2026-08-28 21:32 UTC / 2026-08-28 17:32 ET**

## FINAL 72-hour census verdict — binding

Primary aspiration was **$480/day REALIZED net**. Only authoritative settlement/reconciliation counts as REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

The pre-committed 72-hour census ran from **2026-08-25 20:43 UTC / 16:43 ET** through **2026-08-28 20:43 UTC / 16:43 ET**. The deadline has passed. The verdict thresholds were fixed before seeing the result:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

### FINAL RESULT: **$0/day certified realistic deployable capacity at the fixed 3c floor**

This is in the pre-committed **<$50/day** bucket. Therefore **dedicated arbitrage engineering ends and the ARB project moves to archived/maintenance-only status**. Do not extend the census because the results are interesting. The optional +24-hour data-quality allowance is not invoked: every lane with unresolved measurement or feed quality already has a pre-committed zero-credit treatment, so additional observation time cannot improve the binding decision without changing the rules after seeing the data.

No unrelated live service is being disrupted solely to improve census telemetry. Existing collectors may remain online for evidence preservation/other work, but their continued runtime does **not** reopen this census or authorize new dedicated ARB engineering.

## Fresh production state after cutoff

Fresh Railway one-hour metrics at the final adjudication confirm the two K<->P census processes are still running:

- `sports-kp-live-executor`: average CPU **0.07011**, average RX **0.004303 GB/sample**, average TX **0.002138 GB/sample**.
- `sports-generic-counterpart-shadow`: average CPU **0.03870**, average RX **0.001362 GB/sample**, average TX **0.000354 GB/sample**.

This is runtime-health evidence only. It does not create additional economic/capacity credit after the cutoff.

`crypto-live-balance-reconcile-v2` remains non-authoritative: the last verified runtime failed on `ModuleNotFoundError: No module named 'src.gemini_kalshi_probe_transport'`, and later Railway deployment-success badges did not produce verified reconciliation output. G<->K therefore stays disarmed.

Prior authenticated Kalshi account limits remain **advanced tier, 900 bucket / 300 refill**. Earlier 429 evidence is more consistent with aggregate/concurrent request or connection contention than a weak account tier. Do not disrupt unrelated live services to improve telemetry; any future read-only census work should use Kalshi dedicated external-api/external-api-ws hosts.

## K<->P taker — final decision-grade evidence

Final decision-valid decoded sample:

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

The comparable sample stopped advancing beyond the allowed data-quality grace. Per the pre-committed rule, the stale tail contributes **$0 deployable-capacity credit**. The valid certified slice has **0 >=3c**, so taker/taker contributes **$0/day** at the fixed 3c floor.

After scheduled-start normalization and F5/partial-game exclusion, clean full-game MLB moneyline/spread/total snapshots are **0 >=1c / 0 >=2c / 0 >=3c** with substantial q1 depth. Strict player props contribute **$0 guaranteed capacity** because complete settlement/postponement/cancellation/participation/stat-correction equivalence was not proven. Participation-conditional and rule-mismatched rows are not counted as guaranteed arbitrage. Semantic mirrors are deduped rather than counted as separate capacity.

## K<->P maker/fill/delayed-hedge shadow — final treatment

Last decision-valid maker evidence:

- simulated q1 fills: **2,166**;
- immediate maker-fee cumulative bins: **1,378 >=1c / 1,076 >=2c / 871 >=3c**;
- immediate shadow P&L: **+$128.6480** — descriptive only, never REALIZED;
- median quote age at fill: **994 ms**;
- only **134 / 2,166 (6.19%)** have decision-usable delayed hedge observations;
- **2,032** are WS-unhealthy/unobservable and receive $0 capacity credit;
- at **80 ms**: **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- delayed usable cohort: **0 depth-insufficient, 0 hedge-missing, 0 full-ladder-missing**;
- exact K<->P full-game moneyline postponement/reschedule lifecycle equivalence fails independently;
- `rules_complete=false`, `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

Maker shadow therefore contributes **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. The positive immediate shadow headline is not promoted into the verdict because realistic delayed hedge economics are weak/negative and lifecycle equivalence independently fails.

Polymarket incentive capacity is **$0**: last telemetry saw **22 active MLB moneyline program rows** and **0 under-target live-pool candidates**, while denominator/time-weighted yield/pickoff economics remain incomplete.

## G<->K sports — final treatment

Last decision-valid state:

- comparable evaluations: **120,836** (frozen);
- historical positive/hot evidence: **6 positive / 6 >=3c hot evaluations, 4 completed lifecycles**, uncertified;
- Gemini symbols: **570**, connected: **0**;
- Kalshi tickers: **570**, initialized: **570/570**;
- rule gate: `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`;
- `read_only=true`, `order_entry=false`.

The lane remained two-sided-unmeasurable beyond the permitted grace and therefore contributes **$0 deployable capacity**. **Keep G<->K disarmed** unless unresolved one-leg risk is conclusively cleared in a future, separately justified workstream. Caleb Durbin remains **-$0.44 REALIZED** and may not be shadowed away or reclassified.

## Same-venue lanes

Same-venue Gemini, Polymarket and Kalshi ladder-violation zeros are retained as near-verdict evidence. Do not spend dedicated engineering time trying to manufacture same-venue violations after this verdict.

## Authoritative accounting and weather

Fresh crypto balance reconciliation remains blocked by the read-only reconciler regression. The last Neon ledger query attempt also failed before SQL execution with HTTP **402 data transfer quota exceeded**. Do not advance accounting speculatively.

Last successful authoritative reconciliation:

- **2026-08-27 ET: 0 ledger events, 0 TRADE_REALIZED, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged TRADE_REALIZED / $0.0000 since 2026-03-01**;
- Caleb Durbin: prior `SETTLED_ONE_LEG`, **-$0.44 REALIZED**.

Weather seasonal/backtest/shadow edge is historical/model evidence only. It is separate from current realized performance and receives no ARB census credit.

## Final capacity aggregation

At the fixed 3c floor and realistic-capture/rule-certification gates:

- certified K<->P taker/taker: **$0/day**;
- clean full-game MLB taker/taker: **$0/day**;
- stale K<->P comparable tail: **$0** by exhausted-grace rule;
- K<->P maker: **$0/day guaranteed deployable capacity**;
- strict props/incentives: **$0** uncertified/incomplete;
- G<->K: **$0** unmeasurable beyond grace and disarmed;
- same-venue ladder violations: **$0**;
- weather verified realized since March: **$0** at last authoritative reconciliation.

**FINAL CERTIFIED REALISTIC DEPLOYABLE CAPACITY: $0/day.**

**FINAL PRE-COMMITTED VERDICT: <$50/day => ARCHIVE dedicated ARB engineering.**

## Post-verdict operating policy

1. Do not reopen or extend this census without a genuinely new economic premise; additional telemetry on the same exhausted lanes is not sufficient.
2. Keep G<->K disarmed; preserve Caleb's **-$0.44 REALIZED** loss.
3. Never promote runtime health, raw-trigger growth, stale quotes, shadow activity, mismatched rules, unconfirmed fills or one-leg exposure into REALIZED P&L or deployable capacity.
4. Do not spend dedicated engineering time repairing same-venue zeros, stale K<->P comparables, maker delayed-WS coverage, Gemini reconnects, incentive denominator plumbing, or census-only telemetry.
5. Preserve unrelated live services. Any future work must be justified independently by evidence of executable/realized dollars per day, not candidate count or infrastructure elegance.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. The **$480/day aspiration is not supported by this dedicated arbitrage census**. Reaching that income target requires a different strategy/economic premise rather than relaxing this census's rules.
