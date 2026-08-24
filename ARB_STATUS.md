# ARB Control Plane

Last verified: 2026-08-24 23:22 UTC

This is the durable ARB handoff. **Production state wins over chat history.** Before changing any workstream, verify current Railway deployment/runtime and GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the **$428/day realized-net objective**. A candidate, modeled edge, submitted order, fill, hedge, reservation, or unsettled locked pair is not realized profit.

Historical control-plane detail through 2026-08-23 remains in `ARB_STATUS_HISTORY_2026-08-23.md`.

## LATEST PRODUCTION CHECKPOINT — 2026-08-24 23:22 UTC / ~7:22 PM ET

### Scoreboard

`QUALIFIED -> ORDER_SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`

- **Polymarket MLB same-venue:** live participation is now active. Latest clean production cycle saw **420 participation-ready pairs and 167 near-touch/priced candidates**. The recovered executor is cycling q=1 sticky maker attempts. Multiple attempts have aged through their sticky lifecycle and advanced to the next candidate with no confirmed fill yet. **0 confirmed new fills, 0 hedges, 0 settlements, $0 new realized.**
- **Same-Gemini crypto:** executor is live again and continuously scans the complete safe q1 pair universe. Latest scans have **0 >=3c immediate FOK/FOK candidates**; current whole-contract maker candidates have no qualifying recent exact flow and remain suppressed. **0 current fills, $0 new realized.**
- **Gemini<->Kalshi / general sports supervisor:** live and funded. Current three-venue full-game scan showed **190 priced quotes / 0 positive one-contract locks**. NFL G<->K scan showed **607 priced exact counterpart keys / 0 positive q1 locks**. No current-epoch new realized P&L.
- Historical reconciled Gemini<->Kalshi sports proof remains **7 settled-compatible trades, approximately +$0.2679 realized net total**. This is historical evidence only, not new P&L from tonight.

Do not combine modeled, shadow, reserved, submitted, filled-but-unsettled, or unconfirmed values with realized P&L.

## 7 PM MLB PRODUCTION INCIDENT — ROOT CAUSE FOUND AND FIXED

The earlier pregame explanation became stale once the first MLB games went live. At approximately **6:42 PM ET**, production correctly transitioned from `GAME_NOT_LIVE` to live participation and found:
- 12 participation-certified pairs;
- 2 near-touch candidates;
- a real Nick Sogard BOS-MIA hits ladder attempt.

The attempt reserved $1, then hit `MLB_V2_ATTEMPT_ERROR_SCOPED_CANCEL` / `RuntimeError` and the sports executor hard-halted.

### Root cause 1: silent private Polymarket WS was treated as fatal

The private Polymarket order WebSocket was connected/healthy but had not yet observed any private order updates. The outer verified wrapper re-raised a bootstrap `RuntimeError` when `stream.updates <= 0` instead of preserving the known order id and using authoritative REST recovery.

Fix now live:
- private WS remains primary;
- known order identity is preserved;
- if WS bootstrap is silent/fails, that exact order degrades to paced authoritative REST state reads;
- terminal ambiguity still hard-halts and retains reservation;
- no ambiguous state is converted to assumed zero fill.

### Authoritative zero-fill recovery

The 6:42 Nick Sogard reservation was not blindly released. Startup recovery checked:
- exact maker/hedge slugs;
- open account orders;
- authenticated Polymarket portfolio trade activity after the reservation timestamp.

At **7:05:57 PM ET**, exchange evidence proved no open order and no post-reservation trade activity, so the reservation was released as confirmed zero fill.

A later interrupted Nick Sogard attempt from the next build was also recovered the same way and confirmed zero fill before new order entry.

### Root cause 2: slow/invisible pre-order path

After WS recovery, production could reach hundreds of priced candidates but appeared frozen after pricing. Two hardening changes are now live:

1. **Bounded pre-mutation reads**
   - account balance read has a 12s safe timeout;
   - persistence recheck has a 12s safe timeout;
   - stage telemetry now logs `BALANCE_READ_START/OK/TIMEOUT` and `PERSISTENCE_START/DONE/TIMEOUT`;
   - timeouts before reservation safely skip rather than freezing the process.

