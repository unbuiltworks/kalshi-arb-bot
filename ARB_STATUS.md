# ARB Control Plane

Last verified: 2026-08-23 13:50 UTC

This is the durable ARB handoff. Production state wins over chat history. Before changing a workstream, verify current Railway deployment/runtime + GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the $480/day objective.

## LATEST PRODUCTION CHECKPOINT — 2026-08-23 13:50 UTC

This section supersedes older Gemini/Kalshi basis-risk execution/promotion details below where they conflict. Historical sections are preserved for audit context.

### Basis-risk v6: SOL one-leg execution incident reconciled; Kalshi-first rollover LIVE

The sixth promoted q=1 basis-risk reservation exposed a real execution-order failure in the generic concurrent dual-FOK transport. This lane remains **LIVE SETTLEMENT-BASIS RESEARCH, not guaranteed arbitrage**.

Incident trade `gkbasis-br0823131801-01` at 13:18:39 UTC:
- asset SOL; q=1; direction `KALSHI_YES_GEMINI_NO`;
- Gemini `GEMI-SOL15M2608231330-UP`; Kalshi `KXSOL15M-26AUG230930-30`;
- modeled locked quote profit **+$0.0300**; threshold gap abs **~2.22765bp**; protective direction correctly authorized;
- Gemini NO filled q=1 at **$0.08**, fee **$0.01**;
- paired Kalshi YES FOK filled **0** and rejected `fill_or_kill_insufficient_resting_volume`;
- generic concurrent transport therefore produced `ONE_LEG_FILLED` and hard-halted;
- original generic Gemini emergency flatten used unsupported `MARKET+IOC` and failed; subsequent bounded supported recovery attempts (Gemini SELL NO LIMIT+FOK, complementary Gemini BUY YES LIMIT+FOK, aggressive Kalshi YES FOK) also could not fill before settlement because resting liquidity was unavailable.

Official direct settlement reconciliation completed after the 13:30 UTC expiry:
- Gemini outcome **YES**;
- Kalshi outcome **YES**;
- venue outcomes agreed; this was **not** a settlement-index disagreement;
- only the Gemini NO leg existed, so payout $0 on $0.08 raw cost + $0.01 fee;
- authoritative `TRADE_REALIZED` status `REALIZED_ONE_LEG_SETTLEMENT`;
- gross P&L **-$0.0800**; net P&L **-$0.0900**.

Authoritative basis-risk ledger after repair:
- `CAPITAL_RESERVED`: **6**;
- `TRADE_REALIZED`: **6**;
- profitable realized probes: **5**;
- realized losses: **1**;
- realized net P&L: **+$0.251900**;
- realized loss budget consumed: **$0.0900**;
- existing approved maximum-realized-loss research budget: **$5.00**, not reset/increased;
- remaining loss budget: **$4.9100**;
- unresolved reservations: **0**;
- unresolved submissions: **0**.

Root cause and v6 fix:
- the basis lane no longer uses concurrent Gemini/Kalshi order submission;
- new basis-only transport `src/gemini_kalshi_basis_risk_thin_first_transport.py` executes **KALSHI_THEN_GEMINI**;
- exact economics/skew/payload validation still occurs before mutation;
- Kalshi q=1 FOK is submitted first because it is the observed thinner/less reliable leg;
- if Kalshi does not confirm a q=1 fill, **Gemini is never submitted**;
- only after a confirmed Kalshi q=1 fill may the original Gemini FOK be submitted;
- if Gemini explicitly does not fill after Kalshi filled, the process attempts the existing reverse Kalshi emergency flatten and hard-halts;
- an ambiguous venue result remains fail-closed; no guessing/automatic rollover through ambiguity;
- a safe Kalshi-first rejection produces a zero-P&L `NO_FILL_RELEASED` realization so the durable reservation is explicitly closed before another lease;
- no shared guaranteed-arbitrage transport/authorization was relaxed.

Reviewed-loss continuation semantics are implemented in v6, not by erasing the loss:
- the exact historical row `gkbasis-br0823131801-01` / `REALIZED_ONE_LEG_SETTLEMENT` / `-$0.0900` is the only reviewed exception;
- that $0.09 remains permanently charged to the $5 loss budget;
- **any new or unreviewed realized loss hard-stops future automatic rollover**;
- unresolved reservation/submission still hard-stops;
- q=1, <=$1 per probe, 20 lifetime reservation ceiling, ETH/SOL/XRP only, BTC quarantine, >=0.5bp protective threshold separation, >=3c modeled after-fee edge, protective orientation, freshness/skew/persistence/exact-depth/double-boundary controls remain in force;
- shared `SETTLEMENT_EQUIVALENCE_PROVEN=False` remains unchanged and shared guaranteed-arb `live_probe_authorized(True)` remains false;
- q5 guaranteed-arbitrage promotion remains blocked.

