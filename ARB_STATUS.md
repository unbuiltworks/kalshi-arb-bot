# ARB Control Plane

Last verified: 2026-08-24 19:04 UTC

This is the durable ARB handoff. **Production state wins over chat history.** Before changing any workstream, verify current Railway deployment/runtime and GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective. A fill, locked pair, reservation, shadow opportunity, modeled edge, or order submission is not realized profit.

Historical control-plane detail through 2026-08-23 is preserved verbatim in `ARB_STATUS_HISTORY_2026-08-23.md`. Read that archive when historical risk budgets, incidents, prior services, or older implementation decisions matter.

## LATEST EXECUTION-FIRST CHECKPOINT — 2026-08-24 19:04 UTC

### Bottom line

The latest production-wide execution pass found **no certified immediate pregame arbitrage to force**. No new filled/hedged/settled trade was created in this pass and current-pass realized P&L is **$0**. Do not weaken settlement, freshness, depth, or risk gates merely to create order flow.

The highest-impact finding was a false-positive source in sportsbook matching. That defect is now fixed and should not be reintroduced.

### FanDuel / sportsbook -> prediction-market scanner — cross-game false positives eliminated

Railway service:
- `the-odds-api-mlb-shadow` (`7ab3fe20-a7d1-4626-b3c1-6411262527d8`)
- production environment `b51b1a8c-eefa-4a8a-a7e9-63a0430dc4a2`
- branch `feature/three-venue-sports-router`
- latest scanner fix commit: `9341132f914fd61d3b02376d4c8ff9af40c999ee`
- verified deployment: `f8e7b407-6665-4481-8c0a-675b3b46b025` — SUCCESS
- build validation: **87/87 tests passed**
- read-only; order entry remains disabled.

Root cause fixed:
- sports semantic settlement keys intentionally omit scheduled start time;
- consecutive MLB games between the same teams could therefore collapse into one normalized matchup key;
- sportsbook quotes for one game were being compared with Gemini/Polymarket/Kalshi quotes for another game in the same series.

New physical-event hard gate:
- sportsbook `commence_time` and normalized prediction-market `scheduled_start_ms` must be within **5 minutes** before a retail-vs-prediction row can enter arb math, rule certification, or alerting.

Fresh corrected result:
- sportsbook events: **10**
- sportsbook quotes: **490**
- directly priced prediction quotes: **425**
- apparent positive retail-vs-prediction locks before physical-event guard: **353**
- cross-game rows filtered: **353**
- same-physical-game positive locks after guard: **0**
- actionable locks: **0**
- alerts: **0**.

Therefore the old ~300+ sportsbook lock counts were not executable opportunity. **Never use them as evidence of available arb supply.**

Full-game FanDuel/Polymarket/Gemini/Kalshi pairs also remain rule-conditional rather than blanket guaranteed arbitrage because postponement/cancellation/suspension tails differ by venue. Do not bypass the rule registry to manufacture a pregame trade.

### Gemini <-> Kalshi sports supervisor — healthy, waiting for actual participation inventory

Railway:
- `sports-gk-autonomous-supervisor` (`0238eada-ced4-4927-8150-0ccce2e1a0a4`)
- branch `feature/sports-gk-autonomous-supervisor`
- start: `python -m src.sports_gk_ws_live_entrypoint --epoch-seconds 120 --cooldown-seconds 5`
- restart policy ALWAYS.

Latest verified state:
- balance/funding preflight succeeds;
- shared sports ledger remains below its capital ceiling;
- `MLB_GK_MULTI_FUNNEL RuntimeError` is the code's intentional no-eligible-inventory sentinel and is followed by `SPORTS_SUPERVISOR_IDLE_NO_INVENTORY`; it is not an executor crash;
- pregame hitter/pitcher participation requirements are not yet satisfied, so there is no safe inventory to mutate at this checkpoint.

Historical real evidence remains: seven previously reconciled compatible G<->K sports trades totaled approximately **+$0.2679 realized net**. That is historical settled evidence only, not current-day P&L.

