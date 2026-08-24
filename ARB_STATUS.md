# ARB Control Plane

Last verified: 2026-08-24 22:18 UTC

This is the durable ARB handoff. **Production state wins over chat history.** Before changing any workstream, verify current Railway deployment/runtime and GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the **$428/day realized-net objective**. A candidate, modeled edge, submitted order, fill, hedge, reservation, or unsettled locked pair is not realized profit.

Historical control-plane detail through 2026-08-23 remains in `ARB_STATUS_HISTORY_2026-08-23.md`. Earlier 2026-08-24 implementation detail is summarized below rather than treated as current production truth.

## LATEST PRODUCTION CHECKPOINT — 2026-08-24 22:18 UTC

### Scoreboard

Current new execution work:

`QUALIFIED -> ORDER_SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`

- same-Gemini crypto: qualified structural/frontier math exists, **no current executable-flow candidate**, no current order, 0 fills, 0 hedges, 0 settlements, **$0 new realized**;
- Polymarket same-venue MLB: pregame inventory is healthy, participation not yet certified, 0 current attempts/fills, **$0 new realized**;
- Gemini<->Kalshi MLB: exact joined inventory is healthy, participation not yet certified, 0 current-epoch attempts/fills, **$0 new realized**;
- historical reconciled Gemini<->Kalshi sports evidence: **7 settled-compatible trades, approximately +$0.2679 realized net total**. This is historical proof only, not today's new P&L.

Do not combine modeled, shadow, locked-but-unsettled, or unconfirmed values with realized P&L.

## SAME-GEMINI CRYPTO THRESHOLD LADDERS — LIVE EXECUTION V6

Primary Railway service:
- project: `sports-arb-autonomous` (`7e15f30d-1430-4514-8f7b-bb81c84d8f4d`)
- production environment: `b51b1a8c-eefa-4a8a-a7e9-63a0430dc4a2`
- service: `crypto-maker-live-probe-q1` (`6e0dffb2-8309-47cf-8196-9106eda284d8`)
- branch: `feature/maker-live-probe-q1`
- production source commit: `307fe09e58bfb4f162f1d3ae2d871a592a260d13`
- verified deployment: `18a7020c-db12-4dcc-9eeb-2b475fb58424` — **SUCCESS**
- start: `python -m src.gemini_same_venue_crypto_execution_v6`
- targeted predeploy: **28 passed**
- q=1
- locked after-fee floor: **>= $0.03 per contract**
- old Gemini<->Kalshi 15-minute basis maker remains parked because settlement-index basis risk is not guaranteed arbitrage.

### Private order lifecycle / orphan safety

The live path is now **WebSocket first** for private order state:
- `orders@account` is primary for maker state/fills;
- public books use 100ms depth WebSockets;
- REST is bounded authoritative recovery/audit only;
- a single REST timeout no longer causes an assumed zero-fill release;
- ambiguous state retains the capital reservation and hard-stops;
- startup audits the Gemini account itself before relying on Command Center state;
- any prior `crypto-gem-ladder-*` zero-fill orphan is authoritatively canceled before new maker mutation;
- any orphan with a fill hard-stops rather than being silently cleaned up;
- maker client IDs are restart-unique so a new process cannot collide with an earlier canary ID.

This fixed a real production failure mode: prior deployments had left active zero-fill XRP makers behind even though the ledger appeared clear. The account-authoritative gate found/canceled those orphans. Latest v6 startup is clean:
- active orders seen: **0**;
- recent filled maker orphans: **0**;
- orphan makers canceled on latest startup: **0**;
- unresolved startup exposure: **none**.

### Sticky maker behavior

There is no timer-only four-minute cancellation anymore. A live whole-contract maker remains resting while:
- hedge book stays fresh;
- hedge depth stays sufficient;
- exact fee-adjusted locked floor remains >=3c;
- market lifecycle remains valid;
- private account WebSocket remains healthy.

Cancel/recovery is caused by economics, depth, freshness, lifecycle, account-state failure, or fill — not elapsed time alone.

