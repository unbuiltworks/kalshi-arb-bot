# L5 24/7 operations: the durable chain (OWNER 5571884826)

Requirement: the L5 system runs without a Claude or ChatGPT session open.
This is the audited chain as deployed on 2026-09-07, stage by stage, with
the mechanism that keeps each stage alive, its heartbeat, the failure seen
in production, and what the recorder fix (`e48ae47b` + `af4a2e74`) changed.
Nothing here loosens a gate.

| # | stage | service (Railway) | durable mechanism | heartbeat / evidence | failure mode seen | status |
|---|---|---|---|---|---|---|
| 1 | full live-universe discovery | `pmus-ws-evidence-recorder` | `discover_universe()` every reconnect (`PMUS_WS_DISCOVERY_S`, 300 s): all-league events + `/v1/events?live=true` + incentives; `restartPolicyType: ALWAYS` | `PMUS_WS_STATUS.discovery` (`v1_live_events`, `v1_live_markets_added`, `universe`) every status tick | none: 85 live markets from 41 live events found every cycle on 09-07 | OK |
| 2 | fresh recorder allocation (128 seats) | same | HOT/WARM/COLD planner (`src/pmus_priority_queue.py`) each discovery cycle | `PMUS_WS_STATUS.discovery` (`hot/warm/cold_sampled`, now `stale_evicted`, `fresh_live_subscribed`) | **defect**: lifetime `book_rate` + no eviction pinned 09-06 resolved markets in HOT for 17.5 h; live slate starved | FIXED `af4a2e74`: stale (silent > 900 s) markets leave every tier unless discovery lists the event live; recent-window `book_rate` |
| 3 | served candidate rows | same | status document (`/status`, `spring_watch` <= 10 rows, `top_markets`, `eligible_markets`) rewritten every status tick | `PMUS_WS_STATUS.fresh_markets / stale_markets / pool_alarm` (new) | pool was 75-78 resolved rows + thin-flow | FIXED: rows built from fresh markets only; `PMUS_WS_ALARM pool_stale_or_empty` when live events exist but no fresh candidate is served for `PMUS_WS_POOL_ALARM_S` (600 s), with the reason |
| 4 | L5 ranking / selection | `pmus-spring-l5-probe` | 20 s selection cycle over `spring_watch`: strict-evidence gate (fail closed), frozen model, `pair_score`, yield ordering | `PMUS_L5_CAMPAIGN funnel` every cycle (`cycles`, `admitted`, `refusals` by reason, `not_started.no_candidate`); `selection` rows carry every refused slug + reason | correctly refused every stale row (`strict_evidence_market_closed`) -- the gate did its job; the idle was upstream | OK (gate untouched) |
| 5 | probe / execution | same | one market at a time, maker-only, post-only, 30 s rest deadline, `$2.50` unmatched cap, `$7.50` spend cap, daily stop `-$15`, deadman cancel on SIGTERM; `restartPolicyType: NEVER` (owner rule) with durable `l5_campaign_state.json` restore on redeploy | `session_end` rows (decisions, orders, fills), `campaign_state_restored` after a redeploy | 08:21Z `CAMPAIGN_STOP:HALT` on a venue 429 during a read-only GET with a leg held -> bounded in `43777804` (`session_read_halt`, backoff) | OK |
| 6 | settlement / reconciliation | same | settlement auditor every resume (venue POSITION_RESOLUTION or public resolution + flat account), incentive audit hourly (balance, credited prints), strict SUBMITTED -> FILLED -> SETTLED -> REALIZED | `settlement` rows, `incentive_audit` rows (balance reconciled to the cent: 111.1422 at 12:02Z, 13:02Z, 14:02Z) | none | OK |
| 7 | next-market rotation | same | after settlement the loop returns to selection (`CAMPAIGN_HOLD:POSITION_HELD` until the venue settles; then `CAMPAIGN_NO_CANDIDATE` scans) | `funnel.not_started.no_candidate`, `selection_skipped_position_held` | 0.36 h flat-account wait per market (design: wait for settlement even when flat) and 2.5 h idle on the stale pool | pool side FIXED; the flat-account wait is a rung parameter, reported in `L5_NEXT_RUNG.md`, not changed |

## What runs without a session open
- Stages 1-3 restart automatically (`ALWAYS`) and re-seed from discovery on every start; the status document is regenerated within one status tick.
- Stages 4-7 are one long-running process with durable state; it survives a redeploy (state restore) and a venue read outage (bounded wait), and it stops only on the daily loss stop, the rung's market count, or SIGTERM (deadman cancel). By the owner's rule it is **not** auto-restarted (`NEVER`): a crash requires a human redeploy. That is the one remaining point in the chain that needs a person, and it is a safety choice, not an omission.
- Check-ins from this session are a reporting layer, not a control layer: nothing in the trade path waits on them.

## Alarms and staleness (added / existing)
- NEW `PMUS_WS_ALARM {"kind":"pool_stale_or_empty", live_events, fresh_subscribed, spring_watch_rows, pool_empty_for_s, reason}` from the recorder when the venue lists live events but nothing fresh is served for >= 600 s. Reasons: `no fresh authenticated market in the subscription set` / `no fresh two-sided market inside the 0.35-0.65 spring band`.
- NEW status fields `fresh_markets`, `stale_markets`, `pool_alarm`, planner `stale_evicted`, `stale_but_discovered_live`, `fresh_live_subscribed`.
- Existing: recorder heartbeat file every 200 messages / 15 s idle; probe `funnel` every cycle (absence = process down); `session_read_halt` on venue read failures; settlement and incentive audits.
- Not yet: a probe-side alarm ("no candidate for N minutes while the recorder reports live events") -- it belongs in `src/pmus_l5_continuous_session.py`, a probe watchPattern, so it is staged for the first probe redeploy after the R4 cohort, not pushed mid-cohort.

## Funding (venue evidence, 14:02Z incentive audit)
| item | value |
|---|---|
| current balance = buying power (no open orders, no reservations) | $111.1422 |
| maximum capital simultaneously required by R4 settings | one market at a time x `$7.50` spend cap = **$7.50** at risk; unmatched exposure capped at `$2.50` |
| R4 preflight requirement | `$22.50` deployed (3 markets x `$7.50`), met 4.9x over |
| sufficient for continuous one-market-at-a-time R4 running | **yes**: 14.8 spend caps of headroom; the daily stop (`-$15`) binds long before the balance does |
| bankroll for the next proposed size rung (`$5.00` actions, cap unchanged) | at most `$45` deployed if the spend cap scales with the action (3 actions x `$5.00` x 3 markets), still 2.5x covered by the current balance |
| funding request | **none**. Capital is added only after measured positive realized expectancy (gate G1 in `L5_PROFITABILITY.md`, not met) and executable capacity justify it |