### Polymarket same-venue MLB maker -> hedge lane — armed but pregame-gated

Primary live service remains:
- `sports-kp-live-executor` (`bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`)
- q=1, participation-gated, post-only maker -> FOK hedge.

Fresh same-day scans continued to show roughly **11–17 pregame strict maker-ladder candidates** at various moments, but **participation_ready=0** while the games were not live. No campaign attempt/fill was produced in the latest pass. Do not spend more pregame engineering cycles trying to force these rows through the official participation gate.

### Crypto — no immediate safe revenue lane proven

- The latest sticky Gemini/Kalshi crypto maker supervisor completed **24 cycles with zero terminal fills** and realized P&L **$0**. Do not restart it merely to create order/cancel churn.
- The old SOL 15-minute taker canary remains correctly hard-disabled because Gemini/Kalshi settlement-index mismatch creates basis risk; reviving it would not be guaranteed arbitrage.
- Keep crypto infrastructure separate from sports and from the unrelated SOL executor.

### Economic-event lane — live, but no complementary book

`kp-unemployment-live-probe-q1` is already live-armed and has evaluated **55k+ snapshots** without finding a complementary two-leg book. Leave the healthy service running; do not force one-sided exposure merely because it is continuously scanning.

### Weather — large research EV exists, but 0 execution-eligible rows

Fresh `weather-shadow-arb` deployment `1b34fc6e-1ab1-4651-87bb-a6ee433c96c2` succeeded.

Latest summary:
- rows: **48**
- positive research candidates: **12**
- depth checked: **12**
- execution-eligible events: **0**
- order entry: false.

Some research-model rows show large apparent EV (for example Miami/Austin rows above 30c), but every positive row remains blocked by one or more of:
- `TWC_SOURCE_ACCESS_REQUIRED`
- `MODEL_RESEARCH_ONLY`
- `EXACT_MARKET_SOURCE_AUDIT_REQUIRED`
- `AUTHORITATIVE_FEE_AUDIT_REQUIRED`
- `MODEL_SANITY_AUDIT_REQUIRED`.

These are model wagers, not certified arbitrage. **Do not arm weather live from this signal.**

### Immediate operating priority

Stop adding infrastructure merely to generate candidates. Optimize only for the state transition:

`OBSERVED -> RULE_CERTIFIED -> EXECUTABLE -> ORDER_SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`

At this checkpoint there is no safe immediate pregame trade. Keep healthy continuous lanes running and focus on the first MLB games becoming live. As official participation clears, inspect the existing G<->K and same-venue Polymarket executors for a genuine >=3c q=1 opportunity. If one passes all existing settlement, freshness, depth, capital, and reconciliation gates, let the existing live executor take it automatically. Do not lower the 3c floor, raise q, increase the $5 MLB rule-risk loss ceiling, or count anything before settlement as realized profit.

---

## PRIOR PRODUCTION CHECKPOINT — 2026-08-24 14:57 UTC

### Polymarket same-venue MLB maker -> hedge lane — ARMED, q=1, participation-gated

Primary live service:
- Railway: `sports-kp-live-executor` (`bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`)
- environment: production (`b51b1a8c-eefa-4a8a-a7e9-63a0430dc4a2`)
- source branch: `feature/sports-kp-live-executor-impl`
- current source commit at that checkpoint: `98a30c61a7b505137f371fe81ad655b86161d127`
- verified deployment at that checkpoint: `5ac66d8c-0f9d-41e3-b58b-b27eabe93fff` — SUCCESS
- start command: `python -m src.sports_poly_structural_live_supervisor_verified`
- `SPORTS_POLY_RELAY_LIVE_Q1=1` remains the existing structural/totals gate
- `SPORTS_POLY_DIRECT_MLB_LIVE_Q1=1` is the separate MLB gate; the shared totals switch cannot implicitly arm MLB.