Validation/deployment:
- targeted Kalshi-first transport tests: **4/4 passed**;
- combined v6 transport + reviewed-loss policy suite: **8/8 passed** in Railway deployment `64e9af4e-8102-400b-8660-d1c7cd847374`;
- read-only production preflight deployment `c3052cd0-ed7e-4874-a4a3-598abfca89c6` verified reservations=6, settled/released=6, one exact reviewed loss, realized loss $0.09, realized net +$0.2519, remaining loss budget $4.91, zero unresolved exposure, and shared arb lock unchanged;
- live service `crypto-basis-risk-q1-canary` (`9f6e8d88-868a-4a38-9758-e74fceb71ed6`) now runs `python -m src.gemini_kalshi_basis_risk_rollover_supervisor_v6`;
- restart policy `ON_FAILURE`, max retries 3;
- live deployment `8a855c5f-1327-4269-9c3f-f11f64a408f9` on source checkpoint commit `a2529cf8bce36a560918799f9174e8556f30c59c` is SUCCESS;
- boot logs explicitly show `execution_sequence=KALSHI_THEN_GEMINI`, loss budget consumed 0.09 / remaining 4.91, one-unresolved=true, `new_loss_hard_stop=true`, `guaranteed_arb=false`;
- fresh lease `b60823134601` started with deadline ~15:46:52 UTC;
- ETH/SOL/XRP exact books are scanning normally.

Latest live evidence at ~13:49 UTC:
- no new v6 capital reservation/order/fill after rearm;
- an XRP gate observation with ~2.057bp separation was rejected as `DANGEROUS_THRESHOLD_ORIENTATION`, proving the protective-direction gate remains active;
- do not count diagnostic observations, reservations, no-fill releases, submitted orders, or fills as realized profit.

Do not reintroduce concurrent dual-FOK execution into this basis lane. Do not reset the known $0.09 loss, raise q, raise the 20-reservation ceiling, increase the $5 loss budget, re-enable BTC, or weaken settlement/freshness/depth/dominance controls without a new explicit review.

## LATEST PRODUCTION CHECKPOINT — 2026-08-23 12:35 UTC

This section supersedes older basis-risk probe ceilings/details below where they conflict. Historical sections are preserved for audit context.

### Gemini/Kalshi basis-risk q1 promoted to 20 total probes — LIVE

The dedicated settlement-basis research lane has now completed **5/5 real q=1 probes profitably**, with **0 realized losses** and authoritative realized net P&L **+$0.341900**. This remains **LIVE BASIS-RISK RESEARCH, not guaranteed arbitrage**.

Authoritative Neon state immediately before promotion and again after deployment:
- `CAPITAL_RESERVED`: **5**
- `ORDER_SUBMITTED`: **5**
- `TRADE_REALIZED`: **5**
- realized losses: **0**
- realized loss USD: **$0**
- realized net P&L: **+$0.341900**
- unresolved reservations: **0**
- unresolved submissions: **0**

User explicitly approved promotion beyond the five-probe ceiling. The lane is now bounded to **20 total q=1 probes**, not unlimited size/risk.

Safety semantics of the promotion:
- q=1 only;
- <= **$1** capital reserved per probe;
- at most **20 lifetime `CAPITAL_RESERVED` rows** for this strategy;
- exactly **one unresolved/reserved probe at a time**;
- every prior reservation must be authoritatively `TRADE_REALIZED` before the next reservation;
- **any realized loss hard-stops all future automatic rollover**;
- the already approved **$5 maximum-realized-loss research budget is unchanged and was not reset/increased**;
- historical sequential reservation turnover may exceed $5 because resolved capital turnover is not cumulative realized loss; simultaneous exposure remains <=$1;
- ETH/SOL/XRP only; BTC remains quarantined;
- >=0.5bp protective threshold separation;
- >=3c modeled after-fee locked edge;
- existing protective orientation, 50ms persistence/skew, freshness, exact-depth, double-boundary, FOK, durable reservation, reconciliation and hard-halt controls remain unchanged;
- shared guaranteed-arbitrage settlement-equivalence lock remains unchanged/hard-false;
- q5 guaranteed-arbitrage promotion remains blocked.

Promotion code on `feature/crypto-basis-risk-q1-canary`:
- `src/gemini_kalshi_basis_risk_q1_child_v5.py` commit **`e6e4f8680aeb5d9abd69a7248fe68a7b085fa80d`** changes the promoted ceiling to 20 and correctly treats the $5 authorization as a maximum realized-loss budget rather than a lifetime reservation-turnover cap;
- promotion tests commit **`07064db859cefcfd11bbd79f246a41511e8c867d`**;
- rollover tests commit **`3419674c1d0d9a5490ad7e9265bb87bcd5113605`**;
- rollover documentation/runtime checkpoint commit **`b96050ee9df1b893b6147ef9c1f48bf937b0b7f9`**.

Production validation:
- service was disarmed before code mutation;
- targeted Railway pytest deployment **`6d48b090-4122-4b08-bed5-4b50794158cd`** on marker commit `a497dcd82eb3fd96b01ac045845aed0749f9c63f` finished **SUCCESS**;
- ledger was rechecked clean at 5 reservations / 5 realized / 0 losses / +$0.341900 / 0 unresolved;
- service start command restored to `python -m src.gemini_kalshi_basis_risk_rollover_supervisor` with ON_FAILURE max retries 5;
- arm restored only after validation;
- live source deployment **`a26c7073-2345-43d0-b441-e99b6474d784`** on commit **`8cbf2f29833a06123cc9aaa006a2aefc708e1ff8`** is **SUCCESS**.

