# ARB Control Plane

Last verified: **2026-08-26 08:34 UTC / 2026-08-26 04:34 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- Fresh Railway control-plane state: `sports-kp-live-executor`, `sports-generic-counterpart-shadow`, and `sports-gk-autonomous-supervisor` remain **SUCCESS**. The old `crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED/contained** and was not restarted.
- Fresh generic MLB breadth logs remain heavily contaminated by the known partial-game scope bug. A latest cycle reported **115 semantic unique groups** (moneyline 1 / spread 25 / game total 89), with descriptive `>=3c` counts of **0 / 23 / 87** respectively. Those spread/total rows remain **$0 capacity** because current normalization can still map Polymarket first-five/partial-game markets to full-game Kalshi identities.
- Prepared **draft PR #156** on isolated branch `feature/kp-full-game-scope-filter`. It adds a conservative read-only wrapper that rejects only Polymarket labels explicitly identifying partial-game scope (`First/1st 5 Innings`, `F5`, `First 3`, `First/1st Inning`) before the existing exact semantic matching/pricing pipeline. It adds no network/order transport and changes no start-time, pricing, settlement, or risk gate. Regression tests were added. CI had not started at this checkpoint, so #156 remains **draft/unmerged/un-deployed**; production census is not being altered blind.
- **PR #155 is merged** after its green 451-test CI. The Kalshi MLB ticker HHMM normalization now interprets ticker time in `America/New_York` and converts to UTC; the existing <=5 minute start-time identity gate remains unchanged.
- Railway exposed two production wiring issues during promotion of the read-only MLB breadth service. First, the service watch patterns omitted `src/mlb_game_side_normalization.py`, so the #155 merge was skipped. After adding that file, the effective start command was still pointing at a nonexistent `src.sports_kp_mlb_generic_breadth_loop` module. The service is now correctly configured as `python -m src.sports_kp_generic_mlb_game_breadth --interval-seconds 60`, `restartPolicy=ALWAYS`, with the normalization file in watch patterns. Fresh deployment `ea8bc853-8b7a-4c49-acf4-bfb712036178` is **SUCCESS** and the read-only loop is running.
- The #155 timezone fix worked in production: a fresh breadth cycle found **164 exact shared semantic keys**, **0 start-skew rejects**, **360 physical representations**, **102 priceable semantic groups**, and **208 priced representations**. Start-time normalization is no longer the blocker.
- However, the eye-catching generic spread/total price gaps are **not valid guaranteed-arb evidence**. The top rows include Polymarket first-five-inning (`f5`) totals matched to full-game Kalshi `KXMLBTOTAL` markets. The current normalizer labels all normalized identities `scope="full_contest"` and does not exclude partial-game Polymarket totals. Therefore the descriptive family bins are contaminated by scope/rule mismatch and receive **$0 capacity credit**. Do not count the observed **68 game-total >=3c** or **9 spread >=3c** rows until partial-game scopes are removed and exact full-game rules are certified.
- Family-level descriptive breadth from that contaminated snapshot: moneyline **2 priceable semantic groups, 0 >=1c / 0 >=2c / 0 >=3c**, max edge about **-1.57c**; spread **11 groups, 9 >=1c / 9 >=2c / 9 >=3c**; game total **89 groups, 71 >=1c / 70 >=2c / 68 >=3c**. Only the moneyline zero is currently non-suspicious descriptive evidence; all rule-incomplete families remain capacity $0.
- The decision-grade certified K<->P MLB totals collector is unchanged and freshly verified: **1,031 >=1c / 0 >=2c / 0 >=3c** after fees from **1,661 observations / 9 semantic keys**. At the fixed provisional **>=80ms** realistic-capture floor it remains **137 >=1c / 0 >=2c / 0 >=3c**. Average selected-side q1 depth remains **~2,072.66 contracts**, max **22,647.58**, average quote skew **~45.53ms**. The certified totals slice remains **$0/day deployable capacity at the fixed 3c floor**.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Fresh weather query again shows **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01**. Seasonal/historical weather edge is not credited as current realized performance.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. Do not weaken backoff or disrupt unrelated consumers solely for census telemetry.
- The 72-hour wall clock continues with **no reset or pause**. Invalid/unmeasurable or rule-mismatched breadth contributes $0 for the relevant interval/family.

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

PR #155 is merged and the four-hour timestamp bug is fixed. The active read-only breadth loop now reaches book pricing.

The first post-fix pricing snapshots expose a semantic-scope bug: Polymarket partial-game / first-five (`f5`) markets are currently normalized as `scope="full_contest"` and can collide with Kalshi full-game identities. The very large descriptive >=3c totals/spread gaps therefore cannot receive guaranteed-arb capacity credit. `rules_complete=false` remains authoritative and `capacity_credit_usd=0.0` is correct.

Until partial-game scope is excluded or modeled distinctly and exact venue rule equivalence is certified:

- moneyline descriptive results remain near-zero/negative with **0 >=1c / 0 >=2c / 0 >=3c** in the last clean snapshot;
- spread/game-total descriptive positive gaps: **do not count as arb**;
- moneyline/spread/game-total guaranteed capacity remains **$0**;
- census clock does not pause or reset.

Draft PR #156 is the current bounded cleanup path. It fails closed only on explicit partial-game Polymarket labels before existing matching/pricing. It must pass normal CI/review before any deployment or capacity remeasurement.

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
2. Get draft PR #156 through normal CI/review. If green, deploy the read-only full-game scope filter and immediately rerun moneyline/spread/game-total pricing; explicit partial-game rows remain $0.
3. Report exact cleaned full-game moneyline/spread/game-total >=1c/>=2c/>=3c bins, q1 depth, and missing-to-3c. Rule-incomplete rows remain $0 even after scope cleanup.
4. Treat G<->K hot >=3c observations as **research-only capacity $0** until complete lifecycle settlement equivalence is certified. Keep guaranteed order entry disarmed.
5. Keep rule-incomplete player props at capacity $0 until settlement/postponement/cancellation/stat-correction semantics are certified. Participation proof alone is insufficient.
6. Preserve Kalshi account headroom and websocket backoff; do not stop unrelated services for census convenience.
7. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
8. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
