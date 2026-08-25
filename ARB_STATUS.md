# ARB Control Plane

Last verified: **2026-08-25 05:32 UTC / 2026-08-25 01:32 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Current scoreboard

- Caleb Durbin one-leg Gemini exposure remains realized at **gross -$0.42 / net -$0.44**; do not shadow it away.
- Settlement reconciler previously confirmed `open_reconcilable=0` and `one_leg_open=0`.
- Prior historical proof remains approximately **+$0.2679** across seven earlier settled-compatible Gemini<->Kalshi sports trades; keep historical proof separate from Caleb.
- Same-Gemini crypto most recently showed roughly 2,647 certified q1 pairs / 275 books and **0 immediate >=3c FOK/FOK locks**.
- K<->P strict-prop taker census is now durably emitting snapshots; the latest observed cycles have **0 >=1c, 0 >=2c and 0 >=3c after-fee locks** among books with usable quotes.
- Recent Polymarket maker attempts were zero-fill. Maker-indicated rows are not arbitrage.

## Fixed edge-census protocol

Instrumentation deadline: **Wednesday 2026-08-26**. Then collect **72 hours** and publish verdict **Sunday 2026-08-30**. Data-quality/feed failure may add at most 24 hours; an unmeasurable lane after that counts as **$0 deployable capacity**. Do not extend because results are interesting or disappointing.

Pre-committed project verdict thresholds, which must not move after seeing results:

- **<$50/day** realistically deployable locked-edge capacity: end dedicated arb engineering and archive the project.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward the $480/day aspiration.

Required locked-edge measurements are >=1c/>=2c/>=3c after fees, exact executable depth, p50/p90/max lifetime, quote skew, overlap-adjusted capacity, and latency-floor-filtered realistic capture. Provisional latency floor is 80ms and may rise to measured production p95 but may not be lowered to rescue capacity. Maker adverse-selection evidence uses public trade-print proxy, not live census orders.

Same-venue Gemini/Polymarket/Kalshi zero results are near-verdict evidence; do not spend engineering time manufacturing same-venue ladder violations.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Service `bdcd4d42-bfcf-469c-9bd7-46b26acd8b87` remains **SUCCESS** on deployment `997fd17d-42ea-430e-b179-88d7c608fb26` and is actively cycling, not merely carrying a green badge. Current service-level config starts `python -m src.sports_poly_structural_live_supervisor_verified`, restartPolicy `NEVER`, in `us-west2`; live mutation remains disarmed by census policy.

Fresh 1-hour Railway metrics at 01:32 ET independently confirm process activity: CPU average ~**0.0279**, current ~**0.0152**; network RX average ~**0.00431 GB/sample** and TX ~**0.000615 GB/sample**, with 61 one-minute samples. This is evidence the collector is alive, not evidence of edge.

The first durable hard census result remains unfavorable: across the latest participation-ready strict-player-prop snapshots, **>=1c / >=2c / >=3c after-fee taker-lock bins are all zero** for books that actually have usable two-sided quote data. Do not convert this into a whole-lane verdict yet because feed measurability is incomplete.

The principal data-quality blocker remains Polymarket book coverage. One fresh cycle requested roughly **160** books but only about **9** returned usable top-of-book quotes. Therefore the zero-edge result is valid for quoted books but not yet a complete estimate of deployable K<->P capacity. The current telemetry also explicitly lacks sub-second opportunity lifetimes / print-proxy capture, and full-game MLB moneyline/spread/total coverage is not yet represented in this census feed.

Before the 72-hour clock can be considered valid for this lane, instrument/persist: semantic-deduped full-game MLB ML/spread/total plus strict certifiable props, >=1c/>=2c/>=3c bins after exact fees, executable q1+ depth, first-seen/last-seen/bin-transition lifetimes, quote skew, and latency-floor-filtered realistic capture. Participation-conditional or rule-mismatched rows remain excluded from guaranteed-arb capacity.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Latest deployment `030a6f3d-f40c-48ba-8337-d53285cff801` remains **SUCCESS**. Fresh 1-hour Railway metrics at 01:32 ET confirm sustained activity: CPU average ~**0.4365** (max ~0.6211), network RX average ~**0.02656 GB/sample** and TX ~**0.00986 GB/sample**, across 61 one-minute samples. Current start command remains the event-driven read-only hot shadow with `--hot-profit 0.01`, 50ms max quote skew, 100ms hot-book age, 10s heartbeat. G<->K live mutation remains disarmed and Caleb's realized one-leg loss remains in accounting.

Do not re-arm G<->K unless the already-reconciled zero-open-exposure state remains conclusively true and a separate bounded-risk decision explicitly authorizes it. Census collection itself requires no live order authority.

### Kalshi account-limit diagnosis

A fresh targeted read-only Railway-agent attempt at 01:32 ET again returned **`Agent usage limit reached`** before runtime logs or authenticated `/account/limits` could be inspected. Therefore actual Kalshi tier/read/write/refill/bucket values and account-wide consumer contention remain uncertified this run.

Do **not** change pacing/backoff or stop unrelated live Kalshi consumers based on inference. The next successful runtime inspection must capture `/account/limits` first, enumerate active account consumers, and distinguish account-tier/account-wide contention from local-service pacing. New census code should prefer Kalshi dedicated `external-api` / `external-api-ws` hosts where supported.

### Same-Gemini crypto

Keep zero-order-entry census only. Prior snapshot remains ~2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks. Treat this as near-verdict evidence, not a prompt to manufacture same-venue violations.

### Old Gemini<->Kalshi 15m crypto

Fresh project status still reports `crypto-15m-sol-live-canary-10bp-50ms` **CRASHED** on deployment `03080d7a-1696-4c38-a86d-54fa41882892`. The old executor remains a known bounded-campaign crash case and must not be restarted as guaranteed arbitrage: settlement-index non-equivalence remains the stronger blocker. The safe rollover repair can remain review-ready but is not a reason to spend census time reopening this lane.

## Weather side question

The central arb Neon ledger has **no weather-tagged trade events since 2026-03-01**, so it contributes **$0 verified realized weather P&L** from that source. This is not proof that the separate weather project has no historical realized records.

The separate Railway project `kalshi-weather-bot` is reachable. Its current `weather-live-probe` uses `restartPolicy=NEVER`; separate authoritative March-to-date trade history remains unavailable from the currently exposed runtime records.

Therefore authoritative realized weather P&L since March remains **unverified outside the central ledger and must be credited as $0 until separate project records prove otherwise**. Historical/backtest/seasonal edge remains categorically separate from current realized performance.

## Next highest-value actions

1. Finish K<->P census measurability before the fixed instrumentation deadline: improve usable book coverage, add semantic-deduped full-game MLB ML/spread/total, and persist exact >=1c/>=2c/>=3c depth/skew/lifetime/capture telemetry. Do not weaken rules to increase counts.
2. Capture authenticated Kalshi `/account/limits` plus active Railway Kalshi consumer inventory before changing pacing/backoff. Current runtime-agent quota is a transient diagnostic blocker, not permission to guess.
3. Verify G<->K hot-shadow OPEN/MOVE/CLOSE and latency telemetry is durably recorded while order entry stays disarmed.
4. Extract authoritative realized weather P&L from the separate weather project's own records; until then weather contributes $0 to the verdict.
5. Preserve the fixed verdict thresholds and deadline. If K<->P remains unmeasurable after the allowed +24h data-quality extension, count it as $0 deployable capacity rather than extending research.