Verified live runtime logs:
- `CRYPTO_BASIS_RISK_ROLLOVER_ENABLED ... probe_limit=20 ... approved_budget_usd=5 ... min_gap_bps=0.5 ... min_locked_profit=0.03 ... hard_stop_on_any_loss=true ... one_unresolved=true ... guaranteed_arb=false`;
- `CRYPTO_BASIS_RISK_V5_PROMOTION_INSTALLED probe_limit=20 q=1 tranche_usd=1 approved_loss_budget_usd=5 one_unresolved=true hard_stop_on_any_loss=true shared_arb_lock_changed=false`;
- parent/child ledger reconciliation reported `unresolved_prior_trades=0`;
- fresh lease **`br0823123301`** started with deadline `2026-08-23T14:33:51Z`;
- ETH/SOL/XRP hot feeds are scanning with live Gemini/Kalshi events and no reconnect/sequence-gap issue in the first verified summaries;
- no sixth capital reservation had occurred at the 12:35 UTC ledger check.

Do not silently raise the 20-probe ceiling, q size, or $5 maximum-realized-loss budget. Review the accumulated realized results before any further promotion. Do not call this lane guaranteed arbitrage.

## LATEST PRODUCTION CHECKPOINT — 2026-08-23 03:24 UTC

This section supersedes older sports settlement-lag notification/execution details below where they conflict. Historical sections are preserved for audit context.

### Prediction-market-only automatic settlement-lag routing — LIVE

User routing policy is now explicit:
- **Kalshi / Gemini / Polymarket:** safely certified opportunities may execute automatically.
- **FanDuel / sportsbooks:** manual only. No automatic sportsbook transport should be added to these lanes.
- Post-final known-winner trades remain **SETTLEMENT-LAG / RULE-RISK**, not guaranteed arbitrage. A fill remains `FILLED_NOT_SETTLED` until the venue settles and authoritative reconciliation confirms P&L.

The earlier Pushover messages that said `MLB FINAL EDGE` were single-venue settlement-lag signals. The old manual-instruction wrapper is no longer the production action path: Railway service `mlb-prop-decided-shadow` now runs the underlying read-only scanner directly via `python -m src.mlb_prop_decided_shadow`, preventing duplicate manual-buy alerts while automatic K/P routing is active.

#### Kalshi / Polymarket strict MLB prop final-lag q1 — ARMED

Railway service `mlb-final-lag-live-q1` (`d773c4bd-4ee2-415a-8556-b4f2a387536c`) is live on branch `feature/mlb-final-lag-live-q1` via `python -m src.mlb_prop_final_lag_live_q1_entry`.

Verified production READY log:
- `armed=True`
- q=1
- automatic venues `kalshi,polymarket`
- `manual_only=fanduel`
- conservative modeled after-fee profit floor **$0.10**
- direct final revalidation <= **100ms**
- shared sports cumulative live ceiling **$5**, not reset/increased
- classification `LIVE_SETTLEMENT_LAG_RULE_RISK_NOT_ARBITRAGE`

Execution path requires official MLB Final evidence stable >=60s / >=3 observations, exact q1 displayed depth, fresh direct book revalidation, q1 FOK, durable Command Center reservation before mutation, one unresolved final-lag exposure maximum, and hard halt on ambiguous submission. It only covers strict MLB prop markets where the existing exact K/P overlap/certification pipeline finds a valid candidate; do not infer universal standalone K-only/P-only coverage.

At the 03:24 UTC verification there was **no `FINAL_LAG_EXECUTION_RESULT`** in this service: no new final-lag order/fill/realized P&L from this lane yet.

#### Gemini / Kalshi MLB Hits final-lag q1 — ARMED

New branch `feature/mlb-gk-final-lag-auto-q1-v2` adds:
- `src/mlb_gk_hits_final_lag_live_q1.py`
- `src/mlb_gk_hits_final_lag_live_q1_entry.py`
- source commit **`219f69f1e0bd3c01626c319394d5e28bcd5be089`**.

Railway service `mlb-gk-final-lag-live-q1` (`3063a322-0c0d-4d08-b9c3-1d5ffafe5732`) runs `python -m src.mlb_gk_hits_final_lag_live_q1_entry`.

Disarmed validation deployment `26db4867-1f29-409b-8988-b404d6f1908e` succeeded on the exact branch/commit. Fresh Docker build ran the existing sports suite: **115 passed**. Runtime then showed repeated healthy heartbeats with `gemini_connected=50`, `gemini_updates=50`, `hard_halt=False`, and no mutation while `armed=False`.

After clean validation, the service was armed. Production deployment **`25a76f55-ceb0-4de2-93e1-2c669c322fe7`** succeeded and the first verified runtime heartbeat showed:
- `armed=True`
- `hard_halt=False`
- `gemini_connected=50`
- `gemini_updates=50`
- `stable=0`
- `positive=0`

