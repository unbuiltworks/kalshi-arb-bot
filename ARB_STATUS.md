# ARB Control Plane

Last verified: 2026-08-21 23:48 UTC

This file is the durable handoff point for the ARB project. New chats must establish current production state from Railway/GitHub first, then update this file after meaningful changes. Never treat an old chat message or a successful deployment alone as proof that a strategy is trading.

## State definitions

- **PAPER/SHADOW**: observes or simulates only; no order entry.
- **LIVE PROBE**: bounded real-order capability with explicit safety gates.
- **EXECUTABLE**: exact counterpart/rule match plus fresh after-fee prices and sufficient depth/capital.
- **EXECUTED**: both required legs have confirmed fills.
- **REALIZED**: executed trade has been reconciled/settled and P&L is confirmed.

Only REALIZED P&L counts toward the $480/day objective.

## Current production snapshot

| Workstream | Railway service | Current state | Last verified evidence | Immediate blocker / next action |
| --- | --- | --- | --- | --- |
| Crypto 15m BTC/ETH/SOL/XRP | `crypto-15m-sol-live-canary-10bp-50ms` | **LIVE PROBE** healthy and evaluating; bounded q=1 child armed; no executable edge sampled yet | Deployment `3973a477-078d-4a97-bba8-c883e0786efe` is `SUCCESS` from branch `feature/sol-15m-live-canary-10bp-50ms`, commit `275a889386da015e4766b3c0b956237909dbb997`. All four BTC/ETH/SOL/XRP lanes logged `CRYPTO_HOT_READY_BOTH_BOOKS`. First ~10s summaries showed 39–40 Gemini events/asset, 50–81 Kalshi events/asset, and 89–119 evaluations/asset. | The fatal feed-stop recursion and Gemini handshake/zero-event blockers are fixed. First verified sample had **0 positive evaluations and 0 threshold evaluations**, so no order was justified. Kalshi REST still returns intermittent 429s but is delivering enough exact snapshots for active evaluations; improve pacing only if it increases safe opportunity capture without weakening q=1, fee, 50ms survival/skew, capital, settlement, or fail-closed gates. No fill or realized P&L verified. |
| Sports K↔P combo | `sports-kp-combo-shadow` | SHADOW scanner alive; manufactured-market creation works | Deployment `048abb13-96a7-4d01-a712-edfd4d238f41` is successful on branch `feature/kp-combo-synthetic-shadow`, commit `b90df5e85030b80878c2f4ecf5570df300107da2`. At 23:36 UTC the scanner saw 22 live Polymarket events and retained 8 generated/cached Kalshi MVE markets. Earlier in the same live run it found 46 exact K↔P identity matches and 124 exact K↔Gemini MLB-prop joins. | Still **0 executable combo candidates**. Current best generated combo was `-0.0340` conservative q=1 locked profit and best inverse was `-0.0690`; Gemini replacements remain blocked by full rule-fingerprint certification. Keep generating/pricing, but do not arm merely because live events or exact identity joins exist. |
| Direct sports K↔P | `sports-kp-live-executor` | **LIVE READ-ONLY CERTIFICATION SCAN / NOT EXECUTABLE** | Deployment `1e3a8777-347f-47da-9bb2-d87fce57cd96` is `SUCCESS` on branch `feature/sports-kp-live-executor-impl`, commit `e522c15129ab59bb67153f29eaf02bd7f6b2550e`. Runtime: `hot_contracts=33`, `approved_contracts=0`, `joined_execution_contracts=0`, `read_only_observation_contracts=12`, `poly_books=6`, `kalshi_books=6`, `kalshi_book_transport=REST_SERIES_PLUS_EXACT_FINAL`, `live_env_gate=false`, `arm_token_present=false`, `order_entry=false`. | The prior Kalshi authenticated-WS 429 blocker is bypassed without weakening execution safety: candidate observation uses bounded REST series snapshots and any approved live execution would require an exact depth=1 Kalshi final revalidation. No `SPORTS_KP_CERTIFICATION_CANDIDATE` appeared in the first verified live window. Audit the first >=3¢ after-fee candidate when emitted; never approve exact identity alone. |
| MLB prop rule-equivalence lane | current shared code / `sports-gk-autonomous-supervisor` | CONDITIONAL LIVE RULE-RISK PROBE / NOT CERTIFIED FOR GUARANTEED ARB | `sports-gk-autonomous-supervisor` is active and already uses the same architectural principle: bounded Kalshi REST series snapshots when the account authenticated WebSocket slot is occupied, official MLB participation proof, exact candidate revalidation, q=1, >=3¢ edge, <=50ms skew, <=100ms books, and cumulative bounded sports capital. Its own code still labels the lane rule-risk because Gemini exceptional-event lifecycle is not fully fingerprint-certified. | Highest-impact certification target: reuse the official MLB participation proof in the counterpart pipeline, then require exact remaining venue rule/source/timing equivalence before calling a G↔K prop guaranteed arbitrage. Do not count rule-risk probe activity as realized arbitrage profit. |
| Weather | `weather-live-probe` | Needs fresh reconciliation | Service exists in production | Reconcile prior weather order state before any new live order; do not infer fill from deployment history. |
| Command/observability | `arb-command-center` | Present | Service exists in production | Consolidate status/heartbeat surfaces here where practical so chats can read one operational summary instead of dozens of services. |
| Railway hygiene | project `sports-arb-autonomous` | Service sprawl | Production currently contains dozens of probe/test/shadow services | Inventory by owner/purpose/latest deployment; remove only clearly dead/test/superseded services after confirming no active chat/workstream writes to them. Preserve live/core services. |

