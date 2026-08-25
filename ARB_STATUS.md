# ARB Control Plane

Last verified: **2026-08-25 22:40 UTC / 2026-08-25 18:40 ET**

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
- Caleb Durbin remains a real settled one-leg sports loss at **net -$0.44** and is not reclassified away. Fresh Neon state shows no newer unresolved G<->K one-leg exposure.
- Weather since **2026-03-01:** fresh Neon verification = **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized weather P&L**.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Production remains healthy/read-only on deployment **`11e53e98-d318-4455-8341-2cfadc5d275b`**, Railway **SUCCESS**. One-hour metrics show sustained CPU/network activity, so the process is genuinely alive rather than green-but-dead.

Direct Railway deploy logs now expose the cumulative decision-grade totals census. Latest verified summary near **22:39 UTC / 18:39 ET**:

- **53,237 comparable evaluations**;
- **1,661 decision-census observations** across **9 semantic keys**;
- after-fee edge bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms realistic-capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average q1 selected-side depth **~2,072.66 contracts**, maximum **22,647.58**;
- average quote skew **~45.53ms**, maximum **100ms**;
- average local process latency **~0.234ms**;
- **612 closed signals**, average descriptive lifetime **~174.07ms**, maximum **567ms**;
- source quality remains **`REST_SNAPSHOT_NOT_WS_LIFETIME`** with Kalshi `rest_batch` transport;
- `read_only=true`, `order_entry=false`.

This is the strongest census evidence so far: the measured, rule-certified MLB-total slice has abundant **1c** gaps and real depth, but **zero >=2c and zero >=3c observations**, including **zero >=2c/>=3c at the provisional >=80ms capture floor**. At the fixed 3c taker/taker floor, this measured slice currently contributes **$0 deployable capacity**. REST-derived lifetimes remain descriptive rather than authoritative websocket timing.

The existing v5 matcher still measures strict MLB totals only. Missing MLB moneyline/spread/strict-prop breadth does not pause the clock and counts $0 if not made measurable by the deadline.

### K<->P MLB game breadth one-off

To avoid interrupting the active totals census, an obsolete one-off read-only audit service was repurposed for `src.sports_kp_generic_mlb_game_breadth` and completed **SUCCESS** at **22:39:55 UTC**. It used `external-api.kalshi.com` and `gateway.polymarket.us`, with no order authority.

Fresh snapshot:

- **52 Polymarket MLB events**;
- **88 Kalshi events**: 38 moneyline (`KXMLBGAME`), 25 spread (`KXMLBSPREAD`), 25 total (`KXMLBTOTAL`);
- **896 Polymarket normalized sides / 822 Kalshi normalized sides**;
- **280 ambiguous contract keys rejected**;
- **0 normal event pairs / 0 book-comparable physical pairs / 0 semantic unique groups** for moneyline, spread, or game-total in this generic matcher.

The diagnostic explicitly classified itself **`RULE_INCOMPLETE_NORMAL_EVENT_SHADOW_NOT_EXECUTION_ELIGIBLE`** and assigns **$0/day capacity credit**. This is a breadth/join result, not an edge result: the venues clearly expose MLB game inventory, but the generic normalizer currently produces no execution-certifiable cross-venue joins. No shadow row is promoted to guaranteed arb.

### Gemini<->Kalshi sports

Bounded q=1 canary deployment **`e029636d-5342-478e-a733-7f8c0dadf669`** remains Railway **SUCCESS**. Fresh deploy-log filtering found **no `SUBMITTED` event** in the checked production window, and Neon shows no unresolved newer one-leg exposure after Caleb's settled -$0.44 loss. No new G<->K fill or P&L is credited.

The prior Kalshi authenticated websocket 429 loop remains cleared after reducing the background autonomous supervisor's REST polling from 100ms to 1000ms. No unrelated Kalshi service was stopped solely to improve census telemetry.

### Account-level Kalshi 429 diagnosis

Previously authenticated `/account/limits` evidence remains authoritative: account tier is **advanced**, with **900 request bucket / 300 refill** for both reads and writes. K<->P's separate websocket handshake contention was therefore not attributed to a weak account tier. The active census remains on bounded batched REST while healthy established Kalshi websocket consumers are left undisturbed.

### Maker lane relationship

The fixed **3c net threshold is not being lowered**. The totals census now gives a useful boundary condition for the isolated maker lane: there are many deep 1c taker/taker gaps but no observed 2c/3c gaps in the measured slice. Maker routing may test whether fill-conditioned pricing can bridge that shortfall, but maker-model economics are not counted here as executable or realized capacity.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains Railway **CRASHED** from the previously confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. It is not being reopened here because settlement-index non-equivalence remains the stronger safety/economic blocker. AWS/low-latency maker-routing work remains isolated in the separate chat.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running without resets; measured MLB totals currently show **0 >=3c realistic-capture observations** and therefore $0 deployable taker/taker capacity so far.
2. Diagnose why the generic MLB game normalizer yields **0 joins despite 52 Polymarket / 88 Kalshi events**, focusing only on exact team/start/line/canonical-side identity required to make moneyline/spread breadth measurable. Do not loosen rule certification.
3. Add only strict props with provable rule/settlement equivalence and dedupe semantic mirrors; participation-conditional rows remain excluded from guaranteed capacity.
4. Keep recording G<->K authoritative submissions/fills/settlements only; Caleb remains -$0.44 realized and no newer unresolved one-leg exposure is currently visible.
5. Preserve Kalshi read-budget headroom; do not disrupt unrelated services or lower backoff merely to improve census telemetry.
6. Preserve the fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the maximum one-time +24h data-quality allowance. Interesting results do not extend the census.
7. Keep AWS latency/maker-routing work isolated from this main control-plane chat.
