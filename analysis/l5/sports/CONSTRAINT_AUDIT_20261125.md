# Sports controller constraint audit (Polymarket US, RN1 path)

Code audited: `git show 6a9a3e20a:` (branch `rn1-profit-first`, release 766a965ba). Read-only. The checkout's working tree had uncommitted edits to `src/pmus_sports_two_sided.py` during the audit, so every line number below is the committed HEAD version, not the working tree.

File keys: **TS** `src/pmus_sports_two_sided.py`, **RN** `src/pmus_rn1_replica.py`, **RB** `src/pmus_request_budget.py`, **AR** `src/l5_account_risk.py`, **PH** `src/pmus_sports_phase.py`, **MX** `src/pmus_micro_executor.py`, **Q1** `src/pmus_q1_executor_v4.py`, **XV** `src/pmus_sports_xv.py`, **RW** `src/pmus_sports_rewards.py`.

Classes: **SAFE** safety-critical, **RISK** risk control (owner decision), **STRAT** strategy, **ENG** engineering bottleneck or artefact. "Default → prod" gives the code default, then tonight's value; `=` means production is at the default. Variable prefixes are shortened: `T_` = `PMUS_TWO_SIDED_`, `R_` = `PMUS_RN1_`. "New exposure" means opening and same-side bids; completions are exempt unless stated.

## Findings that are not obvious from the settings list

1. **`T_TOTAL_USD` (legacy total cap, default 4.50) still gates the RN1 path.** Unpaired filled cost at or above 4.50 blocks all new exposure (TS:5074) and all selection (TS:5822). It sits below the campaign unpaired cap of 6, so it binds first. Production value UNVERIFIED (not in the list supplied; assumed default).
2. **Dota 2 is on the allow list but cannot open.** The esports rule refuses every esports match until the venue publishes a best-of format, which the code comment says it does not (TS:1749-1786, 1846-1857).
3. **A send that was never transmitted is treated as uncertain.** `place()` releases a reservation only on `RejectedNoFill`; a `BudgetDeferred` or `POST_ONLY_STALE_REFUSE` (both raised before the order POST) holds capital until the 30 s orphan-sweep branch reconciles, and that branch does not run during the audit (TS:4521-4526, 6803-6818).
4. **Two different slot counters.** Selection counts flat markets (TS:3299-3310); quoting counts markets with an unpaired leg or a resting opening bid (TS:4638-4648). A carried unpaired leg therefore takes one of the four quoting slots but no selection slot, so a fourth market is selected and then blocked `max_markets`.
5. **`R_EXTEND_MAX_UP` and the refill cooldown also apply to balanced inventory.** After any pair exists, each side's next bid is capped at that side's average cost + 2c and waits 60 s after that side's last fill (RN:291-302).
6. **After every restart there is no new exposure until one audit completes** (`T_AUDIT_MAX_AGE_S` 4500 counts "none since boot" as stale; first audit starts ~2 min after boot and takes ~10 min) (TS:2334, 6134-6138).
7. **Reward measurement is off with the fair-value test off.** `_rn1_reward_estimate` is reached only through `rn1_incentive`, called only when `R_EDGE_GATE` is on (TS:4952-4957), so `qualifying_contract_s` never accrues (TS:4805-4809).

## Capital and loss limits