This lane is intentionally **MLB Hits only** for now. It requires exact direct-orientation Gemini/Kalshi Hits identity, MLB Final + starter/plate-appearance proof + 60s/3-observation stability, >=$0.10 conservative after-fee edge, <=100ms book freshness, 50ms exact persistence/revalidation, q=1 FOK, <=$1 capital, durable reservation, the existing shared `sports_gk_*` $5 cumulative ceiling, and hard halt on ambiguous/unexpected partial submission. Kalshi market data uses direct exact REST rather than opening another authenticated Kalshi WebSocket. FanDuel/sportsbooks are unreachable from this transport.

At the 03:24 UTC verification there was **no `MLB_GK_FINAL_LAG_EXECUTION_RESULT`**: no new G/K final-lag order/fill/realized P&L yet.

Do not broaden the Gemini final-lag lane to HR/total bases/strikeouts merely because those families exist elsewhere; settlement-lag rule/correction semantics need separate certification first. Do not count an armed scanner, candidate, submitted order, or fill as realized profit.

## LATEST PRODUCTION CHECKPOINT — 2026-08-23 03:16 UTC

This section supersedes older basis-risk details below where they conflict. Historical sections are preserved for audit context.

### Basis-risk lane: first real probe PROFITABLE + automatic rollover LIVE

The dedicated Gemini/Kalshi settlement-basis q1 lane has now produced a real, fully settled profitable live probe. This is **not guaranteed arbitrage**.

Authoritative trade:
- trade id `gkbasis-br-0822-2245-esx-03-01`
- asset SOL
- direction `KALSHI_YES_GEMINI_NO`
- Gemini `GEMI-SOL15M2608230300-UP`
- Kalshi `KXSOL15M-26AUG222300-00`
- q=1; actual capital **$0.966300**
- modeled locked quote profit **$0.0300**
- threshold gap abs **~3.0631bp**; candidate matched threshold-dominance safe direction
- both venue orders filled
- both official venue outcomes settled YES
- exact public venue settlement reconciliation
- authoritative `TRADE_REALIZED` net P&L **+$0.033700**
- `venue_outcomes_agree=true`

Authoritative basis ledger at rollover promotion:
- `CAPITAL_RESERVED`: 1
- `TRADE_REALIZED`: 1
- realized losses: 0
- basis realized net P&L: **+$0.033700**
- unresolved submitted crypto trades: 0
- unresolved basis reservations: 0

The old fixed 12:30 AM arm expiry is no longer an operational dormancy point. Service `crypto-basis-risk-q1-canary` (`9f6e8d88-868a-4a38-9758-e74fceb71ed6`) now runs:
`python -m src.gemini_kalshi_basis_risk_rollover_supervisor`
from branch `feature/crypto-basis-risk-q1-canary`.

Production deployment `4977c651-a3e7-418d-a611-e8b398f9f0be` on source marker commit `026c50ab2987a13da37d4467e92753bd67678aad` verified:
- `CRYPTO_BASIS_RISK_ROLLOVER_ENABLED`
- 120-minute fresh authorization leases
- q=1 / <=$1 per probe
- promoted total probe ceiling **3**, including the already-settled first probe
- approved maximum-loss research budget remains **$5**; not reset/increased
- >=0.5bp protective threshold separation
- >=3c modeled after-fee locked edge
- ETH/SOL/XRP only; BTC remains quarantined
- one unresolved/reserved trade maximum
- **any realized loss hard-stops all future automatic rollover**
- every prior reservation must be authoritatively `TRADE_REALIZED` before another can reserve
- fresh short campaign ID and fresh deadline each lease; no expired arm token is reused
- process-local basis authorization only; shared guaranteed-arb settlement-equivalence lock unchanged
- q5 remains blocked

First automatic lease started successfully at 03:16 UTC:
- campaign `br0823031601`
- deadline `2026-08-23T05:16:07Z`
- parent saw reservations=1, realized=1, realized net +0.033700
- child logged `CRYPTO_BASIS_RISK_V5_PROMOTION_INSTALLED probe_limit=3`
- authenticated Gemini/Kalshi preflights passed
- current exact books for ETH/SOL/XRP are scanning

Rollover safety implementation:
- `src/gemini_kalshi_basis_risk_q1_child_v5.py`
- `src/gemini_kalshi_basis_risk_q1_campaign_v5.py`
- `src/gemini_kalshi_basis_risk_rollover_supervisor.py`
- targeted rollover tests: **6/6 passed**
- existing Docker safety suite on fresh source build: **48/48 passed**

Do not promote beyond the current three-total-q1 ceiling without reviewing the additional settled probes. Do not call this lane guaranteed arbitrage. The correct classification remains **LIVE BASIS-RISK PROBE** until each filled trade settles and is reconciled.

## State definitions

- **PAPER/SHADOW**: observe/simulate only; no orders.
- **LIVE PROBE**: bounded real-order capability.
- **EXECUTABLE**: exact counterpart/rules + fresh after-fee economics/depth/capital.
- **EXECUTED**: all required legs confirmed filled.
- **REALIZED**: executed trade settled/reconciled and net P&L confirmed.

