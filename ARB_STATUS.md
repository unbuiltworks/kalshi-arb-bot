# ARB Control Plane

Last verified: **2026-08-26 03:50 UTC / 2026-08-25 23:50 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- `sports-kp-live-executor` remains **SUCCESS** and decision telemetry is still live. Fresh runtime through ~03:48 UTC remains **1,031 >=1c / 0 >=2c / 0 >=3c** after fees; provisional >=80ms capture remains **137 >=1c / 0 >=2c / 0 >=3c**. Average selected-side q1 depth remains ~2,072.66 contracts, so liquidity is not the blocker; the certified MLB totals slice remains **$0/day deployable capacity at the fixed 3c floor**.
- The merged PR #153 start-skew diagnostic was executed safely through an isolated, read-only GitHub Actions one-off (`feature/kp-start-skew-live-diagnostic-run`, commit `60911e82...`) without interrupting Railway collectors. The diagnostic code itself started correctly but the run failed specifically on **HTTP 403** from `https://external-api.kalshi.com/trade-api/v2/events?...KXMLBGAME...` from the GitHub-hosted runner. This is an execution-environment/auth-context blocker, not evidence that semantic MLB overlap disappeared and not evidence of a weak Kalshi account tier.
- Do **not** loosen the <=5 minute start-skew gate. Moneyline/spread/game-total breadth remains **$0 capacity** until raw timestamp fields can be measured from an authenticated runtime context. The 72-hour wall clock continues with no reset; the failed diagnostic interval does not earn an extension beyond the one pre-committed +24h maximum.
- Fresh G<->K runtime verification: `sports-gk-autonomous-supervisor` remains **SUCCESS**, explicitly `read_only=true` and `order_entry=false`, with **570/570 Kalshi tickers initialized** and 570 Gemini symbols. It has now recorded **6 hot >=3c evaluations / 4 complete open-close signals**. One fresh Elly De La Cruz hits signal modeled ~3.61c locked profit for ~963ms, but rule assessment remained **UNAUDITED / exact_settlement_equivalent=false / execution_eligible=false**, so it receives **$0 guaranteed-arb capacity credit** and no trade was sent.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-25 ET = 0 TRADE_REALIZED / $0.0000 net**, **0 unresolved durable capital reservations**, and **0 weather-tagged TRADE_REALIZED / $0 verified weather P&L since 2026-03-01**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Kalshi account diagnosis remains unchanged: authenticated `/account/limits` previously proved **advanced tier, 900 bucket / 300 refill** for both reads and writes. The new GitHub-runner 403 is therefore not a reason to weaken backoff or stop unrelated Kalshi consumers.
- The old `crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED/contained**. Its bounded-campaign failure is proven, but Gemini/Kalshi settlement-index equivalence remains unproven, so it is not reopened as guaranteed arbitrage.

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

PR #152 proved hundreds of exact shared semantic MLB game keys exist, but **0 pass the existing <=5 minute scheduled-start gate**, so none reach executable pricing. PR #153 is a read-only diagnostic that exposes raw Polymarket/Kalshi timestamp candidates and skew distribution without relaxing identity rules.

The first safe one-off execution of #153 failed specifically because a GitHub-hosted runner received **403 Forbidden** from Kalshi's dedicated `external-api` event discovery endpoint. Therefore:

- this is **not** proof of zero game-market overlap;
- the <=5 minute identity gate remains unchanged;
- moneyline/spread/game-total capacity remains **$0** until timestamp alignment is measured from authenticated runtime context;
- the census clock does not pause or reset.

### Gemini<->Kalshi sports

The production hot-shadow remains deliberately disarmed: `read_only=true`, `order_entry=false`. Fresh state has **570 Gemini symbols / 570 initialized Kalshi tickers**, >120k comparable evaluations, and **6 hot >=3c evaluations**. These are not guaranteed capacity because the complete Gemini prop lifecycle fingerprint remains uncertified. A fresh Elly De La Cruz hits signal lasted ~963ms and modeled ~3.61c locked profit, but its rule assessment explicitly remained `UNAUDITED`, `exact_settlement_equivalent=false`, and `execution_eligible=false`; it receives $0 guaranteed-arb credit and no order was sent.

Central ledger reconciliation is clean: Caleb is settled -$0.44 and there are **zero unresolved durable reservations**. Keep G<->K execution disarmed unless exact rule/settlement equivalence is independently certified.

Kalshi secondary websocket 429s remain consistent with concurrent-handshake/contention, not a weak account tier. `/account/limits` remains **advanced**, **900 bucket / 300 refill** for both reads and writes.

### Weather

Fresh authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running with no resets. Current certified totals evidence remains **$0/day at >=3c**.
2. Move the merged #153 timestamp diagnostic into an **authenticated read-only runtime path** without interrupting the active collectors; measure raw timestamp fields and skew distribution and correct only a proven adapter normalization bug. Do not loosen the <=5 minute gate.
3. If timestamps align after a proven normalization fix, immediately price moneyline/spread/game-total physical pairs and report >=1c/>=2c/>=3c, q1 depth, skew, and missing-to-3c. Until then those families remain $0 capacity.
4. Treat G<->K hot >=3c observations as **research-only capacity $0** until complete lifecycle settlement equivalence is certified. Keep order entry disarmed.
5. Keep rule-incomplete player props at capacity $0 until settlement/postponement/cancellation/stat-correction semantics are certified. Participation proof alone is insufficient.
6. Preserve Kalshi account headroom and websocket backoff; do not stop unrelated services for census convenience.
7. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
8. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