### Complete same-event pair coverage

The exact structural relationship is evaluated for **every certified L < H pair** in one Gemini event, not only adjacent strikes:

`BUY YES(L) + BUY NO(H)`

For L < H the payout floor is at least $1 across all states; non-adjacent pairs can pay $2 in the middle state but never create a payout hole. Exact source and expiry matching are still mandatory.

Latest production census:
- strict q1-compatible ladder contracts: **255**;
- adjacent pairs: **236**;
- all certified monotone L<H pairs: **2,438**;
- books requested / healthy: **255 / 255**.

### Immediate FOK/FOK taker lane

Immediate same-Gemini FOK/FOK now scans every q1-compatible contract grid, including 0.01-grid instruments. This does **not** relax the resting-maker rule. q=1 must be a valid exact instrument quantity and each FOK must fill completely or cancel.

Latest live scans:
- q1-compatible contracts: **255**;
- fractional-grid contracts included for FOK: **210**;
- monotone pair set: **2,438**;
- healthy books: **255 / 255**;
- current exact after-fee >=3c taker/taker candidates: **0**.

The taker scan repeats on **every idle cycle** before considering another maker; it is no longer startup-only.

### Maker fill-quality selector

Whole-contract maker discovery currently sees:
- whole-contract symbols: **45**;
- raw >=3c frontier rows: about **191** in the latest snapshot;
- robust whole-contract maker candidates: **19**;
- candidates with recent exact maker-side flow/volume: **0**;
- dead candidates suppressed: **19**.

The suppressed rows were instrumented rather than guessed. Current examples across XRP/ETH show:
- exact maker contract 30-minute quantity: **0**;
- exact hedge contract 30-minute quantity: **0**;
- event-wide contract quantity in the same window: **0**;
- no maker contra ask;
- reported current event liquidity: **0**.

Lifetime event volume can be nonzero/high, but these books are not currently trading. **Do not force makers into these dead books merely to create order count.**

Selection policy remains execution-first:
- actual maker-side aggressor flow;
- recent exact maker-contract volume;
- recent hedge-contract volume;
- queue ahead / ability to improve safely;
- ask proximity;
- event activity/liquidity;
- hedge stability/depth;
- expiry.

### Fractional resting-maker census — closed as unsafe at current grids

A read-only production census tested whether the 210 excluded 0.01-grid contracts could safely support q=1 resting makers despite partial fills.

Worst-case policy:
- assume the first partial fill can be one maker quantity increment;
- test every increment multiple through q=1;
- sibling must accept the exact same hedge quantity;
- sibling depth must cover it;
- use exact cent-rounded maker+taker fees;
- include one adverse hedge tick;
- require >=3c **per filled contract at every possible partial size**.

Latest v6 census:
- strict contracts: **255**;
- whole-contract (`increment=1`): **45**;
- fractional (`increment=0.01`): **210**;
- fractional q1 arithmetic frontier directions: **2,626**;
- fractional directions safe at every possible partial fill: **0**.

Therefore **do not enable 0.01-grid resting makers**. The smallest possible partial fill plus cent-rounded fees destroys the 3c/contract floor. FOK/FOK remains allowed on those contracts because immediate all-or-none q=1 execution is a different risk shape.

## MLB POLYMARKET SAME-VENUE MAKER -> FOK

Primary Railway service:
- service: `sports-kp-live-executor` (`bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`)
- branch: `feature/sports-kp-live-executor-impl`
- verified deployment: `c28d50c5-e48a-428c-9cba-767e69b2e478` — **SUCCESS**
- q=1
- minimum modeled locked profit: **$0.03**
- live rule-risk loss ceiling: **$5**, fixed; never silently reset/increase it.

This lane already evaluates all strict same-player/stat threshold combinations within each ladder, not only adjacent thresholds.