| Constraint (variable) | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Account drawdown limit (ledger `max_drawdown_usd`; `T_ACCOUNT_RISK`=1) | off → 5.00 | AR:87-167, 170-180; TS:4331-4364, 5082 | Capacity = limit − ledger figure − all own resting bids (worst case) − unpaired fills newer than snapshot; a bid must fit. Owner 23 Nov: one account-wide drawdown for every money-capable service | SAFE | 60 capacity refusals; peak 4.53 of 5.00 | Linear room for more markets or size; loss ceiling rises equally |
| Capacity room margin (`T_ACCOUNT_RISK_ROOM_MARGIN_USD`) | 0.01 = | TS:360, 4359-4361 | Bid must leave 1c so a bid that exactly fits does not pull everything (24 Nov place-and-cancel cycle) | ENG | Part of the 60 | None useful |
| Account daily-loss halt and cumulative ceiling (ledger figures) | ledger | AR:258-280 | Day realized ≤ −halt or lifetime ≥ ceiling refuses. Owner 18 Nov: same account-wide controls as BTC lanes | SAFE | Not observed | Do not relax |
| Lane exposure cap (`L5_ACCOUNT_MAX_EXPOSURE_USD` / `L5_ACCOUNT_LANES`) | unset = refuse → 20 | AR:334-341; TS:4208-4214, 4347 | Unpaired cost + face value of every resting order + this order ≤ lane share | SAFE | Not observed | Needed above ~20 one-contract markets |
| At-risk budget (`T_AT_RISK_BUDGET_USD`) | 0 (off) → 16 | TS:1216, 4472-4485; PH:154 | Same face-value sum ≤ budget. Owner 22 Nov: cap outstanding orders so simultaneous fills cannot bypass the limit | RISK | Not observed | Binds near 16 markets at 1 contract |
| Campaign spend (`R_TOTAL_SPEND_USD`) | 16 → 30 | RN:158; TS:4650-4674, 4594-4598; latch TS:3587 | Filled cost + resting + reserve to complete at pair cap, all unsettled campaign markets. Owner 22 Nov | RISK | Not observed | Paired inventory counts until a settlement price is recorded (hourly audit only; `T_SETTLE_CHECK_S`=0), so it erodes over a long window |
| Campaign unpaired (`R_TOTAL_UNMATCHED_USD`) | 6 = | RN:159; TS:4662-4665, 4599-4603 | Worst-case unpaired cost (filled + any resting bid filling alone) across campaign | RISK | Never reached (peak 0.63 filled); worst-case counting uses ~0.55 per quoted market | First strategy cap to bind when scaling: ~10 markets at 1 contract |
| Legacy total cap (`T_TOTAL_USD`) | 4.50 → UNVERIFIED | TS:77, 5074, 5822 | Filled unpaired cost ≥ cap: no new exposure, no selection | RISK (artefact on RN1 path) | Not observed | Must rise with the unpaired cap or it silently overrides it |
| Per-market spend (`R_PER_MARKET_USD`) | 8 = | RN:78, 338-348, 441-449; TS:4588; latch TS:3581 | Filled + this bid + completion reserve ≤ 8 | RISK | Not observed | Allows deeper stacking in one market |
| Per-market unpaired (`R_RESIDUAL_CAP_USD`) | 3 = | RN:77, 327-331; TS:4591 | Unpaired cost after this fill ≤ 3 (RN1 "bounded unmatched inventory") | RISK | Not observed (peak 0.63) | More directional carry per market |
| Campaign loss stop (`R_PILOT_LOSS_USD`) | 0 → 4 | TS:116, 6175-6225 | Realized net ≤ −4 latches; also blocks on book worst case, stale meter (`R_PILOT_METER_MAX_AGE_S` 7800 s), unconfirmed fills. Owner 21 Nov: meter must fail closed | SAFE | Not observed | Do not relax without owner |
| Lane daily loss stop (`T_DAILY_LOSS_USD`) | 3 → 5 | TS:264, 2934-2949 | Mark-to-market day change < −5: no new exposure until next UTC day. Owner 15 Nov "we're bleeding money" | SAFE | Not observed | Do not relax without owner |

