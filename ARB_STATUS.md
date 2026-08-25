# ARB Control Plane

Last verified: **2026-08-25 03:49 UTC / 2026-08-24 23:49 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Current scoreboard

- Caleb Durbin one-leg Gemini exposure remains realized at **gross -$0.42 / net -$0.44**; do not shadow it away.
- Settlement reconciler previously confirmed `open_reconcilable=0` and `one_leg_open=0`.
- Prior historical proof remains approximately **+$0.2679** across seven earlier settled-compatible Gemini<->Kalshi sports trades; keep historical proof separate from Caleb.
- Same-Gemini crypto most recently showed roughly 2,647 certified q1 pairs / 275 books and **0 immediate >=3c FOK/FOK locks**.
- Polymarket MLB immediate-execution census most recently showed **621 participation-certified pairs and 0 immediate >=3c FOK/FOK locks**.
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

Service `bdcd4d42-bfcf-469c-9bd7-46b26acd8b87` latest deployment `997fd17d-42ea-430e-b179-88d7c608fb26` is **SUCCESS**. Railway resource metrics over the latest hour show sustained activity (CPU avg ~0.086, nonzero RX/TX throughout), so the process is alive rather than merely carrying a green badge.

Current service-level config still starts `python -m src.sports_poly_structural_live_supervisor_verified`, restartPolicy `NEVER`, in `us-west2`. Live mutation gates remain disarmed by census policy. The prior startup-cleanup Polymarket RateLimitError therefore remains an instrumentation concern, but the fresh nonzero CPU/network evidence means the process is currently doing work. Exact >=1c/2c/3c bins/lifetimes cannot yet be certified from Railway logs because the Railway agent is usage-limited.

Census coverage requirement remains full-game MLB moneylines/spreads/totals plus strict player props where rule equivalence is provable. Semantic mirrors must be deduped; participation-conditional or rule-mismatched rows never enter guaranteed-arb capacity.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Latest deployment `030a6f3d-f40c-48ba-8337-d53285cff801` is **SUCCESS**. Fresh one-hour metrics show substantial activity (CPU avg ~0.268 with nonzero network). Current start command is the event-driven read-only hot shadow with `--hot-profit 0.01`, 50ms max quote skew, 100ms hot-book age, 10s heartbeat. This lane is therefore confirmed alive for census collection; G<->K live mutation remains disarmed.

### Kalshi account-limit diagnosis

Railway's read-only runtime agent is currently usage-limited, so authenticated `/account/limits` values (tier/read/write/refill/bucket) could not be freshly extracted this run. Do **not** change backoff or disrupt unrelated Kalshi consumers based on guesswork. Next successful runtime inspection must capture account limits first, enumerate active account consumers, and distinguish account-tier throttling from contention. New census code should prefer Kalshi dedicated `external-api` / `external-api-ws` hosts where supported.

### Same-Gemini crypto

Keep zero-order-entry census only. Prior snapshot remains ~2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks. Treat this as near-verdict evidence, not a prompt to manufacture same-venue violations.

### Old Gemini<->Kalshi 15m crypto

The old executor remains a known bounded-campaign crash case and must not be restarted as guaranteed arbitrage: settlement-index non-equivalence remains the stronger blocker. The safe rollover repair can remain review-ready but is not a reason to spend census time reopening this lane.

## Weather side question

Authoritative realized weather P&L since March is **not yet quantified**. The separate `kalshi-weather-bot` Railway project and current sports-project weather services are accessible, but Railway's runtime agent hit its usage limit before authoritative trade-history extraction. Historical/backtest/seasonal edge must remain separate from live realized weather P&L. Until authoritative records are extracted, do not credit weather toward current realized performance or locked-arb capacity.

## Next highest-value actions

1. Finish K<->P in-place census instrumentation and prove >=1c/>=2c/>=3c bins, exact depth, lifetimes/skew and realistic-capture capacity; do not let private cleanup block public zero-risk census.
2. Capture authenticated Kalshi `/account/limits` plus active Railway Kalshi consumer inventory before changing pacing/backoff.
3. Verify G<->K hot-shadow OPEN/MOVE/CLOSE and latency telemetry is being durably recorded.
4. Quantify authoritative realized weather P&L since March, explicitly excluding shadow/backtest/seasonal historical edge.
5. Preserve zero order entry during census and keep Caleb's -$0.44 realized loss in accounting.