Current capabilities preserved:
- can seed the first safe post-only bid;
- computes maximum-safe maker frontier with exact current Polymarket US fee math;
- near-touch is ranking, not admission;
- sticky resting maker while hedge remains safe;
- exact current hedge depth/economics revalidation;
- confirmed q1 maker fill -> q1 FOK sibling hedge;
- durable reservation before mutation;
- private order state + bounded recovery;
- partial-fill / hedge-failure flatten + hard halt;
- first reconciled two-leg live canary stops for review;
- settlement is required before realized P&L.

Participation gate remains mandatory and is **not** being weakened. Audited policy requires:
- game state LIVE;
- hitter: confirmed starter + >=1 plate appearance;
- strikeout pitcher: confirmed starting pitcher + >=1 batter faced;
- exact same game/player/stat identity and threshold ordering;
- tick/q1 metadata intact.

Reason: these markets remain `LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE`; identity alone is not enough before the participation branch is resolved.

Latest pregame evidence from the current service:
- roughly **1,470+ strict markets / 1,480+ strict pairs** in recent inventory snapshots;
- `ready_pairs = 0` before the evening games were live;
- rejection reason was `GAME_NOT_LIVE`, not missing books or broken transport;
- 0 current attempts, 0 fills, $0 new realized.

## GEMINI <-> KALSHI MLB SPORTS SUPERVISOR

Service: `sports-gk-autonomous-supervisor` (`0238eada-ced4-4927-8150-0ccce2e1a0a4`).

Current live preflight remains armed with:
- q=1;
- >=3c minimum;
- fixed hard rule-risk budget/loss controls preserved;
- participation required;
- exact rule/counterpart certification required.

Latest observed pregame inventory:
- Gemini events: about **85**;
- Kalshi MLB prop events: about **40**;
- exact joined eligible keys: **329**;
- families include hits and home runs in the current joined set;
- prior supervisor epoch processed tens of thousands of trigger updates, but all were correctly rejected pregame for missing official participation;
- current-epoch attempts/fills: **0 / 0** before games were live.

Historical reconciled evidence remains: seven compatible G<->K sports trades total approximately **+$0.2679 realized net**. Do not use that value as proof of current opportunity supply or current-day profit.

## OTHER LANES / SAFETY

### FanDuel / sportsbook comparison

`the-odds-api-mlb-shadow` remains read-only. A prior false-positive defect matched different games within one series; current physical-event matching requires sportsbook commence time and prediction scheduled start within five minutes. After the fix, the prior hundreds of apparent rows collapsed to **0 same-physical-game actionable locks**. Never resurrect the old count as opportunity supply.

### Crypto basis

Do not revive the old Gemini<->Kalshi 15-minute crypto maker as the primary lane. Settlement index differences created directional basis risk, including an already reconciled one-leg loss. Keep it parked unless a separately certified basis-risk strategy is intentionally approved.

### Weather / economic events

Do not call modeled EV or one-sided rows arbitrage. Execution requires authoritative settlement/rule/fee certification and complementary executable legs.

## OPERATING PRIORITY FROM THIS CHECKPOINT

1. **Let the same-Gemini v6 service keep rechecking the complete 2,438-pair FOK universe and flow-qualified whole-contract maker set.** Do not place into dead books and do not enable fractional resting makers.
2. **As MLB games become live, prioritize actual fills.** Both the Polymarket same-venue lane and Gemini<->Kalshi sports supervisor already have substantial certified inventory waiting on participation. Once participation passes, rank by real trade flow/volume, queue conditions, proximity and hedge stability before edge magnitude alone.
3. Maintain one q=1 live maker exposure until maker fill -> hedge -> settlement -> realized lifecycle is proven cleanly.
4. After a clean settled canary, scale quantity/number of simultaneous makers only from measured fill conversion, hedge reliability, capital reservation capacity and realized loss budget — never from scanner counts.
5. Keep the scoreboard authoritative: `QUALIFIED -> ORDER_SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`.

Do not lower the 3c after-fee floor, weaken settlement/rule certification, bypass participation, silently increase/reset the $5 sports rule-risk ceiling, release ambiguous reservations, count shadow/locked/unsettled P&L as realized, or mix this infrastructure with the unrelated SOL executor.

Never store credentials, signing material, API keys, or other secrets here.
