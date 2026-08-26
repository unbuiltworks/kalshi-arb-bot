# ARB Control Plane

Last verified: **2026-08-26 11:46 UTC / 2026-08-26 07:46 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- **K<->P production remains alive.** `sports-kp-live-executor` and `sports-generic-counterpart-shadow` are both Railway `SUCCESS`. No live order authority was added.
- The decision-grade strict MLB-total census remains **53,237 comparable evaluations / 1,661 decision observations / 9 semantic keys**, with after-fee bins **1,031 >=1c / 0 >=2c / 0 >=3c** and provisional >=80ms realistic-capture bins **137 / 0 / 0**. Average selected-side q1 depth remains **~2,073**, average quote skew **~45.5ms**, and descriptive closed-signal lifetime **~174ms avg / ~567ms max**. Transport triggers continue rising, but this strict semantic sample is not rotating into new comparable totals; that stagnant interval is not re-labeled as new zero-edge evidence.
- The clean full-game MLB breadth path remains **54 Polymarket events / 67 Kalshi events / 258 physical comparable pairs / 97 semantic groups**, with taker reference **0 positive / 0 >=1c / 0 >=2c / 0 >=3c**.
- **New read-only K<->P maker-feasibility evidence is now live.** The census explicitly models quoted maker -> taker-hedge feasibility but has **no fill model and no execution authority**. It remains `RULE_INCOMPLETE` and receives **$0 deployable-capacity credit**.
  - Polymarket-maker conservative rows: moneyline **26 rows, 0 >=1c / 0 >=2c / 0 >=3c**, spread **26 rows, 0 / 0 / 0**, game total **45 rows, 0 / 0 / 0**.
  - Kalshi-maker conservative rows: moneyline **26 rows, 1 >=1c / 0 >=2c / 0 >=3c**, with best modeled conservative profit **+1.5c**; spread **26 rows, 0 / 0 / 0**; game total **45 rows, 0 / 0 / 0**.
  - A deliberately optimistic **zero-Kalshi-maker-fee upper bound** produces one moneyline row >=3c, max **+3.5c**, but this is not executable/fill evidence and is assigned **$0 capacity**. Under the conservative current model there are **zero >=3c maker rows** across all 97 semantic groups.
- This strengthens the current economic diagnosis: **edge magnitude, not displayed hedge depth, is the limiting factor** for the measured K<->P full-game universe. Do not convert the optimistic zero-fee upper bound into capacity or use it to justify live maker engineering in this control-plane workstream.
- G<->K sports remains **read-only/disarmed**. Fresh heartbeat: **120,836 comparable evaluations, 6 >=3c hot evaluations, 4 complete signal lifecycles, 0 active positive keys**; Kalshi is **570/570 initialized**, while Gemini currently reports **0 connected symbols**. The rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`; `order_entry=false`. Those hot signals receive **$0 guaranteed-arb capacity**.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**. The latest ledger event of any type remains **2026-08-25 01:52:17 UTC**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Fresh authoritative weather query: **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01**. Seasonal/historical weather edge is not credited as current realized performance.
- The old Gemini<->Kalshi 15m crypto executor remains **CRASHED/contained**. Do not reopen it as guaranteed arbitrage; its bounded-campaign failure is proven but settlement-index equivalence remains unproven.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. No unrelated Kalshi service was stopped and no backoff was weakened.
- The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and continues with **no reset or pause**. Invalid/unmeasurable/rule-mismatched families contribute $0 for the relevant interval. The fixed verdict thresholds remain unchanged.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Edge-census protocol

The fixed verdict thresholds are:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Data/feed quality may add at most one **+24 hour** extension. An unmeasurable lane after that contributes **$0 deployable capacity**. Thresholds do not move after seeing results.

Required evidence remains explicit >=1c/>=2c/>=3c after-fee bins, exact executable q1 depth, quote skew, source quality, valid signal lifetime, semantic dedupe, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

Same-venue Gemini/Polymarket/Kalshi zero results remain near-verdict evidence. Do not spend engineering time manufacturing ladder violations.

## Current authoritative scoreboard

- **2026-08-26 ET:** **0 TRADE_REALIZED / $0.0000 net**.
- Caleb Durbin: **-$0.44 REALIZED one-leg sports loss**.
- Weather since **2026-03-01:** **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized**.
- Same-Gemini crypto prior snapshot: roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Cross-venue census

### Kalshi<->Polymarket strict MLB totals

Latest cumulative verified decision-grade totals evidence:

- **53,237 comparable evaluations**;
- **1,661 decision-census observations** across **9 semantic keys**;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth **~2,073**, max **~22,648**;
- average quote skew **~45.5ms**;
- descriptive positive-signal lifetime **~174ms avg / ~567ms max**;
- REST-backed Kalshi transport is labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Interpretation: liquidity is not the blocker. This certified totals slice has not produced a >=2c or >=3c after-fee observation and currently contributes **$0/day deployable capacity at the fixed 3c floor**.

### K<->P MLB moneyline/spread/game-total breadth

The scheduled-start timezone bug and partial-game/F5 contamination are removed from the active read-only breadth path. Current clean universe:

- **54 Polymarket events / 67 Kalshi events / 258 physical comparable pairs / 97 semantic groups**;
- taker reference: **0 positive / 0 >=1c / 0 >=2c / 0 >=3c**;
- current maker-feasibility conservative model: **one >=1c moneyline row, zero >=2c and zero >=3c across all families**;
- optimistic zero-maker-fee Kalshi upper bound: **one >=3c moneyline row, max +3.5c**, but no fill model/rule certification and therefore **$0 capacity credit**.

The generic breadth lane remains explicitly rule-incomplete/read-only. Neither taker nor conservative quoted-maker evidence currently supports >=3c deployable capacity.

### Strict K<->P player props

Only families with complete settlement/postponement/cancellation/participation/stat-correction equivalence may enter guaranteed-arb capacity. No player-prop family currently has that complete certification. Under the fixed protocol, uncertified prop families contribute **$0 capacity** rather than triggering open-ended engineering.

### Gemini<->Kalshi sports

Guaranteed-arb order entry remains disarmed/research-only. Caleb is settled **-$0.44 REALIZED**. Fresh hot-shadow state has **6 >=3c evaluations / 4 lifecycles**, but the full Gemini prop lifecycle fingerprint is not certified and Gemini currently reports zero connected symbols; capacity credit remains **$0**.

### Weather

Authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the fixed 72-hour wall clock running with no reset. Current certified K<->P taker capacity remains **$0/day at >=3c**; the new conservative maker-feasibility snapshot also has **0 >=3c rows**.
2. Continue clean time-weighted snapshots through the committed window and compute overlap-adjusted realistic capacity. Do **not** lower the 3c floor and do not build a live maker engine in this control-plane lane merely because the zero-fee upper bound shows one 3.5c quote.
3. Give strict K<->P player props only the existing bounded certification check. If complete lifecycle equivalence is not proven inside the current census window, score props at **$0 capacity**.
4. Keep G<->K guaranteed execution disarmed and Caleb's -$0.44 loss realized.
5. Preserve Kalshi account headroom/backoff and the verified advanced **900 bucket / 300 refill** limits.
6. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
7. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