2. **Fast authoritative startup orphan cleanup**
   - old cleanup batched ~1,600+ revenue slugs into roughly 48-68 authenticated `orders.list` calls;
   - current cleanup uses one unfiltered authenticated `orders.list()` call to obtain all account open orders, filters client-side to the revenue universe, and makes one verification read only if it canceled something;
   - latest startup: **1,683 revenue slugs, 0 account open orders, 1 authenticated list call, 0 unresolved**.

Sports live branch current source commit: `6817a82eef4202c3463b74f2763c368f3fed4c86`.
Sports verified production deployment: `1a4a32bc-77ea-4c62-8949-6978f83024cd` — **SUCCESS**.

### Current live MLB maker activity

Latest clean cycle at ~7:18 PM ET:
- strict markets: **1,683**;
- strict pairs: **1,695**;
- participation-ready pairs: **420**;
- games with live/final boxscore: **4**;
- near-touch/priced candidates: **167**;
- quote requests: 416;
- successful books: 246;
- empty books: 170;
- balance read: OK;
- startup account cleanup: clean.

Observed candidate progression after the fixes:
- Michael Moniak COL-WSH H+R+RBI ladder passed persistence and entered a sticky maker lifecycle;
- Wil Abreu BOS-MIA total-bases ladder passed persistence and spent ~70s in the execution lifecycle before advancing;
- Framber Valdez TB-DET strikeout ladder passed persistence and spent ~60s in the lifecycle before advancing;
- Kyle Stowers BOS-MIA H+R+RBI ladder was the next persistent candidate at the latest checkpoint.

The previous `GAME_NOT_LIVE` condition is **not** the current blocker. The current bottleneck is maker fill conversion / available contra flow at the safe >=3c frontier.

## POLYMARKET MLB SAME-VENUE MAKER -> FOK

Primary Railway service:
- service: `sports-kp-live-executor` (`bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`)
- branch: `feature/sports-kp-live-executor-impl`
- current source commit: `6817a82eef4202c3463b74f2763c368f3fed4c86`
- current verified deployment: `1a4a32bc-77ea-4c62-8949-6978f83024cd` — **SUCCESS**
- q=1
- minimum modeled locked profit: **$0.03**
- live rule-risk loss ceiling: **$5**, fixed; never silently reset/increase it.
- restart policy: bounded `ON_FAILURE`; intentional fail-closed returns still remain halted until the underlying state is safe.

Capabilities preserved:
- all strict same-player/stat threshold combinations within a ladder;
- first safe post-only bid / maximum-safe maker frontier;
- exact current Polymarket US fee math;
- near-touch is ranking, not admission;
- sticky maker while hedge economics remain valid;
- confirmed q1 maker fill -> q1 FOK sibling hedge;
- durable $1 reservation before mutation;
- private WS primary + bounded REST recovery;
- partial-fill / hedge-failure flatten + hard halt;
- first reconciled two-leg live canary stops for review;
- settlement required before realized P&L.

Participation remains mandatory:
- game LIVE;
- hitter starter + >=1 plate appearance;
- strikeout pitcher confirmed starting pitcher + >=1 batter faced;
- exact game/player/stat/threshold identity;
- markets remain `LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE`.

## SAME-GEMINI CRYPTO THRESHOLD LADDERS

Primary Railway service:
- service: `crypto-maker-live-probe-q1` (`6e0dffb2-8309-47cf-8196-9106eda284d8`)
- branch: `feature/maker-live-probe-q1`
- current deployment: `199b409e-dd2d-49e1-a7d0-184e5b92e7ad` — **SUCCESS**
- start: `python -m src.gemini_same_venue_crypto_execution_v6`
- q=1
- locked after-fee floor: **>= $0.03 per contract**
- restart policy changed from `NEVER` to bounded `ON_FAILURE` (max 10) after a transport/library crash left the service dead.

Do **not** infer that the underlying WebSocket masking `TypeError` is fully root-caused yet. Availability is restored; account gate still reconciles before any mutation, and the failure has not recurred in the current deployment.

