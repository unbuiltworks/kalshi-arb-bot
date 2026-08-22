# ARB Control Plane

Last verified: 2026-08-22 15:51 UTC

This is the durable ARB handoff. Production state wins over chat history. Before changing a workstream, verify current Railway deployment/runtime + GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective.

## State definitions

- **PAPER/SHADOW**: observe/simulate only; no orders.
- **LIVE PROBE**: bounded real-order capability.
- **EXECUTABLE**: exact counterpart/rules + fresh after-fee economics/depth/capital.
- **EXECUTED**: all required legs confirmed filled.
- **REALIZED**: executed trade settled/reconciled and net P&L confirmed.

Never count stale quotes, mismatched settlement rules, shadow P&L, armed state, attempted orders, MVE creation/RFQs, or unconfirmed fills as realized arbitrage profit.

## Critical crypto incident — 2026-08-22 05:30 UTC

The Gemini/Kalshi 15-minute crypto taker lane is **NOT settlement-equivalent and must remain fail-closed**.

Authoritative ledger incident:
- trade `gk15m-gk15m-08220515-54da-01`;
- asset BTC, q=1;
- both Gemini and Kalshi legs filled;
- actual deployed capital `$0.885300`;
- settlement: Gemini outcome `yes`, Kalshi outcome `no`;
- `venue_outcomes_agree=false`;
- event recorded as `REALIZED_INDEX_MISMATCH`;
- realized net P&L **-$0.885300**.

External rule/source confirmation:
- Kalshi KXBTC15M uses CF Benchmarks BRTI and settles from the simple average of the final 60 one-second RTI observations before expiry.
- Gemini Prediction crypto events expose `sourceDetails` / deprecated `source`; Gemini documentation examples identify the crypto source as Kaiko `GRR-KAIKO_BTCUSD_60S`.
- Therefore Gemini/Kalshi price agreement, matching duration, matching strike direction, and the existing boundary gates do **not** prove identical settlement outcomes.

Immediate safety response:
- PR #111 added a boot-level settlement-equivalence arm.
- PR #112 moved the incident lock into the shared `live_probe_authorized()` transport guard with code-level settlement equivalence hard-false; environment flags alone cannot re-enable q1/q5.
- Do **not** re-enable until exact source/index/threshold/averaging/rounding/exception rules are independently certified for the exact traded family.
- q5 promotion remains blocked.
- Existing q=1 >=3c after-fee, >=10bp boundary, 50ms survival/skew, freshness, capital reservation, Gemini-first execution, reconciliation, rollover, and hard-halt gates remain unchanged.

Fresh 15:51 UTC evidence:
- Railway runtime access became unavailable again during this supervisor run, so no direct live crypto process/config inspection was possible;
- authoritative ledger still has **no new crypto submission after 2026-08-22 06:40:03 UTC**;
- unresolved ETH `gk15m-gk15m-08220615-c225-02` still has two fills + `ORDER_SUBMITTED/BOTH_FILLED` and no settlement/`TRADE_REALIZED` row;
- do not use the crypto service failure or the prior bounded-campaign diagnosis as a reason to bypass the settlement-equivalence transport kill.

## Crypto bounded-campaign diagnosis / rollover

The prior dormancy root cause was real and has been addressed separately:
- the live executor is a bounded campaign;
- campaign end conditions include `SOL_CANARY_V2_ARM_NOT_AFTER_UTC`, `CRYPTO_CANARY_MAX_TRADES` (default 5), and `CRYPTO_CANARY_MAX_EXECUTION_ATTEMPTS` (default 25);
- PRs #105-#107 added fail-closed diagnosis and supervised continuous rollover;
- PR #109 fixed benign no-claim deadline expiry so a lane merely waiting for an opportunity does not globally halt the campaign;
- do not reuse expired arm tokens or campaign IDs;
- continuous rollover still requires terminal clean local state, no unresolved attempt claim, no reservations/unsettled capital, successful venue reconciliation, and zero resting orders.

This rollover work is no longer the highest-priority crypto blocker. Settlement/threshold equivalence is.

## Current realized P&L truth

