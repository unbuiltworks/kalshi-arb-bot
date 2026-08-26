# ARB Control Plane

Last verified: **2026-08-26 06:48 UTC / 2026-08-26 02:48 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- Fresh Railway project state confirms `sports-kp-live-executor`, `sports-generic-counterpart-shadow`, and `sports-gk-autonomous-supervisor` are **SUCCESS**. The old `crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED/contained**. No unsafe crypto lane was restarted.
- The certified K<->P MLB totals result remains **1,031 >=1c / 0 >=2c / 0 >=3c** after fees from **1,661 observations / 9 semantic keys**. At the fixed provisional **>=80ms** realistic-capture floor it remains **137 >=1c / 0 >=2c / 0 >=3c**. Average selected-side q1 depth remains **~2,072.66 contracts**, max **22,647.58**; liquidity is not the blocker. This certified totals slice remains **$0/day deployable capacity at the fixed 3c floor**.
- Diagnostic #153's Kalshi 403 root cause is fixed. **PR #154 passed CI and merged** at `05fddf65381c1cc6541a888e63bb8382f29e45d8`. The corrected read-only diagnostic successfully ran in Railway.
- The corrected diagnostic produced decisive timestamp evidence: **352/352 exact shared semantic keys were rejected by exactly 14,400,000 ms (4 hours)**. Example: Polymarket `2026-08-27T01:05:00Z` vs Kalshi normalized `2026-08-26T21:05:00Z` from ticker `KXMLBTOTAL-26AUG262105MINATH`. The raw Kalshi expiry fields are later (`expected_expiration_time=2026-08-27T04:05:00Z`, `close_time=2026-08-30T01:05:00Z`), so the observed mismatch is specifically the ticker-HHMM timezone interpretation, not expiry fallback.
- **PR #155** now fixes only that adapter bug: Kalshi MLB ticker HHMM is interpreted in `America/New_York` and converted to UTC while the existing <=5 minute gate remains unchanged. The first CI run exposed two stale three-venue fixtures whose Kalshi tickers encoded the UTC hour; those fixtures were corrected to Eastern ticker semantics without changing production gates. The second full CI run is **GREEN: 451 tests passed**.
- PR #155 remains **draft/unmerged** because the GitHub safety layer blocked the ready-for-review mutation in this run. I did not bypass that control by force-moving the production branch. Head commit is `d5c1aabcc547a726f3eab1354e3d1d69a7f269a3`.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**. Latest ledger event overall remains **2026-08-25 01:52:17.339 UTC**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Fresh weather query again shows **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01**. Seasonal/historical weather edge is not credited as current realized performance.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. Do not weaken backoff or disrupt unrelated consumers solely for census telemetry.

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

- **2026-08-26 ET:** **0 TRADE_REALIZED / $0.0000 net**.
- Latest central-ledger event overall: **2026-08-25 01:52:17.339 UTC**.
- Caleb Durbin: **-$0.44 REALIZED one-leg sports loss**.
- Weather since **2026-03-01:** **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized**. Seasonal/historical edge is not substituted for realized performance.
- Same-Gemini crypto prior snapshot: roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Cross-venue census

### Kalshi<->Polymarket strict MLB totals

`sports-kp-live-executor` remains the decision-grade read-only totals collector. Latest cumulative verified totals evidence:

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

The timestamp blocker is now proven and the narrow code correction is CI-green. Corrected #153 reported **352 shared semantic keys, 352 measured, 0 passing <=5 minutes, all exactly 4 hours apart**. Kalshi MLB ticker HHMM is Eastern wall-clock time; the adapter had treated it as UTC.

**PR #155** changes only that normalization and preserves the <=5 minute identity gate. Full CI passes after updating the stale fixture timestamps to the same Eastern ticker semantics. Until #155 can move through the normal reviewed promotion path:

- moneyline/spread/game-total capacity remains **$0**;
- the census clock does not pause or reset;
- no rule-incomplete row receives guaranteed-arb capacity credit.

Once promoted, immediately use the normal breadth loop to price the now-time-aligned physical pairs and report family-level >=1c/>=2c/>=3c, q1 depth and missing-to-3c.

### Gemini<->Kalshi sports

The guaranteed-arb path remains deliberately disarmed/research-only. Prior hot-shadow state had 570 Gemini symbols / 570 initialized Kalshi tickers and a handful of >=3c modeled signals, but complete Gemini prop lifecycle settlement equivalence remains uncertified. Those signals receive **$0 guaranteed-arb capacity credit**.

Caleb is settled **-$0.44**. Keep guaranteed G<->K execution disarmed unless exact rule/settlement equivalence is independently certified.

Kalshi secondary websocket 429s remain consistent with concurrent-handshake/contention, not a weak account tier. `/account/limits` remains **advanced**, **900 bucket / 300 refill** for both reads and writes.

### Weather

Fresh authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running with no resets. Current certified totals evidence remains **$0/day at >=3c**.
2. Promote CI-green **PR #155** only through the normal reviewed path; do not bypass the safety-layer block.
3. After promotion, let the existing read-only breadth service rerun with the unchanged <=5 minute gate and immediately report book-comparable moneyline/spread/game-total pairs plus >=1c/>=2c/>=3c, q1 depth, and missing-to-3c.
4. Treat G<->K hot >=3c observations as **research-only capacity $0** until complete lifecycle settlement equivalence is certified. Keep guaranteed order entry disarmed.
5. Keep rule-incomplete player props at capacity $0 until settlement/postponement/cancellation/stat-correction semantics are certified. Participation proof alone is insufficient.
6. Preserve Kalshi account headroom and websocket backoff; do not stop unrelated services for census convenience.
7. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
8. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