Latest startup/current state:
- targeted tests: **28 passed**;
- account gate: active orders 0, recent filled maker orphans 0, orphan makers canceled 0;
- startup unresolved exposure: none;
- strict contracts: ~254;
- fractional 0.01-grid maker contracts excluded: 209;
- current books healthy: 254/254;
- monotone q1 pair universe: about 2,430;
- current >=3c FOK/FOK candidates: **0**;
- robust whole-contract maker candidates with qualifying recent exact flow: **0**;
- dead/no-flow candidates remain suppressed.

Fractional resting-maker path remains closed: production worst-partial-fill census found **0 safe fractional maker directions** at the >=3c/contract floor because 0.01 partial fills plus cent-rounded fees destroy the guarantee. Fractional q1 contracts remain eligible only for immediate all-or-none FOK/FOK execution.

Old Gemini<->Kalshi 15-minute basis lane remains parked because settlement-index basis risk is not guaranteed arbitrage.

## GEMINI <-> KALSHI / GENERAL SPORTS SUPERVISOR

Service: `sports-gk-autonomous-supervisor` (`0238eada-ced4-4927-8150-0ccce2e1a0a4`).

Current observed capital snapshot:
- Gemini: about **$62.24**;
- Kalshi: about **$3,405.71**;
- free buffer: $5.

Current scans around 7:21 PM ET:
- three-venue MLB/full-game: 99 normalized counterpart keys, 190 priced quotes, **0 one-contract positive locks**;
- NFL Gemini<->Kalshi: ~1,082 exact counterpart keys, 607 priced q1 keys, **0 positive q1 locks / 0 >=3c locks**.

Do not weaken rule fingerprints or quote-skew constraints to manufacture trades.

Historical reconciled evidence: seven compatible G<->K sports trades total approximately **+$0.2679 realized net**.

## OTHER LANES / SAFETY

### FanDuel / sportsbook

Prior apparent hundreds of sportsbook rows were contaminated by cross-game matching. Current physical-event matching requires scheduled starts within the approved window. Do not reuse the old false-positive count as opportunity supply.

### Crypto basis

Do not revive the Gemini<->Kalshi 15-minute basis lane as guaranteed arbitrage. Settlement-index mismatch created directional basis risk and a prior reconciled one-leg loss.

### Weather / economic events

Do not call modeled EV or one-sided rows arbitrage. Execution requires settlement/rule/fee certification and complementary executable legs.

## OPERATING PRIORITY FROM THIS CHECKPOINT

1. **Do not revisit the stale “games have not started” diagnosis. MLB participation is live and the Polymarket executor is actively cycling candidates.**
2. Measure the now-live Polymarket maker funnel as: persistent candidate -> reservation -> maker submitted -> maker fill -> FOK hedge -> settlement. The immediate optimization target is **fill conversion**, not scanner count.
3. Add/retain explicit maker-order-id and terminal-attempt telemetry so submitted-order counts never have to be inferred from elapsed sticky time.
4. Rank safe candidates by real trade flow, recent exact volume, queue position, spread/proximity and hedge stability. Do not force makers into empty books merely to create order count.
5. Keep same-Gemini v6 continuously scanning the complete safe FOK universe and flow-qualified whole-contract makers; its current absence of trades is economic, not scanner coverage.
6. Keep the G<->K supervisor scanning live MLB and pregame/live football, but do not execute non-certified normal-event matches.
7. Maintain q1 until the complete maker fill -> hedge -> settlement -> realized lifecycle is proven cleanly. Scale only from measured fill conversion and hedge reliability.
8. Scoreboard remains authoritative: `QUALIFIED -> ORDER_SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`.

Do not lower the 3c after-fee floor, weaken settlement/rule certification, bypass participation, silently increase/reset the $5 Polymarket MLB rule-risk ceiling, release ambiguous reservations, count shadow/locked/unsettled P&L as realized, or mix this infrastructure with the unrelated SOL executor.

Never store credentials, signing material, API keys, or other secrets here.