## Inventory rules

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Pair cap (`R_PAIR_CAP`) | 0.97 = | RN:76, 280-285, 425-437; final gate TS:4571-4578 | Completion bid ≤ cap − held average (rests deeper if not yet profitable); two opening bids together ≤ cap, dearer side stepped down. Owner: complete only when profitable | RISK | Shapes every quote; with a 1c spread both sides cannot sit at the touch (sum 0.99) | Higher cap = more completions, thinner locked edge |
| Completion size | hard-coded | RN:286-289 | min(order size, shortfall), raised to venue minimum | STRAT | 3 completions | n/a |
| Same-side add: price ceiling (`R_EXTEND_MAX_UP`) | 0.02 = | RN:79, 299-302 | Bid ≤ side average + 2c ("don't chase it up"); applies whenever the side holds anything, paired or not | STRAT | 0 same-side adds observed | Accumulates into rising prices; more adverse fills |
| Same-side add: fair-value test (`R_EXTEND_EV`, `R_EXTEND_EDGE_TICKS` 1, `R_EXTEND_STEP_TICKS` 1, + event jump ticks `R_JUMP_TICKS`) | on → 0 (off) | RN:105-107, 304-325; TS:4974-4984 | fair − bid ≥ tick × (1 + clips unpaired + jump − reward credit); no fair value = no add | RISK | Off tonight | n/a |
| Marginal test and risk step (`R_EDGE_GATE`, `R_ADVERSE_TICKS` 1, `R_RISK_STEP_TICKS` 1, `R_EDGE_UNIT_USD` 0.01; `R_FAIR_STABLE_S` 10, `R_FAIR_MAX_RANGE_USD` 0.01) | on → 0 (off) | RN:154-157, 198-239, 349-417; TS:462-474, 4692-4720 | Every bid needs fair − bid − fee − adverse ≥ risk step × clips; no settled fair = no bid, completions included (issue #251) | RISK | When on: "no_fair" dropped one completion 45 times in an hour | Off tonight; opening fills lost 3.4c (988 contracts) to 14c (30) at settlement without it |
| Market count for new exposure (`T_MAX_MARKETS`, `rn1_market_slots_full`) | 3 → 4 | TS:72, 4638-4648, 5124 | New exposure only while fewer than 4 other markets hold an unpaired leg or resting opening bid. Owner 22 Nov after three markets quoted under a limit of two | RISK | 24 decisions blocked, caused by a carried unpaired leg in a no-longer-allowed competition | Count only in-scope markets, or raise the number: +1 market immediately |
| One market per game | hard-coded | TS:6004-6010 | Never two markets of the same event. Owner 22 Nov: no cross-outcome hedging | RISK | Unknown | More slots filled on busy events; correlated exposure |
| Foreign exposure skip | hard-coded | TS:2882-2890 (HEAD), 6001 | No selection where another strategy or a human holds a position or order | SAFE | Unknown | Do not relax |
| Buy-only, hold to settlement | hard-coded | RN:14-15; TS:5257-5259 | No sells, no merges; capital recycles only at settlement | STRAT | Always | Selling would free capital; not built |

## Order size and placement

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Order size (`R_QTY`; `R_PREGAME_QTY`, `R_LIVE_QTY` unset = `R_QTY`) | 2 → 1 | RN:72; TS:1201-1205, 5127-5132 | Contracts per bid. Owner 22 Nov "frequency before size" | RISK | Always (every fill is 1 contract) | Linear capital use and loss per fill |
| Largest clip (`rn1_max_clip`) | max of the three | TS:1394-1396; final gate TS:4558; latch TS:3593 | An order above the clip is refused; a resting one latches the bot | SAFE | Not observed | Follows size settings |
| Venue minimum size | venue `minimumTradeQty` | TS:2208; RN:288 | Floor on size | venue | n/a | n/a |
| Volatility shrink (`T_VOL_SHRINK_USD`, `T_VOL_WINDOW_S` 30) | 0 → 0.02 | TS:1206-1208; PH:103-112 | In-game size halved while mid ranged ≥ 2c in 30 s; never below the minimum | RISK | No effect at size 1 | Matters only at size ≥ 2 |
| Quote depth (`R_DEPTH_TICKS`) | 1 = | RN:73, 274 | Bid = own-side touch − 1 tick (ESTIMATED setting, not calibrated) | STRAT | Always: fills only when price trades through (7 fills / 99 orders; every 22–23 Nov exposure fill on a ≥ 2-tick jump) | 0 = join the best bid: more fills, queue position, reward score; pair cap then forces one side back when spread is 1c |
| No improving the best bid | hard-coded | RN:335-337 | `px ≥ touch + tick` is refused (`would_cross`); a negative depth cannot improve | STRAT | Always | Needs code change; not recommended before joining is measured |
| Reactive pull (`T_OPEN_OFFSET_PX`, `T_DEPTH_TICKS`; needs `T_WS`=1) | 0.02, 2 → 0, 1 (handover of 22 Nov; UNVERIFIED tonight) | TS:57-70, 1598-1616, 2461-2476 | On a touch change in a flat market, any opening bid closer than N ticks to its touch, or within 1 tick of crossing, is cancelled and re-set. Owner 14–15 Nov "stop being picked off" | RISK | Pulls the bid whenever the touch falls to it; count unknown | With the 2-tick default it would cancel every 1-tick RN1 bid on every touch change |
| Reprice threshold (`R_REPRICE_TICKS`) | 1 = | RN:81; TS:5211 | Resting bid ≥ 1 tick from target is cancelled and re-placed | STRAT | Main source of order churn | 2 = fewer cancels and requests, staler prices |
| Keep deeper (`R_KEEP_DEEPER_TICKS`) | 0 → 1 | TS:1338, 5212-5215 | Opening bid up to 1 tick below target is left (touch moved away); keeps queue place | ENG relief | Active | Larger = fewer re-posts, bids further from market |
| Price limits on bids (`R_PX_MIN`, `R_PX_MAX`) | 0.20, 0.92 = | RN:74-75, 332-334 | RN1 lost below 0.20 (−29.6% under 0.05) | RISK | Unknown | Wider = more quoting in losing cells |
| Minimum touch depth (`T_MIN_TOUCH_QTY`, cache `T_DEPTH_TTL_S` 10) | 10 = | TS:412-413, 4411-4436, 5159-5163 | Own-side touch must show ≥ 10 contracts not ours; unknown depth blocks. Owner 22 Nov: "a one-contract touch is not a price" | RISK | Unknown; likely frequent on thin table tennis | Lower = more quoting in thin books, worse reference price |
| International price guard (`T_INTL_GUARD`, `_REQUIRE`, margin 1 tick, max age 20 s) | off, require 1 → on, require 0 | TS:7280-7296, 5164-5175; XV:832-847 | Opening bid withheld if above international mid − 1 tick; no international price = allowed (require 0) | RISK | Unknown; allowed competitions mostly have no international price | Require 1 would block them entirely |
| Maker only (`T_ALLOW_TAKER`) | 0 = | TS:1328, 4556 | Non-post-only order refused | RISK | n/a | Taker fee 9.5% × p(1−p) |
| Post-only stale refuse | hard-coded | MX:381-383 | After preview, a fresh book read; bid ≥ ask is not sent | SAFE | Unknown | Do not relax |
| Final gate (`T_FINAL_GATE`) | 1 = | TS:1327, 4541-4606 | Re-checks size, maker, per-market and campaign ceilings, and pair cost against every bid resting on the other side, immediately before sending. Owner 24 Nov | SAFE | Unknown | Do not relax |
| One order per side | hard-coded | TS:5216-5218, 5253-5259 | One kept order per side per market; anything else is cancelled first | STRAT | Always | Ladders need code |
| Both sides at once | — | RN:272-298, 425-449; TS:5153-5176 | Both sides are quoted from flat. A side is dropped by: pair cap repricing below `PX_MIN`, per-market cap, refill cooldown, price limits, depth, guard, pin, campaign caps (dearest dropped first) | — | Campaign/account capacity dropped sides (60 refusals) | — |
| Placement cost | hard-coded | MX:363, 381, 394 | Preview POST + public book GET + order POST = 2 signed requests | ENG | 26 sends deferred | Skipping the preview halves placement cost; loses the venue's echo check |
| Reservation before send; release only on reconciliation (`T_RESERVATION_HOLD_S` 5) | 5 = | TS:4037-4138, 4516-4531, 6803-6818 | Capital reserved before sending; an unanswered send stays reserved until an orphan sweep over an account read ≥ 5 s newer and a complete positions walk agree. Owner 24 Nov | SAFE (order uncertainty) | 25 episodes, ~70 min total, up to 402 s, all budget-deferred sends | Releasing at once for sends that provably never left (deferred, stale refuse) removes these without touching the uncertain case |
| Pin (`R_PIN`) | unset = | TS:1243-1264, 5109-5111 | Restricts markets/sides when set | RISK | Off | n/a |
| Order expiry (`T_ORDER_TTL_S`) | 0 = | TS:586-595 | Self-expiring orders, off | SAFE (unused) | Off | n/a |

## Timing and cooldowns

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Refill cooldown (`R_REFILL_COOLDOWN_S`) | 60 = | RN:80, 291-298 | A side gets no opening or same-side bid for 60 s after that side's own last fill (stamp TS:3329). Does not gate completions. RN1 same-side gap p25 54 s | STRAT | Unknown; consistent with 0 same-side adds | 0 = immediate re-quote after a fill; faster accumulation into a moving price |
| Loop period (`T_LOOP_S`) | 5 = | TS:91, 6852, 2534-2546 | Sleep between passes; socket events wake it early. A pass reads every open order serially, so pass ≈ 10 s | ENG | Always | Shorter adds status requests proportionally |
| Selection and account read cadence | hard-coded 20 s | TS:6752-6771 | Account read (2 signed GETs), safety check, selection every 20 s; 10 s retry after a failed read (`T_ACCOUNT_RETRY_S`) | ENG | 6 normal requests/min | Not a throughput limit |
| Account freshness (`T_ACCOUNT_FRESH_S`) | 60 = | TS:96, 3440-3446, 4457 | New exposure needs an account read ≤ 60 s old; a completion on a stale account needs venue-confirmed inventory (TS:4366-4409). 21 Nov rate-limit audit | SAFE | Unknown | Do not relax |
| Account-risk cache (`T_ACCOUNT_RISK_TTL_S`) | 10 = | TS:345, 4163 | Per-market block check reuses a verdict for 10 s; a placement always recomputes | ENG | — | n/a |
| Account-risk reader (`T_ACCOUNT_RISK_READ_S` 5; snapshot max `T_ACCOUNT_RISK_SNAPSHOT_MAX_S` 30; document age `RISK_MAX_AGE_S` 30 hard-coded; fetch timeout 3 s) | = | TS:370-379, 4178-4193; AR:43, 58, 254 | Snapshot or document older than 30 s, or unreadable: no new exposure, and resting opening bids are cancelled on the next pass (TS:5082, 5153) | SAFE | Blip every 15–30 min, each cancels and re-places every opening bid | A short grace for resting bids (as `R_GAP_HOLD_S` does for fair value) would stop the churn; new bids should stay blocked |
| Own-exposure-added window | hard-coded 70 s | TS:4230-4249 | Unpaired growth since the lowest point in 70 s is subtracted from capacity on top of the ledger figure | SAFE | Double-counts a fill after the ledger catches up; pulls opening bids once | Stamp-based netting would remove the double count |
| Stale-quote ladder (`T_STALE_S` 60) | legacy | TS:127 | Not used on the RN1 path | — | n/a | n/a |
| Cancel confirmation | hard-coded | TS:2298-2315; Q1:295-314, 65 | Cancel POST then up to 12 status polls 0.1 s apart; status retries up to 6.7 s on 404/429/5xx | SAFE | ~2.2 critical requests per placement cycle | Do not relax |
| Fair-value gap hold (`R_GAP_HOLD_S`) | 0 → 20 | TS:1283, 4986-5040 | Only acts with the fair-value test on | ENG relief | Inactive tonight | n/a |
| Keep tolerance (`R_KEEP_TOL_USD` 0, `R_KEEP_MAX_S` 60) | off = | TS:1228, 1265 | Hysteresis for the fair-value test; off | — | Off | n/a |

## Market selection and filters

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Competition scope (`T_OPEN_GATE_ALLOW_SPORTS`, `T_STRICT_SPORTS`) | empty, 0 → setkameua, setkamemd, utr, dota2; 1 | TS:1490-1494, 2961-2968, 5100, 5867, 4500 | No selection or new exposure outside the list. Owner 23 Nov: "must not silently expand beyond its authorization" | RISK | Limits supply; 14 markets selected then released unfilled | Each added competition adds candidates; these thin sports showed 2–4c adverse markout |
| Esports format rule (`T_ESPORTS_LEAGUES`) | hard-coded rule | TS:1749-1797, 1846-1857 | No new exposure unless best-of and map score are known and no team is one map from winning. Owner 22 Nov, fail closed | RISK | Blocks all Dota 2 | Publishing-dependent; removing the rule quotes to the last round |
| Late cutoff (`T_LATE_CUTOFF`) | 1 = | TS:1685, 1860-1918 | No new exposure: tennis set ≥ 3, table tennis set ≥ 4, football/basketball Q4, hockey P3, soccer 70'+, overtime, ended. Owner 14 Nov: 20/20 late lone legs lost | RISK | Unknown; frees slots of late markets | More quoting in deciding stretches |
| Cricket and excluded tags (`T_EXCLUDE` "cr-"; cricket hard-coded) | = | TS:1691-1726 | Never selected; cricket also abandoned. Owner 15 Nov | RISK | n/a under strict scope | n/a |
| Selection band (`T_BAND_LO/HI`) | 0.25/0.75 → 0.30/0.70 | TS:88-89, 5863, 1933 (via 6013) | YES best bid must be in band at selection (listing quote and live book). Not re-checked while quoting; `PX_MIN/MAX` apply there | RISK | Unknown | Wider = more candidates, more lopsided books |
| Maximum spread (`T_MAX_SPREAD_TICKS`) | 6 = | TS:90, 1935 (via 6013) | Selection refused if spread > 6 ticks | RISK | Unknown | Wider books selected |
| Legacy selection pair check (`T_PAIR_CAP` 0.98) | = | TS:1939 (via 6013) | Legacy opening pair must fit 0.98 at selection | ENG artefact | Rarely | n/a |
| Selection slots (`slots_used`, `full`, `T_MAX_TRACKED`) | 2 × markets = 8 | TS:74, 3299-3310, 5824 | Flat active markets ≥ `MAX_MARKETS`, or all active ≥ `MAX_TRACKED`: no selection. A market with an unpaired leg holds no selection slot (15 Nov incident) | RISK | See slot mismatch above | Scales automatically with `MAX_MARKETS` |
| Slate (`T_SLATE`; pregame lead 900–21600 s, cache 120 s) | live → both | TS:1160-1187, 5713-5725 | Live games first, then games starting 15 min–6 h ahead | STRAT | — | Longer lead = more pregame candidates |
| Ranking (`R_REWARD_RANK` 1, `R_REWARD_ONLY` 0, `R_RANK_NET` 0; `R_RANK_NEW_PER_ROUND` 3, `R_RANK_TTL_S` 300, `R_RANK_WATCH_MAX` 8) | = | TS:424-425, 1223, 1373-1383, 5906-5933 | Paying-programme markets first by headline pool rate, then tightest spread, nearest 0.50. The net ranking and its per-round limit are off | STRAT | Ranks by pool, not by achievable share | Ranking by estimated share needs the estimate decoupled from the fair-value test |
| Flat release (120 s hard-coded; `R_PAUSED_RELEASE_S`) | 120 → 600 | TS:1301, 5231-5251 | A flat market with nothing to quote for 120 s (600 s if only paused) is released. 24 Nov: pauses emptied all slots in 8 min | ENG | 14 released unfilled | Longer holds slots on dead markets |
| Reselect (`R_RESELECT`, `R_RESELECT_COOLDOWN_S`) | 0, 60 → 1, 60 | TS:1302-1304, 5977-5996 | Released-flat market eligible again after 60 s | ENG relief | Active | n/a |
| Open-EV gate, overpay budget, drift cap, max stranded, recovery halt | all off = | TS:1461, 318, 1040, 287, 1150 | Legacy gates, inactive | — | Off | n/a |

## Pauses and pulls

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Jump pause (`T_JUMP_TICKS` 2, `T_JUMP_MIN_PX` 0.02, `T_JUMP_PAUSE_S` 15) | = | TS:1559-1563, 2450-2460, 5104-5106 | A touch move ≥ 2 ticks and ≥ 2c in one drained batch: opening bids pulled if flat, no new exposure for 15 s; completions kept. Owner 14 Nov | RISK | 328 in 37 min on live markets (~9/min) | Largest single source of lost resting time in play; higher threshold = more time on book and more jump fills (the fills that lose) |
| Volatility pause (`T_VOL_PAUSE_USD`, `T_VOL_CALM_S` 60) | 0 → 0.03 | TS:1207-1209, 5112-5123; PH:89-100 | Mid range ≥ 3c in 30 s: no new exposure for 60 s, extended while it persists | RISK | Unknown count; overlaps jump pause | As above |
| Kickoff pull and hold (`T_KICKOFF_PULL_S` 300, `_RESUME_MIN_S` 120, `_MAX_HOLD_S` 1800) | = | TS:1190-1195, 4825-4879; PH:28-70 | From 5 min before start every order (completions too) is cancelled; resumes ≥ 2 min after start once listed live with a book; after 30 min unconfirmed, no new exposure. Owner 22 Nov | RISK | Unknown | Shorter pull = more pregame reward time, more kickoff gap risk |
| Lock, halt, latch, stale-account, account-risk, audit and loss-stop blocks | — | TS:5069-5089, 5153 | Any of these sets a block; every resting opening bid is cancelled on that pass | SAFE | Account-risk blips, see above | — |

## Request capacity

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Signed budget (`T_SIGNED_RPS`, `T_SIGNED_BURST` 4) | 1.0 = | RB:66-93, 246-257; TS:2073 | Token bucket for every signed call; the key is shared with other services (21 Nov 429s) | ENG | sent 0.6–0.82 rps | Venue limit for the key UNVERIFIED; raising risks 429 cool-downs |
| Critical class | hard-coded | RB:49-55, 138-145 | Cancels and `GET /v1/order/{id}` never wait and may drive the bucket to −burst | SAFE | ~70% of requests: one status GET per open order per pass (TS:5264-5269) | One open-orders read per pass removes most of it |
| Normal class (wait 3 s hard-coded) | 3.0 | RB:69, 155-168 | Placement, preview, account reads wait ≤ 3 s then raise `BudgetDeferred` | ENG | 26 sends deferred | Longer wait stalls the pass instead |
| Safety class (`T_SAFETY_WAIT_S` 20) | = | RB:98-129, 201-217 | Audit reads wait up to 20 s and normal traffic stands aside. 23 Nov: 1 h 45 min without an audit | SAFE | During every audit | Do not relax |
| Research class (`T_RESEARCH_RESERVE` 0.5) | = | RB:146-154 | Only from spare budget | ENG | — | n/a |
| 429 cool-down (`T_429_COOLDOWN_S` 15) | = | RB:170-175 | No normal/research sends for 15 s after any 429 | ENG | Not reported | n/a |
| Orphan sweep | hard-coded 30 s | TS:6803-6818, 6495-6559 | Venue-open orders not in the book are adopted and cancelled; the only place reservations are released | SAFE | — | Do not relax; also call the release from `service_pass` |

## Reward handling

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Programme read cache (`T_REWARD_REFRESH_S` 300; public, ≤ 2 rps) | = | TS:210, 2555-2579; RW:32 | Published pools, not payments | ENG | — | n/a |
| Reward estimate tied to the fair-value test | hard-coded | TS:4881-4909, 4952-4957, 4805-4809 | Estimate and qualifying-time counters computed only with `R_EDGE_GATE` on | ENG artefact | No reward measurement tonight | Decouple: measurement only, no risk |
| Share model needs `targetSize` | hard-coded | RW:96, 349-390, 396-439 | Share is 0 unless the side reaches the programme target size | venue rule | At 1 contract the modelled share is 0 | Larger size or joining the touch |
| Reward credit (`R_REWARD_CREDIT_TICKS` 1, `R_REWARD_MIN_DAY_USD` 1, `R_REWARD_PROJECT_H` 3) | = | TS:435, 475-476, 4911-4944 | Credit only offsets the inventory-risk step, only with the test on, only if the projection clears $1 | RISK | Inactive | n/a |
| Legacy reward mode (`T_REWARDS`, `T_REWARD_FIRST`) | off = | TS:207-256 | Inactive | — | Off | n/a |

## Session and window controls

| Constraint | Default → prod | Defined / enforced | What it does and why | Class | Bound live | If relaxed |
|---|---|---|---|---|---|---|
| Submission lock / window (`T_SUBMISSION_LOCK`; `T_WINDOW_MAX_S`; grace 0) | 21600 → 43200 | TS:754-777, 885-924, 4487, 3809-3835 | `1` locked; `window:` open only inside it, ≤ 12 h; own orders cancelled at the end. Owner 22 Nov | SAFE | By design | Do not relax |
| Enable switch (`T_ENABLED`=YES) | — | TS:6745-6749 | Otherwise cancel everything, sleep 30 s | SAFE | — | — |
| Shared halt (`L5_HALT_URL`; `T_SHARED_HALT_TTL_S` 15; max age) | 900 → 120 | TS:1495-1504, 3870-3897, 4505, 5080 | Halt: no order at all. Unreadable or attestation older than 120 s: no new exposure | SAFE | Not observed | Do not relax |
| Safety latch (`T_SAFETY_LATCH` 1; `T_MISMATCH_GRACE_S` 45; `T_LOOP_ERROR_LATCH_N` 10) | = | TS:478-499, 745-751, 3566-3644, 6836-6845 | Position mismatch confirmed after 45 s, unverifiable after 135 s, order above clip, cap breach, 10 failed passes: cancel all, refuse all, cleared only by owner acknowledgement | SAFE | Not observed | Do not relax |
| Hourly audit (`T_REWARD_CHECK_S` 3600; `T_AUDIT_RETRY_S` 60; `T_AUDIT_MAX_AGE_S`) | 0 → 4500 | TS:131, 1371-1372, 6134-6173, 6278-6391, 6791-6794 | Up to 200 activity pages on the loop thread at safety priority, order management interleaved (`T_AUDIT_INTERLEAVE` 1). No completed audit in 75 min (or since boot): no new exposure | SAFE | ~10 min each under traffic; heartbeat silent; reservations not released meanwhile | Move to its own thread, or run reservation release and heartbeat inside `service_pass` |
| Audit / late-fill latches (`T_AUDIT_LATCH` 0, `T_FILL_LATE_LATCH_S` 0) | off = | TS:1356-1360 | Off | SAFE (unused) | Off | n/a |
| Loop-stall reporting (`T_LOOP_STALL_S` 120; `T_ORDER_SERVICE_STALL_S` 0) | = | TS:525, 694, 6887+ | Registry reports `manages_own_resting: false` after 120 s without a beat so the watchdog cancels | SAFE | — | Do not relax |
| Shutdown cancel (`T_CANCEL_ATTEMPTS` 4, `T_CANCEL_BUDGET_S` 20) | = | TS:123-124 | Cancels own orders on SIGTERM | SAFE | — | — |
| Campaign marker mismatch | hard-coded | TS:4722-4735, 5086 | No new exposure if the campaign marker is later than the window start | SAFE | Not observed | Do not relax |

## 1. What limits throughput most, in order

1. **Quote placement: one tick behind, never joining, pulled when the market comes to it** (`R_DEPTH_TICKS` 1, reactive pull, `would_cross`). 99 orders produced 7 fills; a bid one tick back fills only when price trades through it, and every exposure-adding fill on 22–23 Nov was on a ≥ 2-tick jump. This also gives a modelled reward share of zero at 1 contract.
2. **Jump and volatility pauses in play.** 328 jump pauses in 37 minutes, each cancelling opening bids and blocking 15 s (vol pause 60 s). On point-by-point sports the bot is off the book for much of the time, and with the fair-value test on the same mechanism re-placed one completion 45 times.
3. **Account limit 5.00 plus its accounting artefacts.** 60 capacity refusals; peak 4.53. Reservations for sends that never left held capital for ~70 of 97 minutes; unreadable-figure blips every 15–30 minutes and the post-fill double count each cancel every opening bid.
4. **Market supply and slot accounting.** Four competitions (one blocked outright by the esports rule), band 0.30–0.70, one market per game; 14 markets selected then released without a fill; 24 decisions blocked `max_markets` by a carried position.
5. **Request budget.** 0.6–0.82 of 1.0 rps, ~70% of it per-order status reads; 26 deferred sends; a 10-minute audit each hour.
6. **Size 1, 60 s refill cooldown, +2c ceiling.** Not yet binding (0 same-side adds, peak unpaired 0.63) but they cap inventory accumulation directly once fills arrive.

## 2. Scaling sketch for 8, 16 and 32 markets with the existing controller

Request model: critical ≈ 6·R + 2.2·N, normal ≈ 6 + 2·N per minute. Fitted to tonight (51/min at 4 markets): R ≈ 1.7 open orders per market, N ≈ 0.3 placements per market per minute. Worst-case capital against the account limit ≈ $0.55 per quoted market per contract (the dearer side). The at-risk budget and lane cap count face value of both bids, ≈ $0.97 per market per contract. The 32-market figures are extrapolations.

| Markets | Requests/min today's code (rps) | With one open-orders read per pass (rps) | Account-limit capital at 1 / 2 / 5 contracts | Caps that bind, in order (1 contract) |
|---|---|---|---|---|
| 4 (now) | 51 (0.85) | 18 (0.30) | 2.20 / 4.40 / 11.00 | Account limit 5.00 (with the ledger figure already ~2) |
| 8 | ~98 (1.6) | 23 (0.38) | 4.40 / 8.80 / 22.00 | Account limit (needs ≥ ~7.5); `T_TOTAL_USD` 4.50 if fills accumulate |
| 16 | ~189 (3.2) | 33 (0.55) | 8.80 / 17.60 / 44.00 | Account limit; campaign unpaired 6 (binds at ~10 markets); at-risk 16 (binds at ~16); lane 20 marginal |
| 32 | ~373 (6.2) | ~53 (0.88) | 17.60 / 35.20 / 88.00 | All four, plus campaign spend 30 (≈ 0.97 per quoted market plus unsettled pairs) |

Settings that must change:

- **8 markets.** `T_MAX_MARKETS` 8 (`MAX_TRACKED` follows). Account limit ≥ ~7.5 at 1 contract. `T_SIGNED_RPS` ≥ 2 under today's code (venue allowance for the shared key UNVERIFIED), or unchanged with batched reads. More competitions in scope: four thin ones did not keep four slots filled. Fix the slot mismatch and the never-sent reservation hold first, or the extra slots are lost to them.
- **16 markets.** As above plus campaign unpaired ≥ ~10, `T_TOTAL_USD` raised with it, at-risk budget ≥ ~22, lane cap ≥ ~24, account limit ≥ ~12, campaign spend ~40. Today's code needs ~3.2 rps and a pass of ~27 serial status reads; batched reads are effectively required.
- **32 markets.** Campaign unpaired ≥ ~20, at-risk ≥ ~36, lane ≥ ~40, account limit ≥ ~21, spend ≥ ~60. Batched reads leave almost no headroom at 1.0 rps; use ≥ 1.5. The serial per-market pass and the on-loop audit become the bottleneck; the audit needs its own thread.
- **Size.** Multiply every capital figure by the contract count. At 2 contracts campaign unpaired 6 binds at ~5 markets and at-risk 16 at ~8; at 5 contracts they bind at 2 and 3 markets.

## 3. Do not remove

| Protection | Where | Why |
|---|---|---|
| Account drawdown limit, daily halt, cumulative ceiling, fail-closed read | AR:237-286, 87-167; TS:4331-4364 | The only account-wide capital protection shared by every lane; unreadable must refuse |
| Reservation before send and hold for truly unanswered sends | TS:4037-4138, 4516-4531 | Without it several bids spend the same capacity (24 Nov: 3.36 against 3.00); an accepted-and-filled unanswered order shows only in positions |
| Final gate | TS:4541-4606 | Last independent check of size, maker-only, ceilings and pair cost at the send |
| Safety latch and position reconciliation | TS:3519-3534, 3566-3644 | The bot stops itself on position mismatch, cap breach, oversize order or repeated loop failure; survives restarts; owner-only clear |
| Submission lock / window with automatic relock | TS:885-924, 3809-3835, 4487 | Trading only inside an approved window; cancels own orders at the end |
| Shared halt and attestation age | TS:3870-3897, 4505 | Watchdog halt overrides everything; a dead watchdog blocks new exposure within 120 s |
| Loop-stall reporting and ownership registry | TS:525-559, 6887-6960 | Lets the independent watchdog cancel a wedged bot's orders |
| Campaign loss stop and meter fail-closed | TS:6175-6225 | Realized-loss stop, latched; missing or unreconciled fills block rather than drop out of the total |
| Lane daily loss stop | TS:2934-2949 | Owner's stop for this lane |
| Account freshness and stale-completion check | TS:3440-3446, 4366-4409, 4449-4465 | No new exposure, and no completion, against unverified inventory |
| Critical request class | RB:49-55, 138-145 | Cancels and fill truth are never delayed or refused |
| Cancel confirmation and pending-cancel handling | TS:2269-2284, 2298-2315 | A cancel that raced a fill is recorded (17 Nov: $3.45 missed) |
| Orphan sweep | TS:6495-6559 | An own order missing from the book is adopted and cancelled (15 Nov watchdog latch) |
| Audit, audit-age block and safety request class | TS:6134-6173, 6278-6391; RB:98-129 | Venue-truth reconciliation; 23 Nov ran 1 h 45 min without one |
| Post-only stale refuse and maker-only | MX:381-385; TS:4556 | Prevents an unintended taker fill |
| Foreign-exposure skip, clip check, campaign-marker check | TS:6001, 3593, 4722-4735 | Ownership separation; oversize detection; no trading outside campaign accounting |
