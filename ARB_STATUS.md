# ARB Control Plane

Last verified: 2026-08-22 00:31 UTC

This file is the durable handoff point for the ARB project. Production state wins over old chat history. New work must establish current Railway + GitHub state first, then update this file after meaningful changes.

## State definitions

- **PAPER/SHADOW**: observes or simulates only; no order entry.
- **LIVE PROBE**: bounded real-order capability with explicit safety gates.
- **EXECUTABLE**: exact counterpart/rule match plus fresh after-fee prices and sufficient depth/capital.
- **EXECUTED**: both required legs have confirmed fills.
- **REALIZED**: executed trade has been reconciled/settled and P&L is confirmed.

Only REALIZED P&L counts toward the $480/day objective. Never count stale quotes, mismatched settlement rules, shadow P&L, armed state, attempted orders, or unconfirmed fills as realized arbitrage profit.

## Current production snapshot

| Workstream | Railway service | Current state | Last verified evidence | Immediate blocker / next action |
| --- | --- | --- | --- | --- |
| Crypto 15m BTC/ETH/SOL/XRP | `crypto-15m-sol-live-canary-10bp-50ms` | **LIVE PROBE / REALIZED P&L EXISTS** | Deployment `3973a477-078d-4a97-bba8-c883e0786efe` on commit `275a889...` produced a verified XRP q=1 two-leg fill at ~23:50 UTC and settled/reconciled at 00:00:18 UTC for **+$0.088800 net realized P&L**. Supervisor then rolled to a fresh campaign. Deployment `4dc6424f-4f31-4912-8896-a9a019dc333c` verified commit `22afeae...`: 48 tests passed, private Gemini trading WS prewarmed on first attempt, all four books live. Deployment `884cb8d6-bdd0-4423-9b48-bf2d65b8715f` verified commit `627a9a9...`: Kalshi pacing/staggering produced hundreds of exact book events with **0 Kalshi reconnect/429 counts in the sampled minute**, but the fresh restart encountered persistent Gemini WS handshake 429s and therefore 0 Gemini events/evaluations in that sample. | Highest-frequency engineering path is reducing Gemini connection contention without weakening q=1, >=3¢ modeled after-fee profit, double-boundary, 50ms survival, <=250ms freshness, <=50ms skew, exact-payload, Gemini-first, settlement, or fail-closed gates. Branch head `4024f8f...` collapses prediction-book + basis-spot market data onto one Gemini socket and makes the private trading WS wait for market-data readiness. **Railway deployment/runtime of this latest head is NOT VERIFIED because the Railway app connector began returning `Link not found` after the commit. Reconnect Railway and verify before calling it live.** |
| Sports K↔P combo | `sports-kp-combo-shadow` | SHADOW | Last verified successful combo service saw live Polymarket inventory and retained generated Kalshi MVE markets, but no positive executable combo. | Continue pricing/certification; do not arm on identity matches alone. |
| Direct sports K↔P | `sports-kp-live-executor` | **LIVE READ-ONLY CERTIFICATION SCAN / NOT EXECUTABLE** | 0 approvals; exact rule fingerprint remains required. | Quantify near misses without loosening rule/freshness/persistence gates. |
| MLB prop G↔K | `sports-gk-autonomous-supervisor` | **LIVE RULE-RISK PROBE / REALIZED P&L EXISTS / NOT CERTIFIED GUARANTEED ARB** | Five settled both-filled historical probes total **+$0.1871 net realized P&L**; sports probe budget usage was $4.84/$5.00. | Do not silently increase risk budget; full settlement-rule certification still required. |
| Weather | `weather-live-probe` | Needs fresh reconciliation | Service exists. | Reconcile prior order state before any new live order. |
| Command/observability | `arb-command-center` | Present | Service exists. | Consolidate operational heartbeat/status here where practical. |
| Railway hygiene | project `sports-arb-autonomous` | Cleanup in progress | Prior inventory contained 88 services. | Remove only verified dead/test/superseded services; preserve active/core workstreams. |

## Crypto 15m — verified trade and frequency work

### Verified REALIZED trade

- Asset: **XRP**, q=1.
- First certified opportunity around 23:49:44 UTC was **not executed** because Gemini's private trading WebSocket handshake returned HTTP 429. Kalshi was correctly not sent because execution is Gemini-first.
- A second certified XRP opportunity around 23:50:05 UTC passed the existing execution gates and returned `BOTH_FILLED`.
- Gemini filled q=1 at approximately `$0.09`; Kalshi filled the counterpart at approximately `$0.20` YES-space equivalent. Existing hedge-cushion logic preserved the configured modeled profit floor.
- Settlement/reconciliation at 00:00:18 UTC recorded **net_pnl=0.088800**, venue outcomes agreed, and the supervisor created a fresh unique campaign afterward.
- This +$0.0888 is **REALIZED**. Do not infer additional fills from actionable/armed logs.

