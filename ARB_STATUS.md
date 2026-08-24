# ARB Control Plane

Last verified: 2026-08-24 19:47 UTC

This is the durable ARB handoff. **Production state wins over chat history.** Before changing any workstream, verify current Railway deployment/runtime and GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective. A fill, locked pair, reservation, shadow opportunity, modeled edge, or order submission is not realized profit.

Historical control-plane detail through 2026-08-23 is preserved verbatim in `ARB_STATUS_HISTORY_2026-08-23.md`.

## LATEST PRODUCTION CHECKPOINT — 2026-08-24 19:47 UTC

### MLB Polymarket same-venue maker -> FOK hedge: true maker-frontier v3 is LIVE

Primary Railway service:
- project: `sports-arb-autonomous` (`7e15f30d-1430-4514-8f7b-bb81c84d8f4d`)
- production environment: `b51b1a8c-eefa-4a8a-a7e9-63a0430dc4a2`
- service: `sports-kp-live-executor` (`bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`)
- branch: `feature/sports-kp-live-executor-impl`
- production source commit: `03b8e1e0a50f103193755aa36147e2fe32e64080`
- verified deployment: `c28d50c5-e48a-428c-9cba-767e69b2e478` — **SUCCESS**
- start: `python -m src.sports_poly_structural_live_supervisor_verified`
- q=1
- minimum modeled locked profit: **$0.03**
- MLB live gate remains separately armed through `SPORTS_POLY_DIRECT_MLB_LIVE_Q1`.

This deployment replaces the old near-touch MLB admission behavior with `src/sports_poly_direct_mlb_campaign_q1_v3.py`, while preserving the already-proven v2 reservation, authenticated market validation, maker-order transport, private order-state stream, FOK hedge, partial-fill flatten, reconciliation, ledger, and first-fill hard-stop path.

### What changed to improve actual maker fills

The old v2 behavior was too passive:
- a candidate had to be profitable at the **existing displayed maker bid**;
- the safe maker quote had to be within **one tick of the contra ask**;
- a zero displayed bid could therefore never seed a new market;
- eligible maker orders normally rested only about **8 seconds** (hard capped at 12 seconds).

The production v3 wrapper changes maker behavior without lowering the profit floor:
1. **Near-touch is ranking only, not an admission gate.** A participation-certified ladder may qualify even when the safe maker price is several ticks from the ask.
2. **Zero-bid markets are allowed.** If the book has no displayed bid, the bot may create the first post-only bid itself when the hedge supports >=3c q1 locked economics.
3. **Highest safe frontier quote.** The bot computes the highest market-reported tick strictly below the maker ask that still preserves >=3c against the current hedge.
4. **Exact current Polymarket US fee math.** q1 admission now uses the audited current fee/rebate helper rather than blindly subtracting a flat 1c taker-fee allowance. Cent rounding can safely gain a tick when the actual taker fee rounds to zero.
5. **Sticky maker rest.** Default maker rest is now 60 seconds (bounded 30–180 seconds) while hedge depth/economics remain safe. This preserves queue age instead of automatically canceling a good order after ~8 seconds.
6. **Private order WS remains primary.** Longer rest does not mean unrestricted authenticated REST polling; the verified supervisor still uses private order WebSocket state with paced REST reconciliation fallback.

The current v3 does **not** blindly cancel/repost on every book movement. It intentionally preserves queue position. A later enhancement may raise a resting quote only when materially beneficial and safely confirmed; do not add churn merely to appear active.

### Exact safety invariants preserved

A live MLB maker may mutate only after the existing fail-closed gates pass:
- strict same Polymarket game/player/stat family and monotone integer thresholds;
- official MLB game state is **LIVE**;
- hitter: starter + >=1 plate appearance;
- strikeout pitcher: starting pitcher + >=1 batter faced;
- positive market-reported tick and q1-compatible minimum quantity;
- authenticated exact market metadata must still match discovery metadata;
- exact q1 hedge depth >=1;
- same maker/hedge direction persists >=50ms immediately before attempt;
- Polymarket balance response present;
- Command Center reachable;
- no conflicting unresolved LIVE sports exposure;
- fixed maximum realized MLB rule-risk loss ceiling remains **$5** and is not auto-increased;
- durable **$1** capital reservation written before maker mutation;
- maker q=1 GTC post-only (`participateDontInitiate=true`), never crosses;
- exact hedge economics and depth are rechecked while maker rests; if the >=3c floor disappears, cancel;
- confirmed maker fill -> exact hedge book reread -> q1 FOK hedge;
- partial fill / hedge failure / ambiguous mutation follows bounded flatten + hard-halt logic;
- confirmed zero-fill attempts release reservation;
- filled/ambiguous attempts retain reservation through settlement/reconciliation;
- first reconciled two-leg MLB pair becomes `MLB_Q1_CANARY_FILLED_REVIEW_REQUIRED` and stops automatic scale-up.