Production evidence from that checkpoint:
- startup revenue-orphan cleanup: **400 slugs**, 16 batches of 25, **0 open**, **0 unresolved**, **0 partial unresolved**;
- supervisor: `LIVE_LOOP_READY`, q=1, >=$0.03 minimum modeled after-fee floor, one maker at a time, private order WebSocket primary with exact REST reconciliation fallback;
- MLB discovery: **400 strict markets**, **402 strict ladder pairs**, 133 ladder groups;
- families: H+R+RBI 126 pairs, Total Bases 126, Hits 123, Strikeouts 27;
- participation certification: **0/402 ready** because all rows were `GAME_NOT_LIVE` at that morning snapshot;
- MLB `live_gate=true`, but **order_entry=false for those cycles because no pair had passed the live participation gate**;
- same-venue MLB maker lane executed trades: **0**;
- same-venue MLB maker lane realized P&L: **$0**.

This is an execution endpoint, not a shadow-only scanner. When a live candidate qualifies, the same production service may mutate automatically only after every gate below passes.

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

Relevant production paths:
- `src/sports_poly_direct_mlb_campaign_q1_v2.py` — participation/tick/near-touch/live execution bridge;
- `src/sports_poly_mlb_participation_gate.py` — strict official participation + live-state + tick/lot certification;
- `src/mlb_live_participation.py` — MLB Stats proof; pitcher gate hardened to batter faced;
- `src/mlb_prop_rule_equivalence.py` — audited participation equivalence policy;
- `src/sports_poly_mlb_rule_risk_budget.py` — shared unresolved-exposure check, fixed $5 realized-loss ceiling check, $1 durable reservation/release;
- `src/sports_poly_structural_reporting.py` — reservation/fill/execution events share one durable trade id; full pair remains `AWAITING_SETTLEMENT`, never `TRADE_REALIZED` merely because both legs filled;
- `src/sports_poly_structural_live_supervisor_verified.py` — private WS transport and batched whole-revenue-universe orphan cleanup.

Build/deploy validation for deployment `5ac66d8c-0f9d-41e3-b58b-b27eabe93fff`:
- existing Docker sports suite: **37 passed**;
- expanded pre-deploy Python compile gate includes the MLB live, participation, budget, reporting, and supervisor modules;
- runtime orphan cleanup and first production cycle succeeded;
- private order-state supervisor reported healthy on preceding verified live cycles; no open/recovered revenue order existed at startup.

### Other sports evidence that remains relevant

- Seven previously identified settled compatible two-leg G<->K sports trades totaled approximately **+$0.2679 realized net**. Do not generalize that number to all sports P&L; it refers only to those seven reconciled trades.
- NFL maker research: 722 exact keys / 133 priced; 0 taker >=3c; 2 maker >=3c; 0 dual; 0 near-touch at the audited snapshot. Not promoted without full rule certification.
- NCAAF G<->K maker research: 661 keys, 11 maker >=3c and persistent supply, but 0 near-touch; pregame execution remains blocked by Gemini 7-day vs Kalshi 48-hour postponement-rule mismatch.
- Existing K<->P MLB rule-risk service was observed `live=true` with strict candidates and participation proof but **0 executions** / **0 final-positive executions** in the inspected logs. The same-venue lane's Command Center unresolved-exposure gate is intended to prevent overlapping LIVE sports capital if another sports service reserves first.

### Crypto separation

Keep crypto/basis-risk infrastructure separate from this sports maker work and keep unrelated SOL infrastructure untouched. Do not infer that an older archived crypto checkpoint is current production; establish current crypto live state before changing it. Do not use sports maker work as justification to rearm or change a disarmed crypto lane.

## Safety invariants

Do not silently raise q, remove a first-fill hard stop, increase the $5 maximum realized-loss budget, bypass durable reservation, relax participation/tick/depth/persistence gates, treat LFMP rule-risk as guaranteed arbitrage, or count unconfirmed/unsettled fills as realized profit.

Never store credentials, signing material, API keys, or sensitive balances here.