Authoritative `arb-trade-ledger` as of 15:51 UTC:
- 2026-08-22 ET day (from 04:00 UTC): `gemini_kalshi_15m_crypto` has **6 realized events, net -$0.618600**.
- These include small BTC/XRP/SOL wins offset by the -$0.885300 BTC index-mismatch loss.
- Trade `gk15m-gk15m-08220615-c225-02` (ETH) remains **UNRESOLVED**: two FILL rows + `ORDER_SUBMITTED/BOTH_FILLED` at 06:40:03 UTC, but no settlement/`TRADE_REALIZED` row. Exclude it from P&L.
- No sports or weather realized event has appeared in the ledger during this ET-day window.

Do not describe prior Gemini/Kalshi crypto wins as guaranteed-arbitrage evidence; the settlement and threshold mismatch proves basis risk can dominate the apparent execution lock.

## Crypto research / maker / q5

- `crypto-maker-arb-shadow-v1` remains SHADOW / `INDEX_MISMATCH_RESEARCH_ONLY`; live maker disarmed.
- q5 remains **BLOCKED / DO NOT ARM**.
- PR #116 `Add read-only crypto threshold dominance study` is open and mergeable on `feature/crypto-settlement-basis-v2`; it is public-data-only and does not authorize trading. It specifically studies threshold gaps and orientation safety in addition to settlement-index basis.

## Sports manufactured MVE combo

Service `sports-kp-combo-shadow` remains the highest-value safe sports manufacturing path.

Fresh 15:51 UTC evidence:
- active branch `feature/kp-combo-synthetic-shadow` advanced via merged PR #117 to commit `5f6f64a65ff5dada9a4ea29696c969111b432a5d`;
- GitHub Railway status for `sports-kp-combo-shadow` on that merge is **success**;
- the stable Railway entrypoint remains supervisor **v40**;
- v40 is read-only and adds signed-auth probing against the Polymarket exchange/RFQ host, then runs v39-v31;
- v37 scans exact same-Kalshi-market `YES + NO = $1` partitions and labels a shadow candidate executable only when q=1 YES ask + NO ask + conservative taker fees leaves **>= $0.03 locked profit** with depth on both sides;
- because both legs are the exact same Kalshi binary market, this path has no cross-venue settlement-equivalence problem;
- authoritative Neon had **no post-deploy telemetry row yet at 15:51 UTC**, so the first cycle after PR #117 is not independently proven even though deployment succeeded.

Preserve the >=3c after-fee lock, exact same-market/rule identity, exact final depth, freshness/skew where applicable, persistence, capital reservation, one-attempt, flatten/hard-halt, and settlement accounting. Shadow scan output is not a trade.

### Same-market observability milestone — promoted 2026-08-22 15:51 UTC

PR #117 `Add same-Kalshi-market scan ledger telemetry` is **merged** after full CI passed and Railway reports the `sports-kp-combo-shadow` deployment **success**.

The promoted telemetry:
- records same-market scan count, >=3c candidate count, best locked profit, ticker, all-in cost, and exact side depth into the existing non-P&L sports telemetry stream;
- cannot authorize orders or count as realized P&L;
- swallows telemetry failures so observation cannot change scan/execution behavior;
- changes no live order path or execution gate.

The next proof point is the first post-deploy cycle. Until an authoritative telemetry row appears, do not claim the production scanner has completed a same-market cycle or found an executable candidate.

## Same-Gemini extreme combo live canary — 2026-08-22 15:24 UTC

A dedicated isolated one-shot service is deployed and armed:
- service `gemini-extreme-combo-live-q1`, service id `4399e98f-ce33-4a74-99f2-900f7615beab`;
- branch `feature/three-venue-sports-router`;
- code commit `3d8f242f5572df8970fbed892e8a5989d94fd362` (`Add bounded q1 Gemini extreme combo live canary`);
- armed deployment `cbf23911-4a34-42bd-b704-3d9648d4dc3a` **SUCCESS**;
- campaign `gcombo-2608221121-a1`;
- q=1 only, combo `GEMI-CMB-260823-8EC8FBCF7988E34C-CMB`, maker BUY YES price `$0.90`, conservative target lock `>= $0.03`, maker TTL 120s;
- restart policy `NEVER`; credentials are referenced through Railway and are not stored here.

