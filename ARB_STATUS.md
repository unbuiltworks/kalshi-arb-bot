# ARB Control Plane

Last verified: **2026-08-25 21:00 UTC / 2026-08-25 17:00 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Edge-census protocol

The 72-hour K<->P wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and continues through imperfect data. Invalid/unmeasurable intervals are scored as **$0 deployable capacity for that wall-clock interval** rather than pausing the clock.

Fixed verdict thresholds remain unchanged:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Required evidence remains >=1c/>=2c/>=3c after fees, exact executable q1 depth, quote skew, source quality, signal lifetime where valid, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

Same-venue Gemini/Polymarket/Kalshi zero results remain near-verdict evidence; do not spend engineering time manufacturing ladder violations.

## Current authoritative scoreboard

- **2026-08-25 ET:** fresh Neon verification = **0 TRADE_REALIZED / $0.0000 net**.
- Caleb Durbin remains a real settled one-leg sports loss at **net -$0.44** and is not reclassified away.
- Weather since **2026-03-01:** fresh Neon verification = **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized weather P&L**.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Production is healthy/read-only and reached `EVENT_READY` with **72 strict discovered / 72 matched live books / 72 seeded Polymarket books / 72 initialized Kalshi books**.

The first decision-grade telemetry window initially appeared to show many nominal positive price gaps but zero q1 depth and zero realistic capture. That result is **not valid executable-capacity evidence** because a telemetry schema bug was found:

- `ShadowPlan.as_dict()` emits `quantity` and side labels but does not emit `poly_depth` / `kalshi_depth`.
- The gateway originally read `qty`, `poly_depth`, `kalshi_depth`, mechanically forcing depth to zero.
- PR **#149** fixed `quantity` and changed depth telemetry to derive selected-side depth from the already-loaded quotes. CI passed and PR #149 merged at `0497113a773eaf37b47b0d2fd2bb81613e0a4753`; Railway deployment `d0e33137-3aff-4d45-b562-199e73c810b0` is **SUCCESS**.
- The replacement runtime exposed a second label mismatch: total-market plans use **OVER/UNDER**, not YES/NO. Therefore the current helper still fails closed to zero depth until OVER->YES-depth and UNDER->NO-depth are explicitly mapped.

Consequently, all current `realistic_capture_bins=0` results from this depth telemetry are **invalid measurement intervals**, not proof of zero real liquidity. Under the fixed clock policy those wall-clock intervals contribute $0 deployable capacity, but they are not counted as market evidence that true q1 depth is zero.

Descriptive price-only evidence from the pre-fix run did show nominal positive observations (including >=1c/>=2c/>=3c bins), with typical cross-venue receive skew around 40-45ms and short observed signal lifetimes around ~0.18s average / ~0.44s max. Those are not executable-capacity claims until depth telemetry is correct and the >=80ms capture filter can be evaluated honestly.

The current v5 strict matcher is also still **full-game MLB totals only** (`KXMLBTOTAL`). Required MLB moneyline/spread and strict rule-certified prop breadth is therefore incomplete. Missing breadth does not pause the 72-hour clock; unmeasured families count as zero unless added within the committed window.

### Gemini<->Kalshi sports

Bounded q=1 canary deployment `e029636d-5342-478e-a733-7f8c0dadf669` remains `SUCCESS`. Fresh log checks after re-arm found **no SUBMITTED and no FILLED events**. Kalshi authenticated cross-book websocket 429 remains the current transport blocker. No live trade is counted.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains crashed from the previously confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. It is not being reopened here because settlement-index non-equivalence remains the stronger safety/economic blocker. AWS/low-latency maker-routing work remains isolated in the separate chat.

## Highest-value next actions

1. Finish and production-verify the **OVER/UNDER selected-side depth mapping** so K<->P realistic-capture bins become trustworthy. Do not pause the 72-hour wall clock while fixing it.
2. Once depth is correct, rank actual >=1c/>=2c/>=3c q1 capacity after the >=80ms floor; price-only nominal gaps do not count.
3. Add K<->P MLB moneyline/spread breadth and only strict props with provable rule/settlement equivalence; semantic mirrors must be deduped.
4. Keep G<->K execution labeling exact; no current submission/fill is verified.
5. Preserve the fixed <$50 / $50-$149 / >=$150 verdict thresholds and do not extend the census because results are interesting.
6. Keep AWS latency/maker-routing work isolated from this main control-plane chat.