## Current GitHub work

Repository: `unbuiltworks/kalshi-arb-bot`

- Crypto 15m production branch is `feature/sol-15m-live-canary-10bp-50ms`; current Railway deployment uses commit `275a889386da015e4766b3c0b956237909dbb997`.
- Crypto feed recovery sequence on 2026-08-21:
  - `b621709b85958667a1fa4bbfb33857a0e7755e2b` — fixed infinite recursion in REST-fallback feed cleanup by preserving/calling the original `_stop_feed` implementation.
  - `0ce258f0e04585ce9eb6f0f185a27254088b2b1c` — consolidated four Gemini public market-data sockets into one shared connection; public handshake throttling persisted.
  - `31e19f20b54049538732e6ae63ba254cbe03cf32` — authenticated the one shared market-data-only Gemini socket; handshake succeeded but `bookTicker` produced no usable events in the verified sample.
  - `275a889386da015e4766b3c0b956237909dbb997` — moved Gemini market data to the documented read-only `depth` RPC over one authenticated persistent socket, with request-start timestamps. This produced verified two-sided Gemini/Kalshi books and active evaluations for BTC/ETH/SOL/XRP.
- Crypto continuous supervised rollover: PR #107 merged into `feature/sol-15m-live-canary-10bp-50ms` before the above production fixes.
- Crypto safe self-recovery: PR #106 merged.
- Crypto rollover planner: PR #105 merged.
- Sports combo synthetic shadow: PR #104 remains open/draft; current Railway combo service is on head `b90df5e85030b80878c2f4ecf5570df300107da2`.
- Sports direct K↔P certification scan on `feature/sports-kp-live-executor-impl`:
  - `edd69fe279b1f4da8780583cfe3ac344180a834c` — when approvals are empty, continue fail-closed read-only observation of exact K↔P joins and emit only >=3¢ after-fee rows as `CERTIFICATION_CANDIDATE_NOT_EXECUTABLE`.
  - `294382e9e15348201377558ab83b24770e609f8f` — back off Kalshi WebSocket 429 reconnects rather than retrying sub-second.
  - `e91988b5e4876a3341d13b48f5423928c908ea31` — serialize Kalshi MLB REST discovery and retry 429s with bounded backoff, stopping the discovery crash/restart storm.
  - `5964167ca1d563fe007288a6eac072615e5819de` — add read-only Kalshi MLB game REST books using series snapshots plus exact depth=1 final revalidation.
  - `e522c15129ab59bb67153f29eaf02bd7f6b2550e` — put the direct K↔P service on that REST fallback, preserve <=100ms/<=50ms/fee/rule gates, and fix structured approval-join logging.
- `config/sports_kp_execution_approvals.json` remains intentionally empty and fail-closed. Each executable row still requires an audited SHA-256 rule fingerprint plus `approved_for_execution=true`.
- q5/q10/dynamic-capacity work remains separate from proof of current q=1 realized profitability. Do not promote size merely because code exists.

## Crypto live findings — 2026-08-21 23:46 UTC