Never count stale quotes, mismatched settlement rules, shadow P&L, armed state, attempted orders, MVE creation/RFQs, or unconfirmed fills as realized arbitrage profit.

## Current realized P&L truth

Authoritative ledger evidence last reconciled earlier on 2026-08-22:
- 2026-08-22 ET day `gemini_kalshi_15m_crypto`: 8 realized events, net **-$0.536200**.
- Historical BTC backfill `gk15m-gkr-260819-1705-04`: **+$0.032800 REALIZED** only after exact public venue settlement reconciliation.
- No sports or weather `TRADE_REALIZED` event was present in the ledger at the last authoritative snapshot.

No new Gemini/Kalshi basis-risk order, fill, executed trade, or realized trade was present in the evening campaigns through 02:05 UTC. The prior evening campaign `basis-0822-rest-02` expired with zero order submissions and zero realized trades; its successor dynamic pilot had zero durable claims, zero `ORDER_SUBMITTED`, and zero `TRADE_REALIZED` at the last live check.

No same-Gemini sports combo order was submitted, filled, executed, or realized during the 20:58-21:00 UTC work below.

## Critical crypto incident / containment

Gemini/Kalshi 15-minute crypto is **NOT settlement-equivalent and must remain fail-closed as guaranteed arbitrage**.

Authoritative incident: BTC trade `gk15m-gk15m-08220515-54da-01` filled both venues but Gemini settled YES while Kalshi settled NO, producing `REALIZED_INDEX_MISMATCH` and **-$0.885300**. Kalshi KXBTC15M uses CF Benchmarks BRTI while Gemini Prediction crypto uses a different settlement source; matching strike/duration/price does not prove identical settlement.

Safety response already merged:
- PR #111 added a boot-level settlement-equivalence arm.
- PR #112 moved the lock into shared `live_probe_authorized()` with code-level settlement equivalence hard-false; env flags alone cannot re-enable guaranteed-arb q1/q5.
- q5 promotion remains blocked.
- Existing quote freshness, persistence, capital reservation, execution reconciliation, rollover, and hard-halt gates remain required.

The old bounded-campaign dormancy diagnosis was real but is no longer the governing guaranteed-arb crypto blocker. Settlement equivalence is.

## Gemini/Kalshi 15m basis-risk q1 research — CURRENT

This is a **separate LIVE BASIS-RISK RESEARCH lane**, not guaranteed arbitrage. Do not use it to bypass the shared settlement-equivalence hard lock or promote q5.

Service `crypto-basis-risk-q1-canary` (`9f6e8d88-868a-4a38-9758-e74fceb71ed6`) runs branch `feature/crypto-basis-risk-q1-canary`, restart `NEVER`, via `python -m src.gemini_kalshi_basis_risk_q1_campaign_v2`.

### Kalshi 429 / market-data repair

Authenticated Kalshi WebSocket handshakes continued returning HTTP 429 even after the account API tier was upgraded. The basis child uses a process-local exact authenticated REST transport instead of opening another Kalshi WS:
- exact `GET /trade-api/v2/markets/{ticker}/orderbook?depth=1`;
- default 50ms per-ticker polling, max eight tickers;
- quote timestamp is **HTTP request start**, so network RTT can only make a quote older;
- one-sided books preserve the quoted side and assign zero depth to the missing side; no liquidity is invented;
- REST 429 => no quote + backoff; stale/missing data fails closed;
- downstream <=100ms freshness, <=50ms skew, 50ms persistence, q=1 exact depth and fee gates remain required.

Production validation before the dynamic pilot:
- targeted transport tests: **6/6 passed**;
- read-only production smoke: **20/20 exact books HTTP 200 + parsed**;
- **19/20 <=100ms** using request-start timestamp; warm RTT roughly 18-36ms; one cold-start request was 100.49ms and would be rejected by the existing freshness gate;
- repaired scanner processed hundreds/thousands of live evaluations without Kalshi reconnect failure.

Occasional REST 429 bursts still occur under aggregate account traffic, including in the current dynamic campaign. They fail closed and recover. Do not treat 429/missing-book cycles as executable quotes. If they become the dominant opportunity-loss source, smooth/stagger request bursts rather than relaxing freshness.

### Historical fixed-10bp campaign — SUPERSEDED

Campaign `basis-0822-rest-02` used q=1 / <=$1 / one trade max / $5 hard budget / >=3c quote edge / >=10bp protective threshold separation / <=50ms skew+persistence / <=100ms freshness / exact q1 depth / durable reservation / one unresolved trade max.

It found real quote candidates but submitted **0 orders** and realized **0 trades** before its arm deadline. The fixed >=10bp threshold-separation gate was materially above current Gemini-vs-Kalshi threshold geometry:
- ETH example: 7c quote lock, ~2.218bp gap, wrong protective orientation -> correctly blocked.
- BTC examples: repeated 3.0-4.6c quote locks, ~0.688bp gap, wrong protective orientation -> correctly blocked.
- XRP example: 3c quote lock, ~2.093bp gap, **correct protective orientation**, blocked solely because <10bp; the official settlements later resolved NO/NO on both venues.

