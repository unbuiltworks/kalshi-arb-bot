# ARB Control Plane

Last verified: 2026-08-22 00:16 UTC

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
| Direct sports K↔P | `sports-kp-live-executor` | **LIVE READ-ONLY CERTIFICATION SCAN / NOT EXECUTABLE** | Last verified production deployment remains `1e3a8777-347f-47da-9bb2-d87fce57cd96`, `SUCCESS`, commit `e522c15129ab59bb67153f29eaf02bd7f6b2550e`: 0 approvals, 12 read-only observation contracts, 6 Poly + 6 Kalshi books, REST series + exact final Kalshi transport, no live env gate/arm/order entry. A fresh pre-change log query through ~00:14 UTC still found 0 `SPORTS_KP_CERTIFICATION_CANDIDATE` rows. Branch head `d818ba3a4f9df09de133d82e39ab9f910c8ceb75` adds diagnostic-only near-miss telemetry; Railway pickup is **not yet verified** because the Railway log connector failed at the resource layer after commit. | No current row is known to clear all >=3¢ after-fee/freshness/skew/persistence gates. Near-miss telemetry will quantify edge vs skew vs fresh-pair vs persistence suppression without loosening gates; verify deployment/runtime before acting on it. Never approve exact identity alone. |
| MLB prop rule-equivalence lane | current shared code / `sports-gk-autonomous-supervisor` | **LIVE RULE-RISK PROBE / REALIZED P&L EXISTS / NOT CERTIFIED GUARANTEED ARB** | Latest verified runtime resolved to deployment `32e0d8fb-81ec-42de-8fd5-608876dafc4b`, `SUCCESS`, after commits `fdbf12c...`, `7c8157b...`, `fbaea371...`. The authoritative ledger shows five `SETTLED_COMPATIBLE`, `realized=true`, two-leg G↔K probes with cumulative **net P&L $0.1871** (gross $0.27; recorded fees $0.0829). Cumulative conservative probe-budget usage is $4.84 of $5, leaving $0.16. | These are real settled live results but remain **rule-risk**, not certified arbitrage, because Gemini exceptional-event/source/timing equivalence is not fully fingerprint-certified. Do not silently expand the $5 risk budget. Four historical Taylor Ward fills used the same settlement key; current duplicate-exposure guard is intended to prevent repeats and now has a dedicated regression test commit `9d389843...`. |
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
  - `d818ba3a4f9df09de133d82e39ab9f910c8ceb75` — add diagnostic-only 30-second K↔P near-miss summaries (`edge_below_threshold`, `quote_skew`, `fresh_pair_unavailable`, `persistence`, etc.) plus best unit profit observed. No execution gate changed. Production deployment verification is pending.
- Sports G↔K supervisor hardening on `feature/sports-gk-autonomous-supervisor`:
  - `fdbf12cbdc2b9b3d070fc8d6c02e24461a40ca58` — bounded retry for transient Gemini/Kalshi discovery transport failures, with no stale fallback.
  - `7c8157b7ca48627a61556f5c59e193be9291a587` — expose non-sensitive cumulative sports-budget contributors and reuse one authoritative ledger read per epoch.
  - `fbaea37182727ad0eb78d020368844fc3d85c66e` — include authoritative realized/legs/fees/gross/net fields in the budget reconciliation output.
  - `9d389843c7cc37394d737c5787a4d11b641d3828` — add regression tests proving filled/settled exact settlement keys and active reservations block duplicate live exposure, while only zero-fill released reservations may retry. No GitHub Actions workflow is attached to this branch, so CI validation is not claimed.
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

## Sports certification findings — 2026-08-22 00:16 UTC

- **Live Polymarket discovery is not the blocker:** the combo service sees ~21–23 accepting live sports events per cycle, including MLB/NFL inventory in the sampled runs.
- **Kalshi manufactured-market plumbing is working:** the combo shadow generated/resolved and retained 8 MVE markets under its persistent creation budget.
- **Identity matching is not certification:** 46 exact K↔P identity matches and 124 exact K↔Gemini MLB-prop joins were observed, but those do not establish settlement equivalence.
- **Current combo economics are not positive:** best sampled generated combo conservative q=1 locked profit was `-0.0340`; best inverse was `-0.0690`; positive economic candidates were 0.
- **Direct K↔P remains fail-closed and had no current economic certification candidate before the diagnostic commit:** a fresh production log query found 0 `SPORTS_KP_CERTIFICATION_CANDIDATE` rows. There are still 0 approvals and no direct order-entry authorization.
- **K↔P diagnostic visibility is improved in branch code without changing risk:** commit `d818ba3...` records rejection counts and best observed unit profit while retaining the same 3¢/50ms/100ms/persistence/rule/arm/final-depth gates. Railway deployment/runtime verification is pending because the connector log action failed after the commit; do not assume it is live yet.
- **G↔K supervisor current scanning is broad but recent epochs did not force trades:** observed epochs had 933–1000 exact joins and multiple 600-second `NO_ATTEMPT` outcomes. One transient Gemini incomplete-chunk error was fixed with bounded transport retries; no stale fallback was introduced.
- **Authoritative realized sports result now exists:** five historical G↔K probes are `SETTLED_COMPATIBLE`, `realized=true`, and `legs_filled=2`. Four Taylor Ward HR>=1 probes each realized `$0.0379` net; one Nathan Church Hits>=2 probe realized `$0.0355` net. Aggregate gross P&L `$0.27`, recorded fees `$0.0829`, aggregate **net realized P&L `$0.1871`**.
- **Classification matters:** that `$0.1871` is genuine settled live P&L, but it was earned under `LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB`; it must not be represented as certified guaranteed arbitrage P&L until the remaining Gemini/Kalshi settlement semantics are fingerprint-equivalent.
- **Sports probe capital is effectively exhausted:** conservative cumulative budget usage is `$4.84 / $5.00`, leaving `$0.16`. A normal ~90¢–$1 q=1 candidate should fail closed on budget reservation. Do not reset or increase the ceiling silently.
- **Legacy duplicate exposure was uncovered and guarded by regression tests:** four realized Taylor Ward probes share the same exact settlement key. Current duplicate-settlement logic blocks any prior actual fill permanently for the canary regime and blocks active reservations; `9d389843...` captures that behavior in tests before any future live-budget increase.
- **Known K↔P game-total semantics can diverge:** the existing fingerprint probe rejected an Athletics/Kansas City total because official-source, minimum-completion, not-started, interrupted, and cancellation branches differ. This remains evidence against blanket approval of exact-looking game totals.
- **MLB props have a narrower path:** participation-specific differences can disappear after official MLB game-state proof, but source/timing/exceptional-event semantics must still be exact before the pair can be called certified/executable guaranteed arb.

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

Authoritative sports reconciliation now establishes **$0.1871 cumulative realized net P&L** from five settled, both-filled Gemini/Kalshi live probes (gross `$0.27`, recorded fees `$0.0829`). This is real realized P&L and may be counted as realized dollars, but it is **not yet certified guaranteed-arbitrage P&L** because those probes remain in the rule-risk classification.

Before reporting dollars/day, continue to distinguish:

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