Rule/economics basis:
- this is a same-Gemini cover, not a Gemini/Kalshi settlement-equivalence trade;
- certified combo rule uses the product of component settlement values;
- exact complement hedges preserve `AB + (1-A) + (1-B) >= 1`, including scalar component settlements;
- the canary pre-hedges both components q=1 FOK at max `$0.02` each before placing the maker combo; failure/ambiguity paths flatten or hard-halt.

Fresh execution evidence:
- read-only preflight deployment `2f1c2436-2bf6-47d0-9a01-9fa41aff01cf` at ~15:22 UTC: terms accepted, no active combo order, but **both exact hedge books had no executable offer**, so `preflight_pass=false`;
- armed deployment at 15:24 UTC re-ran the live preflight with `armed=true` and again found both hedge outcomes unavailable;
- final classification **`LIVE_GATE_BLOCKED`**;
- `order_entry=false`;
- **0 orders submitted, 0 fills, 0 executed trades, $0 realized P&L** from this campaign.

Do not describe this as an executed trade. `armed=true` only means live mutation was authorized; the fresh economics/depth gate correctly prevented capital deployment. The immediate blocker is fresh q=1 liquidity on both exact hedge outcomes or a newly accepted rule-certified same-Gemini combo whose live books preserve the >=3c floor.

## Direct sports K↔P

Service `sports-kp-live-executor` remains read-only certification scan / not executable unless an exact counterpart has certified settlement rules and >=3c after-fee economics. NFL full-game totals previously failed exceptional-event lifecycle equivalence; do not promote line/identity matches alone.

## Sports G↔K props

Service `sports-gk-autonomous-supervisor` remains LIVE RULE-RISK PROBE / not certified guaranteed arb. Historical realized total is +$0.1871. Authoritative rule-risk budget remains $4.84/$5.00. Do not reset or increase without explicit user authorization.

## Weather

Reconcile any unresolved historical weather order before new live execution. Preserve exact settlement-rule certification before trading.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms` (currently fail-closed), `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows while their workstreams are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, and `sports-alert-reliability-shadow`.

Preserve `gemini-extreme-combo-live-q1` while the same-Gemini live-canary workstream is active. It is a one-shot bounded live probe, not a recurring strategy service.

Remove only verified dead/test/superseded services after dependency checks. Never infer deadness from names alone.

## New-chat / next-run protocol

1. Read `ARB_STATUS.md`.
2. Attempt Railway runtime inspection first, but if unavailable do not bypass controls; use GitHub deployment status + authoritative ledger and keep crypto fail-closed.
3. Reconcile unresolved ETH `gk15m-gk15m-08220615-c225-02` before counting any result.
4. Verify no new Gemini/Kalshi crypto order/fill appeared after 06:40:03 UTC.
5. Keep Gemini/Kalshi q1/q5/maker order entry blocked unless exact settlement + threshold equivalence is certified.
6. For the same-Gemini combo lane, first check campaign `gcombo-2608221121-a1` for any prior order state, then obtain fresh books. The 15:24 armed attempt was `LIVE_GATE_BLOCKED` with zero order entry because both hedge outcomes were unavailable.
7. Do not weaken the >=3c floor. If both exact q1 hedges return at <=2c with depth and no prior campaign order exists, use a fresh unique campaign for the next one-shot canary rather than reusing an ambiguous/used campaign.
8. Check for the first PR #117 post-deploy `SPORTS_SUPERVISOR_TELEMETRY` row; if present, use its same-market best locked profit/candidate count to decide whether a bounded same-Kalshi q1 canary is justified.
9. Continue same-day/<=24h sports breadth and weather exact-rule opportunities without weakening certification.
10. Work one blocker through **inspect -> fix -> deploy -> verify -> reconcile -> checkpoint** before widening scope.

After meaningful changes, update this file with UTC verification time, deployment/commit, armed/disarmed state, execution/fill/settlement evidence, and next blocker. Never store credentials, signing material, API keys, or sensitive balances here.
