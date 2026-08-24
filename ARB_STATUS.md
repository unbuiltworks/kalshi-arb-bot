# ARB Control Plane

Last verified: 2026-08-24 14:57 UTC

This is the durable ARB handoff. **Production state wins over chat history.** Before changing any workstream, verify current Railway deployment/runtime and GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective. A fill, locked pair, reservation, shadow opportunity, or modeled edge is not realized profit.

Historical control-plane detail through 2026-08-23 is preserved verbatim in `ARB_STATUS_HISTORY_2026-08-23.md`. Read that archive when historical risk budgets, incidents, prior services, or older implementation decisions matter.

## LATEST PRODUCTION CHECKPOINT — 2026-08-24 14:57 UTC

### Polymarket same-venue MLB maker -> hedge lane — ARMED, q=1, participation-gated

Primary live service:
- Railway: `sports-kp-live-executor` (`bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`)
- environment: production (`b51b1a8c-eefa-4a8a-a7e9-63a0430dc4a2`)
- source branch: `feature/sports-kp-live-executor-impl`
- current source commit: `98a30c61a7b505137f371fe81ad655b86161d127`
- current verified deployment: `5ac66d8c-0f9d-41e3-b58b-b27eabe93fff` — SUCCESS
- start command: `python -m src.sports_poly_structural_live_supervisor_verified`
- `SPORTS_POLY_RELAY_LIVE_Q1=1` remains the existing structural/totals gate
- `SPORTS_POLY_DIRECT_MLB_LIVE_Q1=1` is the separate MLB gate; the shared totals switch cannot implicitly arm MLB.

Current production evidence from the verified deployment:
- startup revenue-orphan cleanup: **400 slugs**, 16 batches of 25, **0 open**, **0 unresolved**, **0 partial unresolved**;
- supervisor: `LIVE_LOOP_READY`, q=1, >=$0.03 minimum modeled after-fee floor, one maker at a time, private order WebSocket primary with exact REST reconciliation fallback;
- MLB discovery: **400 strict markets**, **402 strict ladder pairs**, 133 ladder groups;
- families: H+R+RBI 126 pairs, Total Bases 126, Hits 123, Strikeouts 27;
- current participation certification: **0/402 ready** because all current rows were `GAME_NOT_LIVE` at the verified morning snapshot;
- current MLB `live_gate=true`, but **order_entry=false for the current cycles because no pair has passed the live participation gate**;
- current same-venue MLB maker lane executed trades: **0**;
- current same-venue MLB maker lane realized P&L: **$0**.

This is now an execution endpoint, not a shadow-only scanner. When a live candidate qualifies, the same production service may mutate automatically only after every gate below passes.

### Exact MLB maker execution gates

A same-venue MLB prop pair may reach order mutation only if ALL are true:
1. strict same Polymarket event/game/player/stat family with monotone integer thresholds and no ambiguous duplicate identity;
2. MLB Stats official game state is **LIVE**;
3. hitters: named player proved starter + >=1 plate appearance;
4. strikeouts: named pitcher proved starting pitcher + >=1 batter faced (one pitch is not sufficient);
5. both markets report a positive `orderPriceMinTickSize` and q=1-compatible `minimumTradeQty`;
6. authenticated exact market retrieval still agrees with the discovery tick/lot metadata;
7. exact q=1 hedge depth exists;
8. maker quote uses the market-reported tick, is post-only, and preserves >=$0.03 conservative locked economics against the current taker hedge;
9. safe maker quote is at most one actual tick from the contra ask;
10. the same maker/hedge direction persists for >=50ms immediately before the attempt;
11. Polymarket balance response is present;
12. Command Center is reachable, no other unresolved LIVE sports exposure blocks the lane, the existing maximum realized rule-risk loss ceiling of **$5** has not been exhausted, and a durable **$1** capital reservation is written before maker mutation;
13. maker is q=1 GTC post-only (`participateDontInitiate`); no crossing maker order;
14. while maker rests, exact hedge economics/depth are rechecked; edge loss causes scoped cancel;
15. after confirmed maker fill, the hedge book is re-read and the hedge is q=1 FOK only;
16. partial maker fills, hedge failures, ambiguous mutation outcomes, or reconciliation uncertainty follow bounded flatten/hard-halt logic;
17. confirmed zero-fill maker attempts explicitly release the durable reservation;
18. any filled/ambiguous attempt retains the reservation until settlement/reconciliation;
19. the first `BOTH_FILLED_RECONCILED` MLB pair is converted to `MLB_Q1_CANARY_FILLED_REVIEW_REQUIRED`, causing the parent supervisor to hard-halt for review before automatic scale-up.

