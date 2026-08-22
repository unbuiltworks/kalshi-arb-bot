# ARB Control Plane

Last verified: 2026-08-22 00:38 UTC

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
| Direct sports K↔P | `sports-kp-live-executor` | **LIVE READ-ONLY CERTIFICATION SCAN / NOT EXECUTABLE** | Deployment `ad6d8c60-5806-46be-b8c3-cad809d6b35e` is `SUCCESS` on branch `feature/sports-kp-live-executor-impl`, commit `f9dfd28669e989760070a44d70feeab031f7efe5`. Prior diagnostic commit `d818ba3...` showed `fresh_pair_unavailable` dominating 30-second windows (hundreds per window), while best observed after-fee unit profit briefly reached **+0.0250**, just below the unchanged +0.03 gate. `f9dfd286...` now rate-bounds Polymarket-triggered exact Kalshi depth=1 synchronization at 250ms globally; production logs show repeated exact Kalshi revalidations with HTTP 200 and no observed 429 in the sampled startup window. | Verify the first post-change 30-second near-miss summaries and any `SPORTS_KP_CERTIFICATION_CANDIDATE`. Do not loosen 3¢ after-fee, 50ms skew, 100ms freshness, persistence, rule-fingerprint, or final-depth gates. There are still 0 execution approvals, so even a qualified economic row remains certification-only until exact settlement rules are audited and approved. |
| MLB prop G↔K | `sports-gk-autonomous-supervisor` | **LIVE RULE-RISK PROBE / REALIZED P&L EXISTS / NOT CERTIFIED GUARANTEED ARB** | Production deployment `38ae97c8-742a-4d49-8b70-cff80b6ad87f` is `SUCCESS` on commit `9d389843...`. Five settled both-filled historical probes total **+$0.1871 net realized P&L**; authoritative sports probe budget usage remains **$4.84/$5.00**, leaving $0.16. | Do not silently increase/reset risk budget; a normal q=1 candidate should fail closed on remaining budget. Full settlement-rule certification still required. |
| Weather | `weather-live-probe` | Needs fresh reconciliation | Service exists. | Reconcile prior order state before any new live order. |
| Command/observability | `arb-command-center` | Present | Service exists. | Consolidate operational heartbeat/status here where practical. |
| Railway hygiene | project `sports-arb-autonomous` | Cleanup in progress | Prior inventory contained 88 services. | Remove only verified dead/test/superseded services; preserve active/core workstreams. |

## Sports K↔P — 2026-08-22 00:38 UTC

- `d818ba3a4f9df09de133d82e39ab9f910c8ceb75` was verified deployed successfully before modification. Its new `SPORTS_KP_NEAR_MISS_SUMMARY` telemetry established that the dominant rejection reason was **fresh_pair_unavailable**, commonly 300-800+ rejections per 30-second window.
- Economics were not entirely absent: the best sampled after-fee unit profit improved from negative values to **+0.0250**, which is only 0.5¢ below the unchanged +0.03 execution/certification threshold.
- Root cause of the freshness suppression: candidate scanning used low-rate Kalshi REST series snapshots while Polymarket arrived event-driven over WebSocket. With a strict <=100ms book-age gate, most Polymarket events did not have a simultaneously fresh Kalshi snapshot.
- `f9dfd28669e989760070a44d70feeab031f7efe5` changes timing, not safety: when a Polymarket event arrives and the strict pair is unavailable, the service may request one exact Kalshi depth=1 book, with a **global 250ms floor (max ~4 exact sync requests/sec)**. The recomputed pair must still pass the same <=100ms freshness and <=50ms inter-venue skew gates, then the same >=3¢ after-fee edge, persistence, confidence/rule, and final exact-depth revalidation gates.
- Railway deployment `ad6d8c60-5806-46be-b8c3-cad809d6b35e` successfully picked up `f9dfd286...`. One transient Gemini discovery incomplete-chunk error caused an immediate restart; the restart recovered without stale fallback and reached `SPORTS_KP_LIVE_READY` with 8 read-only observation contracts / 4 distinct Polymarket books / 4 Kalshi books.
- Production then emitted repeated `KALSHI_GAME_EXACT_REVALIDATION` HTTP-200 rows on Polymarket-triggered synchronization. No Kalshi 429 was observed in the sampled post-change startup window.
- There are still **0 approved K↔P execution contracts**, so direct K↔P remains read-only even if a >=3¢ economic row appears. Exact settlement-rule fingerprint certification is still required before any live order.

## Path to meaningful contribution toward $480/day

The q=1 canary rung is necessary for proof, but q=1 at a 3¢ floor cannot itself produce meaningful daily dollars. The production path is therefore:

1. **Increase executable opportunity count without weakening gates:** remove transport/freshness suppression, broaden certified MLB/NFL game/prop/combo overlap, and keep exact live-event discovery.
2. **Convert exact identity joins into rule-certified counterparts:** prioritize market families with the cleanest settlement semantics; do not blanket-certify totals/props with cancellation, official-source, participation, or timing mismatches.
3. **Prove repeated q=1 execution + settlement:** both legs filled and reconciled, not shadow or attempted P&L.
4. **Scale quantity only where final displayed depth and cross-venue capital support it:** q=5, then q=10/q=25+ using exact depth/capital reservations. A 3-5¢ edge becomes economically meaningful only when multiplied by depth and repeated opportunity count.
5. **Run multiple independent certified lanes:** direct K↔P, manufactured K↔P combos, certified G↔K, and eventually sportsbook counterparts. The target must be measured as aggregate realized net dollars/day, not theoretical edge count.

Current evidence says the limiting factor is still executable/certified throughput, not lack of code for larger quantities. Do not scale risk budgets or size before the q=1 counterpart/freshness/settlement proof is repeatable.

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
