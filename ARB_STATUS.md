# ARB Control Plane

Last verified: **2026-08-26 00:38 UTC / 2026-08-25 20:38 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- Re-established Railway production state. `sports-kp-live-executor` remains **SUCCESS** on deployment `11e53e98-d318-4455-8341-2cfadc5d275b`; `sports-generic-counterpart-shadow` remains **SUCCESS** on deployment `577775cc-e429-4609-a622-12e87c90c3ad`; `sports-gk-multifamily-live-canary` remains **SUCCESS**; old `crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** and was not reopened.
- The measured K<->P strict MLB-total census remains decision-negative at the fixed 3c taker/taker floor: **1,031 >=1c / 0 >=2c / 0 >=3c**, and **137 >=1c / 0 >=2c / 0 >=3c** at the provisional >=80ms capture floor. This slice still contributes **$0/day deployable capacity**.
- The MLB game breadth blocker is now implemented on isolated branch `feature/kp-mlb-physical-representation-expansion` at commit `4a5fda180b8c070a2bf090b2ac38e55126f48cef`. The read-only repair preserves exact `contract_key` identity and start-skew gates, enumerates duplicate physical market+side representations only *within* each already-exact shared semantic key, then relies on executable-book pricing and downstream semantic dedupe to select the best physical representation. It adds no order transport, no live authority, no rule certification, and no threshold changes.
- Regression coverage was added for YES/NO mirror expansion, multiple same-side physical representations, and preservation of the start-skew gate. The normal PR creation action was safety-blocked this run, so the branch was **not** force-moved into production and no Railway service was redeployed around review/CI.
- Until that read-only breadth repair is promoted and measured, moneyline/spread capacity credit remains **$0**. Missing breadth does not pause or reset the 72-hour wall clock.
- Account-level Kalshi diagnosis is unchanged: authenticated `/account/limits` = **advanced**, **900 bucket / 300 refill** for reads and writes; do not disrupt unrelated Kalshi services or weaken websocket backoff solely to improve census telemetry.
- Caleb Durbin remains **-$0.44 REALIZED**. No stale/shadow/unconfirmed row is counted as profit.

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
- Fresh authoritative reservation reconciliation shows **0 trade IDs with CAPITAL_RESERVED and neither CAPITAL_RELEASED nor TRADE_REALIZED**. There is therefore no currently unresolved durable capital reservation in the central ledger.
- Weather since **2026-03-01:** fresh Neon verification = **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized weather P&L**.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Production remains healthy/read-only on deployment **`11e53e98-d318-4455-8341-2cfadc5d275b`**, Railway **SUCCESS**. Fresh deploy logs through ~23:42 UTC show the process continuously emitting `EVENT_SUMMARY`; triggers continue increasing, so the process is genuinely alive rather than green-but-dead.

Latest cumulative decision-grade totals census remains:

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

The counters have not added new comparable evaluations since the prior checkpoint even though raw triggers continue increasing. This does not change the measured result: the certified MLB-total slice has deep 1c gaps but **zero >=2c and zero >=3c**, including zero >=2c/>=3c at the provisional >=80ms capture floor. At the fixed 3c taker/taker floor, this measured slice currently contributes **$0 deployable capacity**. The 3c floor is not being lowered.

The existing v5 matcher still measures strict MLB totals only. Missing MLB moneyline/spread/strict-prop breadth does not pause the clock and counts $0 if not made measurable by the deadline.

### K<->P MLB game breadth — exact join root cause narrowed

The read-only `sports-generic-counterpart-shadow` continues to expose real MLB inventory: latest snapshot grew to **52 Polymarket MLB events / 96 Kalshi events** (46 moneyline, 25 spread, 25 total), **896 Polymarket normalized sides / 846 Kalshi normalized sides**.

It still reports **0 normal-event pairs**, but the cause is now materially narrower than “no matching inventory”:

- the pairing function only increments `ambiguous_contract_keys_rejected` for keys present in **both** normalized venue indexes;
- production reports **280 ambiguous shared contract keys** on every cycle;
- `_canonical_side()` then rejects a shared key whenever either venue has multiple distinct YES physical representations (or otherwise lacks a single unique physical representation);
- therefore the generic normalizer is already finding **280 exact semantic contract-key intersections**, but all 280 are being discarded at the physical-representation canonicalization step before start-skew/economics checks.

This is an identity/canonicalization blocker, not evidence of zero cross-venue MLB game overlap. The safe census repair is **read-only physical-representation expansion within each already-exact shared contract key**, followed by book pricing and semantic dedupe, rather than choosing an arbitrary duplicate or loosening team/start/line/rule identity. The isolated implementation now exists on `feature/kp-mlb-physical-representation-expansion` but is not promoted. Until measured and rules are complete, ML/spread rows receive **$0 deployable-capacity credit**.

### Gemini<->Kalshi sports

Fresh `sports-gk-multifamily-live-canary` deployment **`8b387af6-7eb7-4f39-82e2-8950c9052b0d`** is Railway **SUCCESS**, `restartPolicy=NEVER`, and its configured entrypoint is the genuine bounded q=1 HR/TB/K live rule-risk canary rather than a shadow collector. Source still requires exact identity, official MLB participation proof, >=3c modeled after-fee lock, <=100ms books, <=50ms skew, 50ms revalidation, one-use arm/deadline, deterministic IDs, and a single service-wide submission attempt. It remains explicitly classified **`LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB`**.

The prerequisite one-leg safety state is now conclusively clean in the central ledger: Caleb is settled -$0.44 and there are **zero unresolved durable capital reservations**. Fresh ledger queries show **no new G<->K submission/fill/realization** after this deployment.

The canary's Kalshi cross-book websocket is currently being rejected with **HTTP 429** and escalating 5s/10s/20s/60s backoff while MLB participation polling remains alive. This fresh second-connection failure reinforces the earlier account-level diagnosis of **Kalshi websocket handshake/concurrent-connection contention**, not a weak account tier. I did not shut down this bounded live service merely to improve census telemetry.

### Account-level Kalshi 429 diagnosis

Authenticated `/account/limits` evidence remains authoritative: account tier is **advanced**, with **900 request bucket / 300 refill** for both reads and writes. K<->P remains on bounded batched REST while established Kalshi websocket consumers are left undisturbed. New census code should continue to prefer `external-api.kalshi.com` / `external-api-ws.kalshi.com` and preserve backoff rather than trying to brute-force another handshake.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains Railway **CRASHED** from the previously confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. It is not being reopened here because settlement-index non-equivalence remains the stronger safety/economic blocker. AWS/low-latency maker-routing work remains isolated in the separate chat.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running without resets; measured MLB totals currently show **0 >=3c realistic-capture observations** and therefore $0 deployable taker/taker capacity so far.
2. Promote the isolated MLB physical-representation expansion only through normal PR/CI review; then price the 280 already-exact shared semantic keys and semantic-dedupe the best executable physical representation. Do not loosen identity.
3. Keep rules incomplete / capacity credit $0 until settlement/postponement/cancellation semantics are explicitly certified. Add only strict props with provable rule equivalence; participation-conditional rows remain excluded from guaranteed capacity.
4. Keep recording G<->K authoritative submissions/fills/settlements only. Caleb remains -$0.44 realized. Do not count the newly running canary unless both legs and final settlement reconcile.
5. Preserve Kalshi read-budget headroom and websocket backoff; do not disrupt unrelated services merely to improve telemetry.
6. Preserve the fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the maximum one-time +24h data-quality allowance. Interesting results do not extend the census.
7. Keep AWS latency/maker-routing work isolated from this main control-plane chat.