- **Production state overrode the prior status file:** the newest crypto deployment was initially `CRASHED`, despite the prior handoff describing the service as healthy.
- **Actual fatal blocker was cleanup recursion, not merely Kalshi 429s:** the REST fallback monkey-patched `base._stop_feed` and its replacement called the patched symbol again, causing a `RecursionError` during rollover/cleanup. Commit `b621709...` fixed this without changing execution/risk gates.
- **Gemini public WebSocket handshakes were also a real blocker:** four per-asset public sockets were 429ing; one consolidated public socket was still 429ing.
- **One authenticated market-data-only Gemini socket can connect successfully.** The first authenticated `bookTicker` version connected but produced zero Gemini events, so connection success alone was not treated as scanner health.
- **Current production transport works:** commit `275a889...` uses Gemini's read-only WebSocket `depth` request method over one authenticated persistent connection and maps each exact response to its requested asset. All four assets reached `CRYPTO_HOT_READY_BOTH_BOOKS`.
- **Verified first sample is actively evaluating:** ETH 109 evaluations, BTC 89, SOL 119, XRP 103 in the first ~10 seconds; trigger-to-eval p95 was about 19–22 ms on all four lanes.
- **Current sampled economics were not executable:** all four lanes had `positive_evaluations=0` and `threshold_evaluations=0` in that sample. No order should be forced merely to create trade count.
- **Kalshi REST 429s remain noisy but are no longer preventing evaluation.** The fallback now honors `Retry-After` when supplied and otherwise retains exponential backoff. Exact request-start timestamps remain conservative for freshness/skew checks.
- **No new crypto trade has been verified as EXECUTED or REALIZED by this checkpoint.** Armed/preflight logs, book readiness, and evaluations are not fills.

## Sports certification findings — 2026-08-21 23:48 UTC

- **Live Polymarket discovery is not the blocker:** the combo service sees ~21–23 accepting live sports events per cycle, including MLB/NFL inventory in the sampled runs.
- **Kalshi manufactured-market plumbing is working:** the combo shadow generated/resolved and retained 8 MVE markets under its persistent creation budget.
- **Identity matching is not certification:** 46 exact K↔P identity matches and 124 exact K↔Gemini MLB-prop joins were observed, but those do not establish settlement equivalence.
- **Current combo economics are not positive:** best sampled generated combo conservative q=1 locked profit was `-0.0340`; best inverse was `-0.0690`; positive economic candidates were 0.
- **Direct K↔P no longer sleeps when approvals are empty:** it now continuously observes exact K↔P joins but labels an economic row only as `CERTIFICATION_CANDIDATE_NOT_EXECUTABLE` until a valid rule fingerprint is approved.
- **The direct K↔P Kalshi WebSocket 429 was an architecture/connection-slot blocker, not a reason to weaken freshness.** The repo's existing G↔K supervisor already documents that the account authenticated Kalshi WebSocket slot may be occupied by another strategy and uses bounded REST. The K↔P service now does the same.
- **Verified K↔P production readiness:** deployment `1e3a8777-347f-47da-9bb2-d87fce57cd96` reached `SPORTS_KP_LIVE_READY` with 12 read-only contracts, 6 Polymarket books and 6 Kalshi books. Kalshi REST series calls were 200 OK; no second authenticated Kalshi WebSocket was required.
- **Execution safety is still stricter than observation:** any future approved live row must rebuild after slow preflight and force an exact Kalshi depth=1 order-book refresh; stale/low-edge/high-skew rows fail closed.
- **No current K↔P row was promoted:** there were 0 approvals and no `SPORTS_KP_CERTIFICATION_CANDIDATE` in the first verified post-fix window.
- **Known K↔P game-total semantics can diverge:** the existing fingerprint probe rejected an Athletics/Kansas City total because official-source, minimum-completion, not-started, interrupted, and cancellation branches differ. This remains evidence against blanket approval of exact-looking game totals.
- **MLB props have a narrower path:** participation-specific differences can disappear after official MLB game-state proof, but source/timing/exceptional-event semantics must still be exact before the pair can be called certified/executable.
- **No new sports trade was executed or realized by this checkpoint.** Shadow candidates, certification candidates, live-rule-risk probes, and generated MVE quotes are not realized arbitrage profit.

## Railway cleanup checkpoint — 2026-08-21 23:34 UTC

Production inventory contained **88 services**. Classification is deliberately conservative; no service was deleted unless dependency absence could be proven.

### CORE — preserve

- `crypto-15m-sol-live-canary-10bp-50ms`
- `sports-kp-live-executor`
- `sports-gk-autonomous-supervisor`
- `weather-live-probe`
- `arb-command-center`
- `Postgres`
- `sports-trade-settlement-reconciler`
- `crypto-live-balance-reconcile-readonly`

### ACTIVE SHADOW — preserve while current workstream is active

- `sports-kp-combo-shadow`
- `mlb-gk-exec-shadow`
- `weather-shadow-arb`
- `three-venue-router-paper-v2`
- `three-venue-mlb-hot-paper-v1`
- `gemini-kalshi-mlb-prop-hot-shadow`
- `sports-alert-reliability-shadow`
- `crypto-maker-arb-shadow-v1`

### ONE-OFF candidates — finished-looking probes/tests, but dependency verification still required

