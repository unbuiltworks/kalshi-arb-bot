# ARB Control Plane

Last verified: 2026-08-21 23:21 UTC

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
| Crypto 15m BTC/ETH/SOL/XRP | `crypto-15m-sol-live-canary-10bp-50ms` | LIVE service alive and scanning | Deployment `6859fef6-7cd7-4fad-8776-f01cb79f6421` succeeded from commit `5b4c5fa6b4819cf59961060fc3e3d64120bedbf6`; runtime is actively polling BTC/ETH/SOL/XRP 15m books | Repeated Kalshi REST 429s are occurring during fallback book polling. Fix pacing/cache/shared-book acquisition without weakening q=1, fee, survival, capital, settlement, or fail-closed gates. Do not count a running process as a trade. |
| Sports K↔P combo | `sports-kp-combo-shadow` | SHADOW scanner alive | Deployment `85b1e835-2ad9-4771-ba2f-e7dc10180ba7` succeeded from commit `2f0a00d0b222c279965137ec69410e05ec8c14f8`; heartbeat saw 21 live Polymarket sports events, including 5 MLB and 1 NFL | Kalshi MVE heartbeat reported `listed_executable=0`, `generated_cached=0`, `combined_executable=0`; order entry remains false. Continue counterpart discovery/rule certification rather than merely waiting. |
| Direct sports K↔P | `sports-kp-live-executor` | Needs fresh verification before use | Service exists in production | Re-check exact rule-approved contract inventory, freshness, after-fee edge, capital, and q=1 bounded arm state before any live trade. |
| Weather | `weather-live-probe` | Needs fresh reconciliation | Service exists in production | Reconcile prior weather order state before any new live order; do not infer fill from deployment history. |
| Command/observability | `arb-command-center` | Present | Service exists in production | Consolidate status/heartbeat surfaces here where practical so chats can read one operational summary instead of dozens of services. |
| Railway hygiene | project `sports-arb-autonomous` | Service sprawl | Production currently contains dozens of probe/test/shadow services | Inventory by owner/purpose/latest deployment; remove only clearly dead/test/superseded services after confirming no active chat/workstream writes to them. Preserve live/core services. |

## Current GitHub work

Repository: `unbuiltworks/kalshi-arb-bot`

- Crypto continuous supervised rollover: PR #107 merged into `feature/sol-15m-live-canary-10bp-50ms`; production deployment above uses commit `5b4c5fa6b4819cf59961060fc3e3d64120bedbf6`.
- Crypto safe self-recovery: PR #106 merged.
- Crypto rollover planner: PR #105 merged.
- Sports combo synthetic shadow: PR #104 remains open/draft; current Railway combo service is on head `2f0a00d0b222c279965137ec69410e05ec8c14f8`.
- q5/q10/dynamic-capacity work remains separate from proof of current q=1 realized profitability. Do not promote size merely because code exists.

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