This lane remains **LIVE RULE-RISK, NOT GUARANTEED ARBITRAGE** because Polymarket MLB player props retain residual LFMP/suspension/delay settlement paths even after participation proof. Never count a modeled lock or filled pair as realized until official settlement + authoritative ledger reconciliation.

### Validation of maker-frontier deployment

New/changed paths:
- `src/sports_poly_direct_mlb_campaign_q1_v3.py` — exact safe-frontier maker wrapper;
- `src/sports_poly_current_fees.py` — audited q1 fee/rebate math;
- `src/sports_poly_revenue_campaign_q1.py` — routes MLB revenue fallback through v3;
- `tests/test_sports_poly_mlb_maker_frontier_v3.py` — regression coverage for far-from-touch pricing, zero-bid-compatible frontier logic, post-only ask cap, exact fee rounding, and sub-cent tick handling;
- `railway.structural.json` — now compiles the full MLB live path and runs the targeted v3 frontier test gate in preDeploy.

Verified deployment evidence:
- deployment `c28d50c5-e48a-428c-9cba-767e69b2e478`: **SUCCESS**;
- existing Docker sports suite: **37 passed**;
- targeted frontier test command is part of preDeploy and the deployment exited successfully;
- startup cleanup: **1,181 slugs**, 48 batches, **0 open**, **0 unresolved**, **0 partial unresolved**;
- `LIVE_LOOP_READY`: q=1, >=3c floor, one maker at a time, private WS primary / REST reconciliation fallback;
- latest MLB inventory: **1,181 strict markets**, **1,191 strict ladder pairs**, 392 ladder groups;
- families: H+R+RBI 375, Total Bases 375, Hits 369, Strikeouts 72;
- latest participation certification: **0/1,191 ready**, all `GAME_NOT_LIVE` at the 19:46 UTC verification snapshot;
- therefore **0 MLB order attempts, 0 fills, $0 new realized P&L** during this deployment change.

This was deployed during a pregame-safe window with no MLB maker order to orphan.

## OTHER CURRENT EXECUTION FINDINGS

### FanDuel / sportsbook scanner — cross-game false positives eliminated

Service `the-odds-api-mlb-shadow` remains read-only. The prior scanner defect matched different games from the same MLB series because semantic identity did not include scheduled start time. A hard physical-event guard now requires sportsbook `commence_time` and prediction `scheduled_start_ms` within 5 minutes before a row can enter arb math.

Fresh corrected evidence from the 2026-08-24 execution pass:
- raw apparent positive retail-vs-prediction locks: **353**;
- cross-game rows filtered: **353**;
- same-physical-game positive locks: **0**;
- actionable locks: **0**.

Never use the old 300+ count as evidence of executable supply.

### Gemini <-> Kalshi sports

`sports-gk-autonomous-supervisor` remains the other important sports execution lane. Historical evidence: seven previously reconciled compatible G<->K sports trades totaled approximately **+$0.2679 realized net**. That number refers only to those seven settled trades, not current-day total P&L. Pregame participation constraints still apply to the current MLB prop inventory.

### Crypto

Keep crypto separate from sports and the unrelated SOL executor.
- The latest sticky Gemini/Kalshi crypto maker experiment completed 24 cycles without a terminal fill; new realized P&L was $0.
- Do not recreate short-TTL order/cancel notification churn merely to generate activity.
- Old SOL 15-minute taker/basis execution remains correctly disabled because Gemini/Kalshi settlement-index mismatch creates directional basis risk.

### Economic events / weather

- `kp-unemployment-live-probe-q1` has scanned tens of thousands of snapshots without a complementary two-leg book. Do not force one-sided exposure.
- Weather research shows positive modeled EV in places but currently has **0 execution-certified rows** because authoritative settlement/model/fee audits are incomplete. Do not call those rows arbitrage or arm them from research EV alone.

## OPERATING PRIORITY

The project is now execution-first. Optimize the state transition:

`OBSERVED -> RULE_CERTIFIED -> EXECUTABLE -> ORDER_SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`

For the MLB same-venue lane, **do not restore the one-tick admission requirement, do not require an existing profitable maker bid, and do not reduce sticky rest back to an 8-second probe.** The v3 endpoint is designed to create the best safe bid itself and preserve queue age.

When MLB games go live and participation clears, the next meaningful evidence is actual maker time-in-market, fill conversion, hedge success, and settlement. If the first live v3 canary still receives no fill despite being top-of-book for meaningful time, the next safe optimization is candidate ranking by actual recent trade flow/volume and queue conditions—not lowering the 3c floor or weakening settlement rules.

Do not silently raise q, remove the first-fill review halt, increase the $5 maximum realized-loss budget, bypass durable reservation, relax participation/tick/depth/persistence gates, treat LFMP rule-risk as guaranteed arbitrage, or count unconfirmed/unsettled fills as realized profit.

Never store credentials, signing material, API keys, or sensitive balances here.