- `sports-rule-audit-probe`
- `odds-io-prop-snapshot-probe`
- `gemini-tucker-depth-probe`
- `gemini-prop-depth-math-test`
- `odds-io-prop-guarantee-test`
- `sports-pushover-smoke-test`
- `mlb-prop-rule-risk-runtime-check`
- `fanduel-mlb-current-locks-probe`
- `odds-api-pregame-gate-validate`
- `sports-gk-account-preflight-now`
- `sports-gk-live-preflight`
- `sports-ledger-reporting-smoke`
- `sports-alert-reliability-validate`
- `xrp-15m-market-diagnostic`
- `crypto-rollover-optimizer-test`
- `crypto-target-lifecycle-cache-test`
- `crypto-execution-reliability-test`
- `kp-unemployment-live-probe-q1` — live-capable naming means explicit workstream confirmation is required before removal.
- `crypto-maker-live-probe-q1` — live-capable naming means explicit workstream confirmation is required before removal.

### SUPERSEDED candidates — likely replacements exist, but dependency verification still required

- `crypto-transport-latency-shadow` → `crypto-transport-latency-east-shadow` / `crypto-transport-latency-east-v2-shadow`
- `crypto-transport-latency-east-shadow` → `crypto-transport-latency-east-v2-shadow`
- `crypto-15m-settlement-ledger-shadow` → `crypto-15m-settlement-ledger-v2-shadow`
- `xrp-settlement-reconcile-temp` → `xrp-settlement-reconcile-alpine`
- `three-venue-sports-router-paper` → `three-venue-router-paper-v2`; old service failed at 20:11 UTC and v2 succeeded later at 21:56 UTC on Aug 21.

### UNKNOWN — preserve pending ownership/dependency check

All remaining services are UNKNOWN for cleanup purposes, including older MLB game/prop shadows and supervisors, KGP micro variants, Crypto.com discovery shadows, rule-fingerprint/certification shadows, settlement-risk/opportunity-audit shadows, feed validators, and older three-venue paper services. Names/status alone are insufficient evidence for deletion.

Two especially strong dead-placeholder candidates were investigated further:

- `mlb-game-paper-v2` — Railway shows **no deployment ever**; GitHub repo search found no reference by service name.
- `sports-kgp-real-branch-shadow` — Railway shows **no deployment ever**; GitHub repo search found no reference by service name.

The Railway dependency-check/delete agent timed out on the broad audit and again on narrow per-service checks. Because current environment/service references could not be conclusively inspected, these two remain **UNKNOWN rather than deleted**. No services were removed in this checkpoint. This preserves the fail-safe cleanup rule and avoids disrupting active chats/workstreams.

## Realized P&L truth

This snapshot does **not** establish a new reconciled realized trade or realized P&L figure. Before reporting realized dollars/day, query the authoritative ledger/reconciliation output and distinguish:

1. candidate/shadow opportunity,
2. execution attempt,
3. single-leg fill or ambiguous exposure,
4. both-filled trade,
5. settled/reconciled realized P&L.

Stale quotes, mismatched settlement rules, shadow P&L, and unconfirmed fills never count as realized arbitrage profit.

## New-chat startup protocol

Every ARB workstream chat should begin with this sequence:

1. Read `ARB_STATUS.md`.
2. Query current Railway deployment + runtime logs for the specific service being worked on.
3. Query the relevant GitHub branch/PR/commit and compare it with the Railway deployment commit.
4. State the exact current rung: PAPER/SHADOW, LIVE PROBE, EXECUTABLE, EXECUTED, or REALIZED.
5. Work one production blocker through **inspect → fix → deploy → verify → reconcile → checkpoint** before expanding scope.

If production disagrees with this file, production wins and this file must be updated.

## Checkpoint protocol

After any meaningful change, append/update the relevant row with:

- UTC verification time,
- service + deployment ID,
- deployed commit/branch,
- armed/disarmed/order-entry state,
- heartbeat/candidate/execution/fill/reconciliation result,
- blocker or next action.

Do not store API keys, credentials, private signing material, or sensitive balances in this file.

## Railway cleanup rule

Classify services before deletion:

- **CORE/LIVE** — current production dependency; keep.
- **ACTIVE SHADOW** — actively producing evidence for a current workstream; keep until superseded.
- **ONE-OFF PROBE/TEST** — finished diagnostic with no current dependency; candidate for removal.
- **SUPERSEDED** — replaced by a newer verified service; candidate for removal.
- **UNKNOWN** — do not delete until ownership/dependencies are established.

The cleanup goal is fewer production surfaces without disrupting current sports, crypto, weather, ledger, alerting, or reconciliation paths.