### Rule classification

This lane is **LIVE RULE-RISK, not guaranteed arbitrage**.

Polymarket MLB player-prop rules can use last-fair-market-price treatment under suspension/delay/other contingency branches. Participation proof removes the pre-participation mismatch but does not eliminate every residual LFMP settlement path. Never label these fills guaranteed arbitrage and never count modeled/locked economics as realized P&L. Only official settlement plus authoritative ledger reconciliation can produce realized profit.

### Durable capital / accounting semantics

New files/paths in the production lane:
- `src/sports_poly_direct_mlb_campaign_q1_v2.py` — participation/tick/near-touch/live execution bridge;
- `src/sports_poly_mlb_participation_gate.py` — strict official participation + live-state + tick/lot certification;
- `src/mlb_live_participation.py` — MLB Stats proof; pitcher gate hardened to batter faced;
- `src/mlb_prop_rule_equivalence.py` — audited participation equivalence policy;
- `src/sports_poly_mlb_rule_risk_budget.py` — shared unresolved-exposure check, fixed $5 realized-loss ceiling check, $1 durable reservation/release;
- `src/sports_poly_structural_reporting.py` — reservation/fill/execution events share one durable trade id; full pair remains `AWAITING_SETTLEMENT`, never `TRADE_REALIZED` merely because both legs filled;
- `src/sports_poly_structural_live_supervisor_verified.py` — private WS transport and batched whole-revenue-universe orphan cleanup.

Build/deploy validation for deployment `5ac66d8c-0f9d-41e3-b58b-b27eabe93fff`:
- existing Docker sports suite: **37 passed**;
- expanded pre-deploy Python compile gate includes the new MLB live, participation, budget, reporting, and supervisor modules;
- runtime orphan cleanup and first production cycle succeeded;
- private order-state supervisor reported healthy on the preceding verified live cycles; no open/recovered revenue order existed at latest startup.

### Other sports evidence that remains relevant

- Seven previously identified settled compatible two-leg G<->K sports trades totaled approximately **+$0.2679 realized net**. Do not generalize that number to all sports P&L; it refers only to those seven reconciled trades.
- NFL maker research: 722 exact keys / 133 priced; 0 taker >=3c; 2 maker >=3c; 0 dual; 0 near-touch at the audited snapshot. Not promoted without full rule certification.
- NCAAF G<->K maker research: 661 keys, 11 maker >=3c and persistent supply, but 0 near-touch; pregame execution remains blocked by Gemini 7-day vs Kalshi 48-hour postponement-rule mismatch.
- Existing K<->P MLB rule-risk service was observed `live=true` with strict candidates and participation proof but **0 executions** / **0 final-positive executions** in the latest inspected logs. The new same-venue lane's Command Center unresolved-exposure gate is intended to prevent overlapping LIVE sports capital if another sports service reserves first.

### Crypto separation

Keep crypto/basis-risk infrastructure separate from this sports maker work and keep unrelated SOL infrastructure untouched. Do not infer that an older archived crypto checkpoint is current production; establish the current crypto live state before changing it. Do not use sports maker work as justification to rearm or change a disarmed crypto lane.

## Immediate operating priority

The MLB same-venue maker endpoint is built, deployed, and armed. **Do not weaken gates to manufacture trades.** The next meaningful production event is a LIVE, participation-proven MLB ladder that becomes one-tick-near-touch while preserving >=3c q=1 economics. At that point the service is authorized to reserve $1, post the q=1 maker, hedge a confirmed fill FOK, reconcile, and hard-halt after the first filled pair for settlement/risk review.

After the first successful q=1 canary is settled and reconciled, optimize safe realized dollars/day in this order:
1. prove actual fill->hedge->settlement behavior and authoritative net P&L;
2. replace the conservative flat 1c taker-fee allowance with the already-audited exact Polymarket US fee/rebate function without reducing the 3c floor;
3. measure touch frequency, maker fill conversion, hedge slippage/failure, and realized edge decay;
4. only then consider more than one canary, larger q, or wider families, with explicit capital/rule-risk review.

Do not silently raise q, remove the first-fill hard stop, increase the $5 maximum realized-loss budget, bypass durable reservation, relax participation/tick/depth/persistence gates, treat LFMP rule-risk as guaranteed arbitrage, or count unconfirmed/unssettled fills as realized profit.

Never store credentials, signing material, API keys, or sensitive balances here.
