# ARB Control Plane

Last verified: **2026-08-26 12:31 UTC / 2026-08-26 08:31 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- **K<->P production is genuinely alive.** `sports-kp-live-executor` remains Railway `SUCCESS`; the last hour has 61/61 metric samples with average CPU ~0.0865, nonzero RX/TX, and stable memory. Effective start command is the read-only `src.polymarket_kalshi_shadow_v5_gateway` with the 100ms quote-age ceiling and bounded REST fallback. No live order authority was added.
- The latest decision-grade strict MLB-total result remains **53,237 comparable evaluations / 1,661 decision observations / 9 semantic keys**, with after-fee bins **1,031 >=1c / 0 >=2c / 0 >=3c** and provisional >=80ms realistic-capture bins **137 / 0 / 0**. Average selected-side q1 depth remains **~2,073**, average quote skew **~45.5ms**, and descriptive positive-signal lifetime **~174ms avg / ~567ms max**. The certified totals slice therefore still contributes **$0/day deployable capacity at the fixed 3c floor**.
- Clean K<->P full-game breadth remains **97 semantic groups** with taker reference **0 positive / 0 >=1c / 0 >=2c / 0 >=3c**. Conservative maker feasibility remains **one >=1c moneyline row, 0 >=2c, 0 >=3c**. The optimistic zero-Kalshi-maker-fee 3.5c upper-bound row still has no fill model/rule certification and receives **$0 capacity**.
- **Strict K<->P player props now fail the bounded certification check conclusively for this census window.** The production rule registry explicitly keeps Polymarket incomplete because Kalshi uses a 48-hour-from-scheduled-start lifecycle while Polymarket uses a two-calendar-day reschedule window, Polymarket post-game stat-correction semantics are not explicit, and strikeout participation semantics differ (`starting_pitcher_and_batter_faced` vs `starting_pitcher`). No prop family can currently be marked `CERTIFIED`/exact-settlement-equivalent, so strict props receive **$0 guaranteed-arb capacity** rather than triggering more open-ended research.
- **G<->K remains a read-only hot shadow.** `sports-gk-autonomous-supervisor` is Railway `SUCCESS`; the last hour shows substantial sustained activity (average CPU ~0.505, nonzero network). Its effective start command is `src.gemini_kalshi_mlb_prop_hot_shadow`; no live executor entrypoint is configured. Prior hot signals therefore remain census-only. Caleb Durbin stays **-$0.44 REALIZED**.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**. Latest event of any type remains **2026-08-25 01:52:17 UTC**.
- Fresh authoritative weather query: **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01**. Seasonal/historical weather edge is not credited as current realized performance.
- The old Gemini<->Kalshi 15m crypto executor remains Railway **CRASHED/contained**. Its bounded-campaign failure is proven, but settlement-index equivalence is not; do not reopen it as guaranteed arbitrage.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. Railway's deeper runtime agent is temporarily quota-blocked this run, so no new account-limit read was fabricated and no unrelated Kalshi service/backoff was changed.
- The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and continues with **no reset or pause**. Missing/unmeasurable/rule-mismatched families contribute $0 for the relevant interval.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Fixed verdict protocol

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Data/feed quality may add at most one **+24 hour** extension. An unmeasurable lane after that contributes **$0 deployable capacity**. Thresholds do not move after seeing results.

Required evidence remains explicit >=1c/>=2c/>=3c after-fee bins, exact executable q1 depth, quote skew, source quality, valid signal lifetime, semantic dedupe, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

## Current authoritative scoreboard

- **2026-08-26 ET:** **0 TRADE_REALIZED / $0.0000 net**.
- Caleb Durbin: **-$0.44 REALIZED one-leg sports loss**.
- Weather since **2026-03-01:** **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized**.
- Same-Gemini crypto prior snapshot: roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Cross-venue census

### Kalshi<->Polymarket strict MLB totals

Latest cumulative verified decision-grade evidence:

- **53,237 comparable evaluations**;
- **1,661 decision-census observations** across **9 semantic keys**;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth **~2,073**, max **~22,648**;
- average quote skew **~45.5ms**;
- descriptive positive-signal lifetime **~174ms avg / ~567ms max**;
- REST-backed Kalshi observations remain labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Interpretation: displayed hedge liquidity is not the blocker. Certified totals have not produced a >=2c or >=3c after-fee observation and currently support **$0/day deployable capacity at 3c**.

### K<->P MLB moneyline/spread/game-total breadth

The scheduled-start timezone bug and partial-game/F5 contamination are removed. Current clean breadth has **97 semantic groups** with **0 positive / 0 >=1c / 0 >=2c / 0 >=3c** on the taker reference. Conservative maker feasibility has **one >=1c moneyline row and zero >=2c/>=3c rows**; an optimistic zero-maker-fee upper bound is not execution evidence and receives $0 capacity.

### Strict K<->P player props

The existing bounded rule audit is sufficient for the census verdict. Kalshi/Polymarket profiles are not lifecycle-identical: exceptional-event clocks differ, Polymarket stat-correction semantics remain incomplete, and pitcher participation wording differs. Therefore **no strict prop family is exact-settlement-certified** and props contribute **$0 guaranteed capacity** for this decision window.

### Gemini<->Kalshi sports

Guaranteed-arb order entry remains disarmed/research-only. The active service is the hot-shadow process, not the mutation executor. Caleb remains **-$0.44 REALIZED**. Any observed hot signals remain $0 guaranteed capacity until the full Gemini prop lifecycle fingerprint is certified.

### Weather

Authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical/seasonal edge remains separate and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the fixed 72-hour wall clock running with no reset. Current certified K<->P taker capacity remains **$0/day at >=3c**; conservative maker evidence also has **0 >=3c rows**.
2. Continue clean time-weighted snapshots through the committed window and compute overlap-adjusted realistic capacity. Do not lower the 3c floor.
3. Treat strict K<->P player props as **$0 capacity for this census window** unless genuinely new authoritative rule text resolves every existing lifecycle mismatch; do not open another certification project merely because candidate counts are interesting.
4. Keep G<->K guaranteed execution disarmed and Caleb's -$0.44 loss realized.
5. Preserve verified Kalshi account headroom/backoff and do not disrupt unrelated services solely to improve telemetry.
6. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
7. Keep AWS latency/maker-routing work isolated from this control-plane workstream.