Read-only settlement-basis profile on 236 matched settled windows / 232 complete transitions found:
- maximum observed absolute threshold gap by asset: BTC **1.519bp**, ETH **4.166bp**, SOL **4.514bp**, XRP **6.213bp**;
- current sample had **0 >=10bp transitions**;
- 131 transitions had >=1bp gaps, 65 had >=2bp, 24 had >=3bp, and two XRP transitions were in 5-10bp;
- **0 observed threshold-safe-direction dominance breaches** across the 232 complete transitions;
- one SOL venue-outcome disagreement occurred in the 1-2bp bucket, but it was not adverse to the threshold-safe orientation.

These broad historical results alone were not sufficient to lower the live gate. Quote-conditioned evidence was required next.

### Quote-conditioned exact-candidate reconciliation — COMPLETE

The old exact-revalidation shadow had five distinct Aug. 18 BTC windows where a real q=1 quote candidate survived the 50ms exact-price/depth revalidation. A new read-only reconciler direct-fetched the exact official Gemini historical event and Kalshi market for each recorded pair and resolved **5/5**.

Read-only reconciliation deployment: `crypto-threshold-dominance-oneoff` deployment `6ab4f4ae-2e3c-4e11-a416-c53eb65bbdaa`; final reconciliation code commit `1c9b30b43c54470293c2b6ca6584c2afc5978df7` on `feature/crypto-settlement-basis-v2`.

Results:
- 18:15 BTC: 4c observed lock, 1.5952bp threshold gap, candidate orientation was **opposite** the protective direction, final YES/YES.
- 18:45 BTC: **3c observed lock**, 0.35356bp gap, candidate **matched** protective direction, final NO/NO.
- 19:00 BTC: 7c observed lock, 1.40603bp gap, candidate orientation was **opposite** protective direction, final YES/YES.
- 19:15 BTC: **7c observed lock**, 0.83417bp gap, candidate **matched** protective direction, final NO/NO.
- 20:00 BTC: **10c observed lock**, 0.18409bp gap, candidate **matched** protective direction, final NO/NO.

Quote-conditioned summary:
- exact candidates resolved: **5/5**;
- protective-direction candidates: **3**;
- adverse double-losses among those 3: **0**;
- favorable double-wins: **0**;
- normal one-winner settlements: **3**;
- combined observed quote locks on the 3 protective candidates: **20c**.

These were historical quote observations, **not fills and not realized P&L**. The sample is small, so it supports only a highly bounded pilot, not a general profitability claim.

### Dynamic q=1 first-probe pilot — LIVE NOW

User approved continuing the evidence-backed basis-risk path. Instead of immediately using the broad shadow threshold near 3c, the first dynamic live pilot was intentionally made more conservative on economics.

Current campaign: **`basis-0822-dyn-01`**, arm deadline **2026-08-23 04:30 UTC (12:30 AM ET)**.

Current live policy:
- q=1;
- max one trade;
- first-tranche capital <= **$1**;
- approved hard research-loss budget remains **$5**; not increased/reset;
- candidate direction must equal the threshold-dominance protective orientation;
- venue threshold separation >= **0.5bp**;
- modeled locked quote profit >= **$0.07**;
- <=50ms entry/current quote skew;
- 50ms exact-price/depth persistence;
- <=100ms quote freshness;
- exact q1 displayed depth;
- durable Postgres capital reservation before order submission;
- one unresolved trade maximum;
- existing same-side/distance-to-boundary safety remains: spot must remain at least **10bp away from both settlement thresholds** during the positive episode;
- shared guaranteed-arbitrage settlement-equivalence lock remains unchanged;
- `guaranteed_arb=false`;
- q5 remains blocked.

Important terminology: the old runtime log `basis_entry_min=2bp` is a public-spot proxy-margin research check, **not** the Gemini-vs-Kalshi threshold-separation gate. The 10bp double-boundary gate is a separate near-strike safety requirement and remains deliberately stricter than that proxy check. Neither means the dynamic venue-threshold gap reverted to 10bp.

Dynamic policy code/deploy chain:
- `src/gemini_kalshi_basis_risk_dynamic_policy.py` commit `fdce459b338805fdb631475be99816de7781ca5e`;
- `src/gemini_kalshi_basis_risk_q1_child_v4.py` commit `9962119b2a050c09c9fe7c71d4ee5796f9a336c7`;
- campaign launches v4 child and retains v2 durable controls + v3 gate telemetry;
- parent process-local 0.5bp policy fix commit **`da3658ccb3cc808366cc49e3e95f4945e2ba7fd1`**;
- current Railway deployment **`2c3fd92c-7c39-41c2-b8bf-2e041064b2d0`**.

Verified current boot:
- `unresolved_prior_trades=0`;
- fresh persistent arm `basis-0822-dyn-01-01` created;
- dynamic child logs `min_gap_bps=0.5`, `min_locked_profit=0.07`, q=1, capital limit $1, approved budget $5, `guaranteed_arb=false`;
- Gemini private REST/auth WS and Kalshi production auth preflights passed;
- both venues had >=$1 available for the probe;
- exact BTC/ETH/SOL/XRP books are scanning;
- at the latest live check, current window summaries had **0 positive/threshold evaluations**, **0 durable capital claim**, **0 ORDER_SUBMITTED**, and **0 TRADE_REALIZED**.

