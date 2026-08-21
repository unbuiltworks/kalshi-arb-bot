# ARB Control Plane

Last verified: 2026-08-21 23:46 UTC

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
| Direct sports K↔P | `sports-kp-live-executor` | DISARMED / NOT EXECUTABLE | Latest successful deployment `3ab49a47-c15f-49bd-ba32-72cab032b6be`, branch `feature/sports-kp-live-executor-impl`, commit `0284c141f5cb706ecf7772e08acf3541fc386e21`. Runtime reported `approved_contracts=0`, `joined_execution_contracts=0`, `live_env_gate=false`, `arm_token_present=false`, `order_entry=false`. | Approval registry is empty because identity alone is unsafe. The audited MLB K↔P game-total example fails exact settlement equivalence on official-source/minimum-completion/interruption/cancellation semantics. Certify only exact rule-equivalent lanes; do not populate approvals mechanically. |
| MLB prop rule-equivalence lane | current shared code / prior `sports-gk-multifamily-live-canary` evidence | CONDITIONAL / NOT CERTIFIED FOR GUARANTEED ARB | `mlb_prop_rule_equivalence.py` establishes that batter props can only converge after confirmed start + first plate appearance and pitcher strikeouts after confirmed starting pitcher + first pitch. The prior G↔K live canary already consumed official MLB boxscores for this participation gate, but its own code labels the lane `LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB` because Gemini exceptional-event lifecycle is not fully fingerprint-certified. | Highest-impact certification target: reuse the official MLB participation proof in the current counterpart pipeline, then require exact remaining venue rule/source/timing equivalence before execution. Do **not** revive the expired Aug. 19 canary as guaranteed arbitrage. |
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

## Sports certification findings — 2026-08-21 23:36 UTC

- **Live Polymarket discovery is not the blocker:** the combo service sees ~21–23 accepting live sports events per cycle, including 5 MLB and 1 NFL at the latest checks.
- **Kalshi manufactured-market plumbing is now working:** the live shadow run generated/resolved and retained 8 MVE markets and continued under the persistent daily creation budget without Kalshi 429s in the sampled cycle.
- **Identity matching is not certification:** 46 exact K↔P identity matches and 124 exact K↔Gemini MLB-prop joins were observed, but those do not establish settlement equivalence.
- **Current combo economics are not positive:** best sampled generated combo conservative q=1 locked profit was `-0.0340`; best inverse was `-0.0690`; positive economic candidates were 0.
- **Known K↔P game-total semantics can diverge:** the existing fingerprint probe rejected an Athletics/Kansas City total because official-source, minimum-completion, not-started, interrupted, and cancellation branches differ. This is evidence against blanket approval of exact-looking game totals.
- **MLB props have a narrower path:** participation-specific differences can disappear after official MLB game-state proof, but source/timing/exceptional-event semantics must still be exact before the pair can be called certified/executable.
- **No new sports trade was executed or realized by this checkpoint.** Shadow candidates and generated MVE quotes remain research evidence only.

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