### Trade-frequency fixes on production branch `feature/sol-15m-live-canary-10bp-50ms`

- `b621709...` — fixed REST-fallback feed-stop recursion.
- `275a889...` — restored active four-asset evaluation using one authenticated Gemini `depth` RPC market-data socket.
- `22afeae7f4ef61a33bdf244cbc37c690bdd3eb8c` — moved one private Gemini trading WebSocket handshake off the actionable critical path by prewarming/reusing one session per bounded child. Verified on Railway deployment `4dc6424f-4f31-4912-8896-a9a019dc333c`; 48 tests passed and prewarm succeeded on attempt 1.
- `627a9a99daf026eff76705fc621408b0c6155cdf` — changed Kalshi exact REST books from four synchronized ~80ms loops to ~180ms per-asset polling with BTC/ETH/SOL/XRP staggering. Verified deployment `884cb8d6-bdd0-4423-9b48-bf2d65b8715f` showed hundreds of usable Kalshi book events with 0 reconnect/429 counts in the sampled summaries, versus roughly 2–3 429s/sec/asset before pacing.
- `4439638680019a9361b3066d182c8e96bd361904` — added process-local Gemini market-data readiness coordination.
- `cbaa6b3a7056f14a335e410c90633fe8c57a9b63` — private trading prewarm now waits for market-data readiness plus a short settle interval, so the execution socket does not race the scanner into Gemini's handshake throttle.
- `4024f8f0d56d7a3483a8ad40c9e9ce52c1e94d4c` — branch head: prediction-market `depth` RPC and the Gemini public-spot basis feed share one authenticated Gemini WS; the old third basis-spot connection is suppressed. The private execution connection still remains separate and fail-closed. **Not yet Railway-verified because the Railway connector lost its link immediately after commit.**

### Current crypto safety configuration — do not weaken merely to create trade count

- quantity q=1
- bounded campaign max trades 5
- max execution attempts 25
- hard experiment budget <=$5
- modeled after-fee locked profit floor >=$0.03
- double-boundary gate retained
- 50ms survival/execution-delay canary path retained
- quote freshness <=250ms and cross-venue skew <=50ms retained
- exact displayed price/depth and exact-payload recomputation retained
- Gemini-first terminal execution; Kalshi sent only after Gemini terminal fill
- one unresolved trade at a time; ambiguity hard-halts/reconciles fail-closed
- settlement/reconciliation required before REALIZED classification

## Realized P&L truth

- Crypto 15m verified realized net P&L: **+$0.0888**.
- Sports G↔K verified settled live net P&L: **+$0.1871**, but still classified as rule-risk rather than certified guaranteed arbitrage.
- Total verified realized live P&L currently tracked across those two workstreams: **+$0.2759**.

Do not describe the full $0.2759 as certified guaranteed-arbitrage P&L; the sports portion remains rule-risk. Continue to distinguish candidate/shadow, attempt, single-leg/ambiguous, both-filled, and settled/realized.

## Railway cleanup checkpoint

Preserve as CORE: `crypto-15m-sol-live-canary-10bp-50ms`, `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows while their workstreams remain active, including `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, `sports-alert-reliability-shadow`, and `crypto-maker-arb-shadow-v1`.

One-off/superseded-looking services remain deletion candidates only after dependency checks. No live/core deletion should be inferred from naming alone. `mlb-game-paper-v2` and `sports-kgp-real-branch-shadow` previously had no deployment history/repo-name reference but were left UNKNOWN because dependency verification did not complete.

## New-chat startup protocol

1. Read `ARB_STATUS.md`.
2. Query current Railway deployment + runtime logs for the specific service.
3. Query the relevant GitHub branch/commit and compare it with Railway's deployed commit.
4. State the exact rung: PAPER/SHADOW, LIVE PROBE, EXECUTABLE, EXECUTED, or REALIZED.
5. Work one blocker through **inspect → fix → deploy → verify → reconcile → checkpoint** before expanding scope.

If production disagrees with this file, production wins and this file must be updated.

## Checkpoint protocol

After a meaningful change record UTC verification time, service/deployment ID, deployed commit/branch, armed/disarmed state, heartbeat/candidate/execution/fill/reconciliation result, and the next blocker. Never store API keys, private signing material, credentials, or sensitive balances here.