Do not call the current campaign arbitrage. If it trades, classify it as a bounded **LIVE BASIS-RISK PROBE**. Even a dual fill is **FILLED_NOT_SETTLED** until both official venue outcomes settle and authoritative net P&L is reconciled.

## Same-Kalshi / sports manufacturing

Service `sports-kp-combo-shadow` remains a high-value safe manufacturing path.

Production branch `feature/kp-combo-synthetic-shadow` includes merged PR #117 at `5f6f64a65ff5dada9a4ea29696c969111b432a5d`. v37 scans exact same Kalshi binary markets for `YES + NO = $1`, requires q=1 depth on both sides, applies conservative taker fees, and labels a shadow candidate executable only at **>= $0.03 locked profit**. Same-market identity avoids cross-venue settlement-equivalence risk.

PR #117 added diagnostic-only `SPORTS_SUPERVISOR_TELEMETRY`. At the last authoritative database check there were still no fresh sports telemetry rows, so telemetry/database connectivity remains an observability blocker until direct runtime evidence proves otherwise.

Do not infer an executable candidate until authoritative telemetry or direct runtime evidence shows >=3c locked after conservative fees with exact q=1 depth.

## Same-Gemini sports combo lane — CURRENT

### Historical live-probe incident

A 16:10 UTC q=1 pre-hedge-first probe exposed an execution-recovery bug: two hedges filled before the combo, the combo did not fill, and the old emergency flattener used unsupported `MARKET+IOC`. Recovery was completed with supported SELL `LIMIT+FOK` exits. Final reconciliation showed zero remaining positions/orders and approximately **-$0.06 realized live-probe loss**. This was not arbitrage profit.

The lane was redesigned combo-first so no hedge is bought until the combo fills. Subsequent combo-first q=1/q=3 probes produced no combo fills and no new hedge churn. Gemini also rejected same-event multi-driver F1 combos and a known `too closely correlated` same-match construction; these classes remain excluded.

### Fractional-fill finding — resting maker is now BLOCKED

The prior monitored resting-q1 design is **superseded and must not be re-armed**.

Live Gemini refdata proves combo instruments can have:
- `quantityMinimum = 1`
- `quantityIncrement = 0.01`

Therefore a submitted q=1 resting combo order is not indivisible and can receive sub-contract fills. With Gemini prediction fees conservatively rounded up to a cent, the 0.01-fill branch cannot preserve the required **>=3c/contract guaranteed floor**. Exact fractional hedge increments do not repair the fee-floor problem.

Fresh catalog/liquidity audit:
- active registered combos: 17;
- dynamically valid unresolved combos: 15;
- current watcher symbol set: 31 combo/component instruments;
- most recent refdata liquidity scan found no liquid whole-increment combo instrument;
- resting combo-maker live mode is therefore **BLOCKED** for this guaranteed-floor lane.

Do not revive campaign `gcombo-2608221328-torclb-rest1` or any other resting combo quote merely because q=1 was requested. q=1 does not imply all-or-none on these combo grids.

### Safe candidate architecture — q=1 FOK only

Branch `feature/gemini-combo-mm-v1` contains a dynamic same-Gemini FOK path:
- `src/gemini_combo_mm_fok_preflight.py`: read-only active-catalog economics for both exact conjunction covers.
- `src/gemini_combo_mm_fok_watch.py`: continuous read-only WebSocket watcher; no authenticated order entry.
- `src/gemini_combo_mm_fok_live_v1.py`: bounded q=1 combo-first LIMIT+FOK executor with dynamic canonical-leg certification, settled-PASS removal, variable leg counts, exact q1 depth, <=50ms final receive skew, >=3c conservative after-fee floor, balance/account guards, unexpected-partial hard halt, bounded `LIMIT+FOK` recovery, and post-recovery reconciliation.
- `src/gemini_combo_mm_fok_entry.py`: fail-closed production entrypoint. DISARMED boots are public-only; when genuinely armed it replaces per-symbol active-order REST scans with one unfiltered `/v1/prediction-markets/orders/active` call filtered locally. A full 100-order page fails closed.

Why the entrypoint was added: an earlier disarmed deployment crashed with `Gemini time-based nonce rate exceeded` because it queried active orders separately for every combo/component symbol. This was an observability/preflight bug, not a trading loss. It is now fixed without weakening active-order checks.

Validation at ~20:59 UTC:
- Railway test service `gemini-combo-mm-test-v1`: **15 passed**.
- Disarmed executor service `gemini-combo-mm-canonicalize-v1` (`efe9ef29-18b2-4908-9953-11d7b8ca3a67`) booted successfully on commit `79ff8f5823354bf8a803b7ff4be9d73e41b8d902` with `armed=false`, 17 registered combos, 15 valid dynamic candidates, private preflight skipped, `order_entry=false`, `realized=false`, restart `NEVER`.
- Read-only watcher `gemini-combo-mm-fok-preflight-v1` (`71b403d8-77dc-49ab-837a-ea628026f084`) refreshed the same 17-combo catalog / 15 valid combos / 31 symbols and reported **0 active executable signals** on its fresh heartbeat.

