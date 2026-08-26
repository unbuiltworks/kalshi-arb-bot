# ARB Control Plane

Last verified: **2026-08-26 02:41 UTC / 2026-08-25 22:41 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- Fresh Railway control-plane verification: `sports-kp-live-executor` remains **SUCCESS**; `sports-generic-counterpart-shadow` remains **SUCCESS** on deployment `f06bb651-8096-4e08-99d0-3e45c80f52de`; `sports-gk-autonomous-supervisor` is **SUCCESS**; the old `crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** and contained.
- Fresh authoritative Neon `arb-trade-ledger` query: **2026-08-25 ET = 0 TRADE_REALIZED / $0.0000 net**, **0 unresolved durable capital reservations**, and **0 weather-tagged TRADE_REALIZED / $0 verified weather P&L since 2026-03-01**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Decision-grade K<->P strict MLB totals remain unchanged: **1,031 >=1c / 0 >=2c / 0 >=3c** after fees; at the provisional >=80ms realistic-capture floor **137 >=1c / 0 >=2c / 0 >=3c**. Measured certified totals capacity remains **$0/day at the fixed 3c floor**.
- PR #152 remains the active read-only MLB game breadth repair. Production continues to prove exact semantic overlap survives physical-representation expansion: the overlap count has declined naturally as the live slate closes (268 earlier, ~214 by 02:40 UTC), but **0 pairs pass the existing <=5 minute scheduled-start gate**, so moneyline/spread/game-total capacity remains $0.
- Source inspection narrows the likely timestamp mismatch: Polymarket normalization prefers `market.gameStartTime`, then event start fields; Kalshi normalization first attempts a YYMONDDHHMM timestamp embedded in `event_ticker`, then falls back to `event.strike_date`, `market.expected_expiration_time`, or `market.close_time`. This remains a hypothesis until raw production fields are measured; **do not loosen the <=5 minute gate**.
- The read-only timestamp diagnosis is now a completed engineering milestone: **PR #153 passed GitHub Actions, became mergeable, and merged** into `feature/sports-kp-live-executor-impl` at `2762e988c6a9571c0d9664c3104508483b9e2ab4`. It reports start-skew min/p50/p90/p99/max plus raw Polymarket and Kalshi timestamp source fields for rejected exact semantic keys and contains no order transport, execution eligibility, rule relaxation, or threshold change.
- Railway correctly **SKIPPED** the #153 merge for `sports-generic-counterpart-shadow` because the new diagnostic file is outside that service's watch patterns. The active #152 breadth collector therefore remained uninterrupted. The diagnostic still needs to be executed through a safe read-only one-off/runtime path; no live service was repointed merely to obtain telemetry.
- Railway's deeper read-only runtime agent is temporarily quota-blocked (`Agent usage limit reached`), but native deployment logs remain available and confirm the active breadth loop is healthy: both Polymarket gateway and dedicated Kalshi `external-api` discovery calls are returning HTTP 200 each cycle.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Edge-census protocol

The 72-hour K<->P wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and is **not paused or reset** by instrumentation defects. Invalid/unmeasurable intervals score **$0 deployable capacity for that wall-clock interval**.

Fixed verdict thresholds remain unchanged:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Data/feed quality may add at most one **+24 hour** extension. An unmeasurable lane after that contributes **$0 deployable capacity**. Thresholds do not move after seeing results.

Required evidence remains explicit >=1c/>=2c/>=3c after-fee bins, exact executable q1 depth, quote skew, source quality, valid signal lifetime, semantic dedupe, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

Same-venue Gemini/Polymarket/Kalshi zero results remain near-verdict evidence. Do not spend engineering time manufacturing ladder violations.

## Current authoritative scoreboard

- **2026-08-25 ET:** **0 TRADE_REALIZED / $0.0000 net**.
- **Unresolved central durable reservations:** **0**.
- Caleb Durbin: **-$0.44 REALIZED one-leg sports loss**.
- Weather since **2026-03-01:** **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized**. Seasonal/historical edge is not substituted for realized performance.
- Same-Gemini crypto prior snapshot: roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Cross-venue census

### Kalshi<->Polymarket strict MLB totals

` sports-kp-live-executor` remains the decision-grade read-only totals collector. Latest cumulative verified totals evidence:

- **53,237 comparable evaluations**;
- **1,661 decision-census observations** across **9 semantic keys**;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth **~2,072.66**, max **22,647.58**;
- average quote skew **~45.53ms**, max **100ms**;
- average local process latency **~0.234ms**;
- **612 closed signals**, descriptive average lifetime **~174.07ms**, max **567ms**;
- Kalshi transport is bounded `rest_batch`, source quality **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Interpretation: liquidity is deep enough, but this certified totals slice has **zero >=2c and zero >=3c after-fee observations**, including zero >=2c/>=3c at the provisional realistic-capture floor. At the fixed 3c taker/taker floor this slice currently contributes **$0/day deployable capacity**.

### K<->P MLB moneyline/spread/game-total breadth

Production #152 initially reported:

- Polymarket events: **50**;
- Kalshi events: **95** (`KXMLBGAME=47`, `KXMLBSPREAD=24`, `KXMLBTOTAL=24`);
- normalized sides: **836 Polymarket / 838 Kalshi**;
- expanded shared semantic keys: **268**;
- pairs passing <=5 minute start skew: **0**;
- book-comparable physical pairs: **0**;
- semantic unique priced groups: **0**;
- capacity credit: **$0/day**;
- classification: **RULE_INCOMPLETE_NORMAL_EVENT_SHADOW_NOT_EXECUTION_ELIGIBLE**.

As games close, current shared-key counts naturally fall (roughly **214 by 02:40 UTC**), but the outcome remains the same: no shared key reaches pricing because of scheduled-start mismatch. The current root cause is **scheduled-start alignment**, not absent semantic overlap. Do not loosen the gate. The merged #153 diagnostic exists only to expose the raw venue fields and skew distribution needed to prove whether one adapter is using expiry/close metadata rather than scheduled first pitch. Moneyline/spread rows remain capacity $0 until both timestamp identity and settlement/postponement/cancellation semantics are certified.

### Gemini<->Kalshi sports

Central ledger reconciliation is clean: Caleb is settled -$0.44 and there are **zero unresolved durable reservations**. Keep G<->K disarmed unless any live canary is independently proven safe; do not count canary/shadow rows without authoritative both-leg fill and final settlement reconciliation. Kalshi secondary websocket 429s remain consistent with concurrent-handshake/contention, not a weak account tier. `/account/limits` remains **advanced**, **900 bucket / 300 refill** for both reads and writes.

### Weather

Authoritative central records still show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running with no resets. Current certified totals evidence remains **$0/day at >=3c**.
2. Execute the now-merged **PR #153** read-only start-skew diagnostic through a safe one-off/runtime path without interrupting the active breadth collector. Measure raw timestamp fields and skew distribution for exact semantic keys; correct only a proven adapter bug and preserve the <=5 minute gate.
3. If timestamps align after a proven normalization fix, immediately price moneyline/spread/game-total physical pairs and report >=1c/>=2c/>=3c, q1 depth, skew, and missing-to-3c. Until then those families remain $0 capacity.
4. Keep rule-incomplete families at capacity $0 until settlement/postponement/cancellation semantics are certified. Add only strict props with provable equivalence; participation-conditional rows remain excluded.
5. Preserve Kalshi account headroom and websocket backoff; do not stop unrelated services for census convenience.
6. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
7. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
