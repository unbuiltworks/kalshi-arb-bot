# ARB Control Plane

Last verified: **2026-08-26 01:55 UTC / 2026-08-25 21:55 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- Re-established authoritative accounting from Neon `arb-trade-ledger`: **2026-08-25 ET = 0 TRADE_REALIZED / $0.0000 net**, **0 unresolved durable capital reservations**, and **0 weather-tagged TRADE_REALIZED / $0 verified weather P&L since 2026-03-01**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- K<->P strict MLB totals remain decision-negative at the fixed 3c taker/taker floor: **1,031 >=1c / 0 >=2c / 0 >=3c**; at the provisional >=80ms realistic-capture floor **137 >=1c / 0 >=2c / 0 >=3c**. Measured totals capacity remains **$0/day** at the fixed 3c floor.
- The read-only MLB game breadth repair passed GitHub Actions and was promoted through **PR #152**, merged to `feature/sports-kp-live-executor-impl` at `58eddb8ad74af8ed56a01b9ca4f84995e547c5c7`. It preserves exact semantic `contract_key` identity and the <=5 minute start-skew gate, while enumerating duplicate physical market+side representations only inside each already-exact shared key. No order transport, live authority, rule relaxation, or threshold change was added.
- Railway `sports-generic-counterpart-shadow` is now **SUCCESS** on deployment `f06bb651-8096-4e08-99d0-3e45c80f52de` running the #152 merge. Fresh production output proves the representation-expansion step is active: **50 Polymarket MLB events / 95 Kalshi events**, **836 / 838 normalized sides**, **268 expanded shared semantic keys**. However, **0 pairs pass the existing <=5 minute scheduled-start gate**, so `book_comparable_physical_pairs=0` and all ML/spread/total capacity remains $0.
- That changes the next root cause: the 268 exact semantic overlaps are no longer being lost to duplicate physical representation. They are being rejected at **scheduled-start alignment** before book pricing. The next bounded diagnostic is to measure the start-skew distribution/source fields for those exact shared keys and correct only a proven timestamp-normalization mismatch; the <=5 minute gate must not be loosened to manufacture matches.
- A PR-number race occurred during promotion: unrelated **PR #151** (`Precommit Aug 28 ARB decision gate`) already occupied that number and was merged before the collision was detected. It contains control-plane/read-only decision-gate work, not order authority. The intended census breadth change is correctly **PR #152**. Do not attribute #151 to the breadth repair.
- Account-level Kalshi evidence remains authoritative: `/account/limits` = **advanced**, **900 bucket / 300 refill** for both reads and writes. Preserve dedicated `external-api` / `external-api-ws` hosts and current backoff; do not disrupt unrelated Kalshi services merely to improve census telemetry.
- Old Gemini<->Kalshi 15m crypto remains **CRASHED/contained** from the previously confirmed bounded deadline -> `HALTED_FAIL_CLOSED` path. It is not reopened because settlement-index non-equivalence remains the stronger blocker.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Edge-census protocol

The 72-hour K<->P wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and is **not paused or reset** by instrumentation defects. Invalid/unmeasurable intervals are scored as **$0 deployable capacity for that wall-clock interval**.

Fixed verdict thresholds remain unchanged:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Data/feed quality may add at most one **+24 hour** extension. An unmeasurable lane after that contributes **$0 deployable capacity**. The thresholds may not move after seeing the results.

Required evidence remains explicit >=1c/>=2c/>=3c after-fee bins, exact executable q1 depth, quote skew, source quality, signal lifetime where valid, semantic dedupe, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

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

Interpretation remains unchanged: liquidity is deep enough, but the measured certified totals slice has **zero >=2c and zero >=3c after-fee observations**, including zero >=2c/>=3c at the provisional realistic-capture floor. At the fixed 3c taker/taker floor this slice currently contributes **$0/day deployable capacity**.

### K<->P MLB moneyline/spread/game-total breadth

Production #152 output now proves exact semantic overlap survives physical-representation expansion:

- Polymarket events: **50**;
- Kalshi events: **95** (`KXMLBGAME=47`, `KXMLBSPREAD=24`, `KXMLBTOTAL=24`);
- normalized sides: **836 Polymarket / 838 Kalshi**;
- expanded shared semantic keys: **268**;
- pairs passing <=5 minute start skew: **0**;
- book-comparable physical pairs: **0**;
- semantic unique priced groups: **0**;
- capacity credit: **$0/day**;
- classification remains **RULE_INCOMPLETE_NORMAL_EVENT_SHADOW_NOT_EXECUTION_ELIGIBLE**.

The important diagnosis is now **timestamp alignment**, not lack of semantic overlap and not physical duplicate canonicalization. `SportsEventIdentity.event_key()` intentionally excludes scheduled time; therefore exact team/family/side/line semantic keys can intersect while start timestamps still disagree. Do not loosen the <=5 minute gate. Diagnose whether Polymarket and Kalshi adapters are reading different notions of start time (scheduled first pitch vs market/event timestamp) and only correct a proven adapter normalization bug.

Moneyline/spread rows remain rule-incomplete and receive **$0 capacity credit** until settlement/postponement/cancellation semantics are explicitly certified. Strict props may be added only when rule equivalence is proven; participation-conditional rows remain excluded from guaranteed-arb capacity.

### Gemini<->Kalshi sports

Central ledger reconciliation is clean: Caleb is settled -$0.44 and there are **zero unresolved durable reservations**. Do not count any G<->K canary/shadow row without authoritative both-leg fill and final settlement reconciliation. Kalshi secondary websocket 429s continue to support the account-level concurrent-handshake/contention diagnosis rather than a weak account tier.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running with no resets. Current certified totals evidence remains **$0/day at >=3c**.
2. Diagnose the **268 exact shared MLB game keys rejected solely on scheduled-start skew**. Emit start-skew distribution plus the raw Polymarket/Kalshi source timestamps for a small read-only sample. Correct only a proven timestamp-adapter bug; preserve the <=5 minute identity gate.
3. Once timestamps align, price the resulting physical pairs and report moneyline/spread/game-total >=1c/>=2c/>=3c, q1 depth and missing-to-3c. Until then those families remain $0 capacity.
4. Keep rules incomplete / capacity $0 until settlement/postponement/cancellation semantics are certified. Add only strict props with provable equivalence.
5. Preserve Kalshi read-budget headroom and websocket backoff; do not stop unrelated services for census convenience.
6. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
7. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