Current classification:
- resting combo maker: **BLOCKED**;
- WebSocket FOK watcher: **SHADOW / READ-ONLY ACTIVE**;
- q=1 FOK executor: **LIVE-CAPABLE CODE, DISARMED**;
- current executable signal count at last check: **0**;
- new orders: **0**;
- new fills: **0**;
- new executed trades: **0**;
- new realized sports arb P&L: **$0**.

Do not arm the FOK executor just to test it when there is no executable signal. A future live promotion still requires a fresh >=3c q1 signal, exact canonical certification, final <=50ms q1 depth/economics, clean account state, sufficient capital for all legs, and the existing bounded recovery path. Separate FOK orders are not atomic across legs; that residual execution risk must remain explicit.

## Direct sports K↔P

`sports-kp-live-executor` remains read-only certification scan unless exact counterpart settlement rules and >=3c after-fee economics are proven. NFL full-game totals previously failed exceptional-event lifecycle equivalence; identity/line matches alone are insufficient.

## Sports G↔K props

`sports-gk-autonomous-supervisor` remains LIVE RULE-RISK PROBE / not certified guaranteed arb. Historical realized total +$0.1871. Authoritative rule-risk budget remains **$4.84/$5.00**. Do not reset/increase without explicit user authorization.

## Weather

Reconcile any unresolved historical weather order before new live execution. Preserve exact settlement-rule certification.

## Railway preservation / cleanup

Preserve CORE: `crypto-15m-sol-live-canary-10bp-50ms` (fail-closed), `sports-kp-live-executor`, `sports-gk-autonomous-supervisor`, `weather-live-probe`, `arb-command-center`, `Postgres`, `sports-trade-settlement-reconciler`, `crypto-live-balance-reconcile-readonly`.

Preserve active shadows/workstream services while their lanes are active, including `crypto-maker-arb-shadow-v1`, `sports-kp-combo-shadow`, `mlb-gk-exec-shadow`, `weather-shadow-arb`, `three-venue-router-paper-v2`, `three-venue-mlb-hot-paper-v1`, `gemini-kalshi-mlb-prop-hot-shadow`, `sports-alert-reliability-shadow`, `gemini-extreme-combo-live-q1`, `gemini-combo-mm-fok-preflight-v1`, `gemini-combo-mm-test-v1`, the disarmed FOK executor service currently named `gemini-combo-mm-canonicalize-v1`, and the active `crypto-basis-risk-q1-canary` while the bounded pilot is running.

Remove only verified dead/test/superseded services after dependency checks.

## Next-run protocol

1. Read `ARB_STATUS.md`, then inspect current Railway/GitHub production state before mutation.
2. Keep guaranteed-arb Gemini/Kalshi crypto q1/q5/maker blocked unless exact settlement + threshold equivalence is independently certified. Treat `crypto-basis-risk-q1-canary` separately as bounded basis-risk research only.
3. **LATEST OVERRIDE:** inspect v6 automatic rollover and the authoritative basis-risk ledger first. Do not use stale historical campaign deadlines or v5 concurrent-execution details as current production state.
4. v6 automatic basis-risk rollover may create fresh 120-minute leases only while: total reservations <20; no unresolved reservation/submission exists; the only realized negative row remains the exact reviewed `gkbasis-br0823131801-01` -$0.09 one-leg execution incident; cumulative realized loss remains below the unchanged $5 maximum-realized-loss budget; and all live execution gates pass. Any new/unreviewed loss hard-stops.
5. **Execution order override:** basis v6 must execute `KALSHI_THEN_GEMINI`. If Kalshi does not confirm q=1 first, Gemini must never submit. Never restore concurrent dual-FOK for this lane.
6. A Kalshi-first safe rejection may close its durable reservation only via an authoritative zero-P&L `NO_FILL_RELEASED`; this is not profit and not a filled trade.
7. Any actual basis submission/fill remains LIVE BASIS-RISK PROBE / FILLED_NOT_SETTLED until official venue settlement/reconciliation. Only then count REALIZED P&L.
8. If REST 429 bursts materially suppress qualifying evaluations, stagger/smooth exact-book requests rather than weakening freshness or reintroducing an unauthorized WebSocket path.
9. For same-Gemini sports combos, keep **resting maker mode blocked**. Never infer indivisibility from q=1.
10. Verify the read-only FOK watcher is receiving all current symbol snapshots and whether `active_executable_signals` is still 0.
11. If a >=3c q1 FOK signal appears, re-run exact canonical metadata, final <=50ms q1 depth/economics, positions, active orders, and capital reservation immediately before any bounded live promotion.
12. Any future live FOK campaign must use a fresh unique campaign + matching arm token + short boot expiry, restart `NEVER`, and cleared arm variables after boot. Never reuse an expired campaign.
13. Continue sports breadth and exact-rule weather opportunities without weakening certification. Work one blocker through inspect -> fix -> deploy -> verify -> reconcile -> checkpoint before widening scope.

Never store credentials, signing material, API keys, or sensitive balances here.