# L5 sports clock study: is there a defensible analogue of `seconds_to_settlement`?

Owner question (#167 comment 5577894093): most defensible sports analogue of L5's crypto seconds_to_settlement / window-phase state; and whether the current mapping materially explains R5F residual losses or missed completions

Sample: 635 decisions over 35 markets (314 in-sample / 321 out-of-sample), 90 fills, 50 residual-creating fills. SMALL. Every rate below is a handful of markets; the cluster bootstrap CIs are the honest read, not the point estimates.

Re-run: `python3 -m src.l5_sports_clock_study --logs DIR [DIR ...] [--probe-gateway|--offline]` over the input set `55516865b37cde2b` (21 probe log dumps; every file name and hash is in the JSON). Log directories are shared and can grow -- the manifest is what pins a run.

Frozen mapping `L5_SPORTS_CLOCK_v1` sha `df3408c843ee7c25`. Causal audit: 5080 clock-field re-derivations from the causal prefix, 0 mismatches.

## 0. Verdicts

**q1_is_the_time_dimension_degenerate** -> YES, completely
  - evidence: 635 of 635 decisions (100.0%) sit in the single bucket 'tte>10m'; the tte dimension takes one value across the whole live sample, in-sample and out-of-sample alike.
  - sharper: the shared label is misleading in the other direction too: in L5's canonical tape 'tte>10m' is bounded at 896 s and comes entirely from 15-minute windows, so it means 'the opening third of a 15-minute crypto window'. Our sports decisions sit hours from resolution and key into it.

**q2_does_the_mapping_explain_the_residual_losses** -> NO
  - reason: a feature that takes one value cannot explain any variation in outcome. The tte bucket is identical on the markets that paired to zero residual and on the markets that stranded residual, so it accounts for 0 of the loss.
  - where_the_loss_actually_sits: {'reconciling_markets': ['aec-setkamecz-tomvoj-ticjir-2026-09-07', 'aec-setkamemd-scovla-otgand-2026-09-07', 'tsc-mlb-laa-bos-2026-09-07-7pt5'], 'reconciling_totals_usd': {'faithful_l5_behavior': 0.0, 'execution_queue_failure': -3.1844, 'market_selection_transfer': -1.924, 'remaining_shell_distortion': -3.2609}, 'non_reconciling_markets': [{'slug': 'aec-setkameua-malden-stoole-2026-09-08', 'residual_contribution_usd': '-1.5079', 'partition_sum_usd': '-7.4951'}], 'note': "a non-reconciling market is EXCLUDED from the totals above. src/l5_r5f_sequence_ledger's FIFO residual walk was built for the day-1 cohort; on a market that opens, completes and re-opens several times it attributes more dollars than the market's residual contribution. Reported as a finding, not patched here (the ledger is another session's file)."}
  - realized_usd_venue_sum: -9.2384
  - corollary: the degeneracy is a COHERENCE defect -- we key a crypto model on a state it never trained on -- not, on this evidence, the cause of the losses.

**q3_is_the_degeneracy_costing_action_fidelity** -> MUCH LESS THAN IT LOOKS
  - evidence: on L5's OWN crypto tape the tte dimension carries 0.17% of the entropy of his next relation and buys 0.0018 bits held-out over price+inventory; the inventory dimension carries ~7%. Losing the time dimension loses very little of what the key does.
  - caveat: this measures the RELATION distribution the key is used for. It says nothing about the economics of holding residual, which is a different question and is where the sports/crypto difference actually bites.

**q4_mini_window** -> EPISODES EXIST; EPISODE PHASE DOES NOT PREDICT OUR OUTCOMES ON THIS SAMPLE
  - episodes: 170
  - fill_rate_effect: {'point': -0.0099, 'ci95': [-0.1761, 0.1081], 'B_defined': 4000, 'clusters': 35, 'labels': 'cluster bootstrap over markets; percentile CI'}
  - reading: a local price/flow episode structure is clearly present and is not degenerate, but within-episode phase separates neither fills nor completions here.

**q5_best_analogue** -> the venue-served EVENT-PROGRESS clock (period / score / ended), NOT a wall clock and NOT the local-regime clock
  - why: it is the only one of the three that is (a) served by the venue for most leagues we trade, (b) monotone toward resolution the way seconds_to_settlement is, and (c) directionally associated with completion here. The market-lifecycle clock has no served endpoint (endDate is a contract expiry) and the local-regime clock is, on L5's own tape, a WORSE time dimension than tte.
  - status: NOT ESTABLISHED -- see the sample and the robustness check below
  - sample_it_rests_on: {'ev_early(<0.34)': {'markets_fully_completed': 2, 'markets': 2}, 'ev_late(>=0.67)': {'markets_fully_completed': 2, 'markets': 4}, 'ev_mid(0.34-0.67)': {'markets_fully_completed': 5, 'markets': 7}}
  - robustness_check_FAILS: {'what': "the C1 gradient survives only under the STRICT outcome 'the residual later reached zero'. Under the softer outcome 'the residual was later reduced at all' the gradient disappears.", 'strict_completion_by_event_phase': {'ev_early(<0.34)': {'hits': 7, 'n': 7, 'rate': 1.0}, 'ev_late(>=0.67)': {'hits': 7, 'n': 18, 'rate': 0.3889}, 'ev_mid(0.34-0.67)': {'hits': 19, 'n': 24, 'rate': 0.7917}, 'ev_unknown': {'hits': 1, 'n': 1, 'rate': 1.0}}, 'residual_reduced_by_event_phase': {'ev_early(<0.34)': {'hits': 7, 'n': 7, 'rate': 1.0}, 'ev_late(>=0.67)': {'hits': 15, 'n': 18, 'rate': 0.8333}, 'ev_mid(0.34-0.67)': {'hits': 20, 'n': 24, 'rate': 0.8333}, 'ev_unknown': {'hits': 1, 'n': 1, 'rate': 1.0}}, 'meaning': "late-game markets DO keep pairing, they just do not finish. That is consistent with the crypto intuition, and equally consistent with 'we ran out of runway / cap on those markets'. On this sample the two cannot be separated."}
  - recommendation: do NOT substitute it into the live key. Record it in shadow, per decision, and re-run this study against the OOS gate.

### Out-of-sample gate `L5_SPORTS_CLOCK_v1_OOS_GATE`

Applies to: any live use of the event-progress clock, in any form. the in-sample effect is null and the out-of-sample effect rests on 5 markets; a gate is the only thing standing between a 13-market pattern and a live behaviour change

- **sample** -- >= 60 residual-creating fills spread over >= 40 DISTINCT markets and >= 4 leagues, collected AFTER the mapping hash below was written, in shadow
- **effect** -- completion(ev_early|ev_mid) - completion(ev_late), cluster-bootstrapped over markets, has a 95% CI whose LOWER bound is > 0.10 (not merely excludes zero)
- **coherence** -- the substituted key must not lose action fidelity: agreement with the frozen v1/v3 relation draw, measured on the same rows, must not fall by more than 0.5 pp, and the held-out log-loss of price+event_phase+inventory must be <= that of price+tte+inventory on the SAME rows
- **coverage** -- a venue-served game position on >= 90% of decisions with an observation age p90 <= 120 s (i.e. the clock is polled per decision, not carried from market entry)
- **no_pnl_only_promotion** -- a P&L improvement alone does NOT satisfy this gate; the coherence criterion must pass on its own

If it fails: record the shadow numbers and change nothing.

### Smallest shadow-only implementation `L5_SPORTS_CLOCK_v1_SHADOW`

attach the venue-served event phase and the local-regime age to every decision row as ANNOTATION, and change nothing else

Why only this: the live probe already fetches the venue's live-event feed every cycle (src/pmus_event_progress.table, wired into src/pmus_l5_continuous_session for the ranker) and already writes game_period / game_proximity onto SELECTION rows. The only thing missing is that the annotation is not written per DECISION, so the clock goes stale the moment a market is held -- observation age p90 661 s in this cohort. Nothing new has to be fetched.

- on each decision row, alongside the existing `state`, add `sports_clock`: {event_phase, game_period, game_proximity, proximity_basis, observation_age_s, regime_phase, regime_age_s, trailing_drift_cents, mapping_sha}
- no change to `state`, to the lookup key, to relation selection, to sizing, to pricing, to admission, or to any cap or gate
- the annotation must be written even when it is ev_unknown, so the coverage number in the gate is measurable

Explicitly NOT changed: the frozen action model (sha bbb55bf90638d2bd) and lookup v3; the fidelity gate; the safety shell; the approvals file; market admission / selection ranking

Coherence argument (not P&L): the case for recording it is that the live key currently asserts a state ('tte>10m') that in L5's training data means 'the opening third of a 15-minute crypto window'. Recording the event phase makes the translation auditable: we can finally SAY which sports states we are mapping onto which crypto states, and show the mapping is not outcome-derived. That is worth doing even if the P&L effect is zero, and it is the only reason offered here.

Evaluation: re-run this study with --logs pointed at the shadow logs; the OOS gate above is the pass condition. Until it passes, the annotation is measurement only.

## 1. Is the time dimension degenerate on sports?

| tte bucket | R5F decisions | R5F share | L5 canonical share |
|---|---:|---:|---:|
| tte<30s | 0 | 0.0% | 1.2% |
| tte30-120s | 0 | 0.0% | 15.0% |
| tte2-5m | 0 | 0.0% | 25.3% |
| tte5-10m | 0 | 0.0% | 32.5% |
| tte>10m | 635 | 100.0% | 25.9% |

Share of L5's canonical decisions in buckets we never occupy: **74.0%**.

src/pmus_l5_action_shadow.SPORTS_TTE_DEFAULT_S = 1e9 -- an in-game sports market has no settlement clock on the tape, so TTE collapses to the coarsest bucket. The venue's only settlement-like field, market.endDate, is a fixed gameStartTime + 14 d CONTRACT EXPIRY (see src/pmus_event_progress docstring and market_detail below), so wiring it in would not un-degenerate the key.

L5's canonical tape holds only {'15': 556, '5': 1660}-minute windows (2216 windows, 71984 prints). L5's canonical tape contains ONLY 5- and 15-minute windows, so its 'tte>10m' bucket is bounded above by the 15-minute window length -- it means 'the opening third of a 15-minute window', not 'more than ten minutes from resolution'.

| tte bucket | L5 prints | seconds_to_settlement range |
|---|---:|---|
| tte2-5m | 19070 | 120-299 s |
| tte30-120s | 10723 | 30-119 s |
| tte5-10m | 22706 | 300-599 s |
| tte<30s | 856 | -5-29 s |
| tte>10m | 18629 | 600-896 s |

The inventory dimension is degenerate in the OTHER direction:

| inventory bucket | R5F share | L5 canonical share |
|---|---:|---:|
| flat | 46.9% | 0.9% |
| imb1-10 | 9.0% | 5.1% |
| imb10-50 | 42.4% | 20.0% |
| imb>50 | 1.7% | 74.0% |

## 2. Does a clock carry information in L5's OWN domain?

N = 69768 L5 decisions, H(relation) = 1.0668 bits. held-out log-loss is the honest column: lower is better. A time dimension that helps must beat 'price+inventory (time dropped)'.

| feature | states | IG (in-sample, bits) | held-out log-loss (bits) |
|---|---:|---:|---:|
| price | 7 | 0.0047 | 1.0631 |
| tte | 5 | 0.0018 | 1.066 |
| inventory | 4 | 0.0762 | 0.9915 |
| regime_age | 3 | 0.0002 | 1.0675 |
| price+tte+inventory (LIVE KEY) | 136 | 0.0856 | 0.9853 |
| price+inventory (time dropped) | 28 | 0.0812 | 0.9871 |
| price+regime_age+inventory (tte->regime) | 79 | 0.0817 | 0.9883 |
| price+tte+regime_age+inventory | 297 | 0.0877 | 0.9876 |

## 3. What sport-native state does the venue actually serve?

Measured 2026-09-08T02:37:56.224149+00:00 on https://gateway.polymarket.us (none (keyless public gateway, GET)).

| league | events | live at probe | sample period / score | per-sport substate | populated fields |
|---|---:|---:|---|---|---|
| arg2 | 16 | 0 | - | - | not_observed |
| atp | 50 | 1 | `S3` / `6-2, 6-2, 0-0` | tennisState | createdAt, ended, eventId, gameId, id, live, period, score, tennisState.round, tennisState.servingTeamId, tennisState.tournamentName, type, updatedAt |
| cfb | 50 | 1 | `Q2` / `17-10` | footballState | createdAt, elapsed, ended, eventId, footballState.down, footballState.driveState, footballState.timeouts, footballState.yard, id, live, period, periodScores, score, sportradarGameId, type, updatedAt |
| cs2 | 50 | 0 | - | - | not_observed |
| dota2 | 13 | 1 | `Game 1` / `0-0` | - | createdAt, ended, eventId, id, live, period, score, sportradarGameId, type, updatedAt |
| ekst | 9 | 0 | - | - | not_observed |
| epl | 10 | 0 | - | - | not_observed |
| itfm | 0 | 0 | - | - | not_observed |
| itfw | 0 | 0 | - | - | not_observed |
| lol | 15 | 0 | - | - | not_observed |
| mlb | 38 | 3 | `Top 9th` / `2-3` | baseballState | baseballState.balls, baseballState.inningHalf, baseballState.onFirst, baseballState.onSecond, baseballState.onThird, baseballState.outs, baseballState.strikes, createdAt, ended, eventId, gameId, id, live, mainSpreadLine, mainTotalLine, marketGroupStats, period, score, sportradarGameId, type, updatedAt |
| nba | 0 | 0 | - | - | not_observed |
| nfl | 32 | 0 | - | - | not_observed |
| nhl | 0 | 0 | - | - | not_observed |
| sea | 10 | 0 | - | - | not_observed |
| setkamecz | 48 | 0 | - | - | not_observed |
| setkamemd | 35 | 1 | `S2` / `11-9, 4-2` | - | createdAt, ended, eventId, id, live, period, score, sportradarGameId, type, updatedAt |
| setkameua | 50 | 2 | `S2` / `12-10, 5-5` | - | createdAt, ended, eventId, id, live, period, score, sportradarGameId, type, updatedAt |
| valorant | 10 | 0 | - | - | not_observed |
| wta | 40 | 1 | `S2` / `7-5, 1-0` | tennisState | createdAt, ended, eventId, gameId, id, live, period, score, tennisState.round, tennisState.servingTeamId, tennisState.tournamentName, type, updatedAt |
| wtt | 50 | 0 | - | - | not_observed |

Venue-DECLARED per-sport substates: baseballState, footballState, soccerState, tennisState, cricketState, ufcState. Declared but NOT observed live in this probe window: cricketState, soccerState, ufcState. the venue's published OpenAPI for /v1/events and /v2/sports; DECLARED is not the same as served -- a league with no live event at probe time is 'not_observed', which is missing evidence, not evidence of absence

Market-detail probe (the settlement-clock question):

| market | gameStartTime | endDate | endDate - gameStart (days) |
|---|---|---|---:|
| aec-atp-artgea-botzan-2026-09-06 | 2026-09-07T20:45:00Z | 2026-09-07T22:15:46Z | 0.06 |
| aec-atp-fracer-aleblo-2026-09-06 | 2026-09-07T18:10:00Z | 2026-09-07T17:41:26Z | -0.02 |
| aec-atp-karkha-leatie-2026-09-07 | 2026-09-07T21:45:00Z | 2026-09-07T21:52:56Z | 0.01 |
| aec-cfb-smu-flst-2026-09-07 | 2026-09-07T23:30:00Z | 2026-09-21T23:30:00Z | 14.0 |
| aec-cs2-ntr-cs-2026-09-07 | 2026-09-07T17:30:00Z | 2026-09-07T16:55:09Z | -0.02 |
| aec-mlb-az-kc-2026-09-07 | 2026-09-07T18:10:00Z | 2026-09-07T17:13:53Z | -0.04 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 2026-09-07T19:45:00Z | 2026-09-07T16:02:28Z | -0.15 |
| aec-setkamemd-ghifio-butdum-2026-09-08 | 2026-09-08T02:00:00Z | 2026-09-07T22:16:43Z | -0.16 |

## 3b. What each clock covers on our own decisions

- **C1 event progress**: a venue-served game position on 598 of 635 decisions (94.2%); observation age p50 50.2s, p90 661.5s, max 1682.4s. the venue serves the CURRENT state of an event and cannot be replayed; the only retrospective record for R5F is the probe's own selection rows, which are written at market entry, so the attached game position goes stale while a market is held. Effective independent units are MARKETS, not decisions: each market contributes essentially one event phase.
- period tokens seen: {'2H': 18, "45'": 50, "56'": 9, "60'": 9, "77'": 6, 'Bot 7th': 5, 'Bot 8th': 27, 'Bot 9th': 12, 'HT': 6, 'LIVE': 6, 'Map 3': 6, 'Mid 4th': 6, 'Mid 7th': 1, 'None': 7, 'Q1': 147, 'Q2': 12, 'S1': 4, 'S2': 107, 'S3': 100, 'S4': 11, 'S5': 86}
- **C2 market lifecycle**: NOT SERVED by the venue -- market.endDate on a live sports market is a fixed gameStartTime + 14 d contract expiry; on a closed market it is overwritten and can precede gameStartTime. Neither is a resolution time.
- **C3 local regime**: 617 decisions carry a touch; 170 episodes; phases {'ep15-60s': 177, 'ep<15s': 367, 'ep>=60s': 91}

## 4. The mini-window test

Local-regime episodes are segmented on our own decision tape: a new episode opens on a yes-equivalent touch move of >= 2.0c or a wall-clock gap of >= 300.0s. Sensitivity:

| k | episodes | ep<15s | ep15-60s | ep>=60s |
|---|---:|---:|---:|---:|
| k=1.0c | 228 | 69.3% | 24.9% | 5.8% |
| k=2.0c | 170 | 57.8% | 27.9% | 14.3% |
| k=3.0c | 151 | 52.4% | 28.8% | 18.7% |
| k=5.0c | 109 | 42.4% | 28.3% | 29.3% |

### ALL

- decisions 635, order-placing 561, residual-creating fills 50
- fill rate by regime phase: ep15-60s 22/154 (0.1429); ep<15s 54/327 (0.1651); ep>=60s 14/80 (0.175)
- completion by drift bucket: drift2-5c 4/7 (0.5714); drift<2c 6/7 (0.8571); drift>=5c 13/23 (0.5652); drift_unknown 11/13 (0.8462)
- STRICT completion by event phase: ev_early(<0.34) 7/7; ev_late(>=0.67) 7/18; ev_mid(0.34-0.67) 19/24; ev_unknown 1/1
- SOFT 'residual later reduced' by event phase: ev_early(<0.34) 7/7; ev_late(>=0.67) 15/18; ev_mid(0.34-0.67) 20/24; ev_unknown 1/1

- effect `fill_rate(ep<15s) - fill_rate(ep>=60s)` = -0.0099 95% CI [-0.1761, 0.1081] (clusters 35, B 4000)
- effect `completion(quiet regime) - completion(drift>=5c)` = 0.2848 95% CI [-0.1765, 0.5622] (clusters 13, B 4000)
- effect `completion(ev_early|mid) - completion(ev_late)` = 0.4498 95% CI [-0.266, 0.9231] (clusters 13, B 3911)

### IN-SAMPLE (exploratory)

- decisions 314, order-placing 251, residual-creating fills 24
- fill rate by regime phase: ep15-60s 13/72 (0.1806); ep<15s 25/142 (0.1761); ep>=60s 7/37 (0.1892)
- completion by drift bucket: drift2-5c 0/2 (0.0); drift<2c 3/3 (1.0); drift>=5c 7/10 (0.7); drift_unknown 7/9 (0.7778)
- STRICT completion by event phase: ev_late(>=0.67) 3/5; ev_mid(0.34-0.67) 13/18; ev_unknown 1/1
- SOFT 'residual later reduced' by event phase: ev_late(>=0.67) 3/5; ev_mid(0.34-0.67) 14/18; ev_unknown 1/1

- effect `fill_rate(ep<15s) - fill_rate(ep>=60s)` = -0.0131 95% CI [-0.1966, 0.2258] (clusters 16, B 3991)
- effect `completion(quiet regime) - completion(drift>=5c)` = 0.1333 95% CI [-0.375, 0.5923] (clusters 8, B 3965)
- effect `completion(ev_early|mid) - completion(ev_late)` = 0.1222 95% CI [-0.5909, 0.9474] (clusters 8, B 3030)

### OUT-OF-SAMPLE (validation)

- decisions 321, order-placing 310, residual-creating fills 26
- fill rate by regime phase: ep15-60s 9/82 (0.1098); ep<15s 29/185 (0.1568); ep>=60s 7/43 (0.1628)
- completion by drift bucket: drift2-5c 4/5 (0.8); drift<2c 3/4 (0.75); drift>=5c 6/13 (0.4615); drift_unknown 4/4 (1.0)
- STRICT completion by event phase: ev_early(<0.34) 7/7; ev_late(>=0.67) 4/13; ev_mid(0.34-0.67) 6/6
- SOFT 'residual later reduced' by event phase: ev_early(<0.34) 7/7; ev_late(>=0.67) 12/13; ev_mid(0.34-0.67) 6/6

- effect `fill_rate(ep<15s) - fill_rate(ep>=60s)` = -0.006 95% CI [-0.5215, 0.1268] (clusters 19, B 3863)
- effect `completion(quiet regime) - completion(drift>=5c)` = 0.4135 95% CI [0.0, 0.6833] (clusters 5, B 3938)
- effect `completion(ev_early|mid) - completion(ev_late)` = 0.6923 95% CI [0.0, 1.0] (clusters 5, B 3412)

### The C1 effect at its true unit (markets, not fills)

| event phase at the residual fills | markets fully completed | markets |
|---|---:|---:|
| ev_early(<0.34) | 2 | 2 |
| ev_late(>=0.67) | 2 | 4 |
| ev_mid(0.34-0.67) | 5 | 7 |

| market | cohort | event phase | periods | residual fills | fully completed | later reduced |
|---|---|---|---|---:|---:|---:|
| asc-cfb-smu-flst-2026-09-07-neg-10pt5 | oos | ev_early(<0.34) | Q1 | 1 | 1 | 1 |
| asc-cfb-smu-flst-2026-09-07-neg-13pt5 | oos | ev_early(<0.34) | Q1 | 6 | 6 | 6 |
| aec-atp-karkha-leatie-2026-09-07 | oos | ev_late(>=0.67) | S5 | 4 | 4 | 4 |
| aec-mlb-az-kc-2026-09-07 | in_sample | ev_late(>=0.67) | Bot 9th | 3 | 3 | 3 |
| aec-setkameua-malden-stoole-2026-09-08 | oos | ev_late(>=0.67) | S5 | 9 | 0 | 8 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | in_sample | ev_late(>=0.67) | Bot 8th | 2 | 0 | 0 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | in_sample | ev_mid(0.34-0.67) | S3 | 4 | 0 | 1 |
| aec-setkamemd-ghifio-butdum-2026-09-08 | oos | ev_mid(0.34-0.67) | S2 | 6 | 6 | 6 |
| aec-setkamemd-scovla-otgand-2026-09-07 | in_sample | ev_mid(0.34-0.67) | S2 | 1 | 0 | 0 |
| aec-setkameua-malden-pytiva-2026-09-07 | in_sample | ev_mid(0.34-0.67) | S3 | 3 | 3 | 3 |
| aec-setkameua-naiole-khaser-2026-09-07 | in_sample | ev_mid(0.34-0.67) | S2 | 4 | 4 | 4 |
| atc-ekst-psz-plo-2026-09-07-draw | in_sample | ev_mid(0.34-0.67) | 45' | 5 | 5 | 5 |
| aec-setkamemd-salvea-dupale-2026-09-07 | in_sample | ev_mid(0.34-0.67), ev_unknown | None, S2 | 2 | 2 | 2 |

this is the sample the C1 effect actually rests on: a handful of MARKETS, not tens of fills

## 5. Where the money actually went

Ledger loss partition (venue realized sum -9.2384): {'reconciling_markets': ['aec-setkamecz-tomvoj-ticjir-2026-09-07', 'aec-setkamemd-scovla-otgand-2026-09-07', 'tsc-mlb-laa-bos-2026-09-07-7pt5'], 'reconciling_totals_usd': {'faithful_l5_behavior': 0.0, 'execution_queue_failure': -3.1844, 'market_selection_transfer': -1.924, 'remaining_shell_distortion': -3.2609}, 'non_reconciling_markets': [{'slug': 'aec-setkameua-malden-stoole-2026-09-08', 'residual_contribution_usd': '-1.5079', 'partition_sum_usd': '-7.4951'}], 'note': "a non-reconciling market is EXCLUDED from the totals above. src/l5_r5f_sequence_ledger's FIFO residual walk was built for the day-1 cohort; on a market that opens, completes and re-opens several times it attributes more dollars than the market's residual contribution. Reported as a finding, not patched here (the ledger is another session's file)."}
Losing markets: ['aec-setkamecz-tomvoj-ticjir-2026-09-07', 'aec-setkamemd-scovla-otgand-2026-09-07', 'aec-setkameua-malden-stoole-2026-09-08', 'tsc-mlb-laa-bos-2026-09-07-7pt5']

| market | dec | fills | entry | last | max excursion (c) | net (c) | pairs | pair cost | residual | realized |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 44 | 4 | 0.44 | 0.04 | 41.0 | -40.0 | 4.11 | 1.11070 | 10.35 | -3.8778 |
| aec-setkamemd-ghifio-butdum-2026-09-08 | 33 | 7 | 0.38 | 0.79 | 41.0 | 41.0 | None | None | None | None |
| aec-setkameua-naiole-khaser-2026-09-07 | 28 | 5 | 0.53 | 0.37 | 39.0 | -16.0 | 9.58 | 0.99556 | 0.00 | None |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 28 | 2 | 0.38 | 0.06 | 35.0 | -32.0 | 0 | None | 9.62 | -3.1213 |
| aec-atp-karkha-leatie-2026-09-07 | 27 | 6 | 0.46 | 0.58 | 29.0 | 12.0 | 11.16 | 0.96805 | 0.00 | None |
| aec-setkamemd-scovla-otgand-2026-09-07 | 25 | 1 | 0.36 | 0.12 | 28.0 | -24.0 | 0 | None | 5.07 | -1.8252 |
| atc-ekst-psz-plo-2026-09-07-draw | 68 | 6 | 0.39 | 0.51 | 27.0 | 12.0 | 13.64 | 0.88291 | 0.00 | None |
| asc-cfb-smu-flst-2026-09-07-neg-13pt5 | 74 | 7 | 0.43 | 0.2 | 24.0 | -23.0 | None | None | None | None |
| asc-cfb-smu-flst-2026-09-07-neg-3pt5 | 28 | 7 | 0.44 | 0.68 | 24.0 | 24.0 | None | None | None | None |
| aec-setkameua-malden-stoole-2026-09-08 | 43 | 9 | 0.46 | 0.51 | 21.0 | 5.0 | 18.90 | 0.94212 | 4.76 | -0.4141 |
| aec-wta-anatik-darsem-2026-09-07 | 14 | 4 | 0.64 | 0.46 | 18.0 | -18.0 | 8.7 | 0.95241 | 0.0 | None |
| asc-cfb-smu-flst-2026-09-07-neg-6pt5 | 12 | 1 | 0.4 | 0.57 | 17.0 | 17.0 | None | None | None | None |
| aec-mlb-az-kc-2026-09-07 | 12 | 6 | 0.36 | 0.5 | 14.0 | 14.0 | 15.84 | 0.97080 | 0.00 | None |
| aec-atp-artgea-botzan-2026-09-06 | 12 | 6 | 0.61 | 0.52 | 9.0 | -9.0 | None | None | None | None |
| tsc-mlb-stl-sf-2026-09-07-6pt5 | 6 | 0 | 0.51 | 0.45 | 6.0 | -6.0 | None | None | None | None |
| aec-setkamemd-turmax-dupale-2026-09-07 | 12 | 0 | 0.58 | 0.56 | 5.0 | -2.0 | None | None | None | None |
| aec-setkameua-malden-pytiva-2026-09-07 | 24 | 6 | 0.41 | 0.44 | 5.0 | 3.0 | 16.7 | 0.98335 | 0.0 | None |
| aec-atp-fracer-aleblo-2026-09-06 | 12 | 2 | 0.62 | 0.58 | 4.0 | -4.0 | 4.23 | 0.995 | 0.00 | None |
| aec-setkameua-hreiur-konrom-2026-09-08 | 5 | 0 | 0.57 | 0.53 | 4.0 | -4.0 | None | None | None | None |
| aec-setkameua-hrykos-skaser-2026-09-08 | 12 | 0 | 0.55 | 0.52 | 3.0 | -3.0 | None | None | None | None |
| asc-cfb-smu-flst-2026-09-07-neg-10pt5 | 9 | 2 | 0.47 | 0.44 | 3.0 | -3.0 | None | None | None | None |
| aec-setkamemd-salvea-dupale-2026-09-07 | 5 | 4 | 0.4 | 0.42 | 2.0 | 2.0 | 10.82 | 0.98 | 0.00 | None |
| aec-cs2-ntr-cs-2026-09-07 | 6 | 0 | 0.38 | 0.39 | 1.0 | 1.0 | None | None | None | None |
| asc-cfb-smu-flst-2026-09-07-neg-2pt5 | 18 | 0 | 0.53 | 0.54 | 1.0 | 1.0 | None | None | None | None |
| atc-sea-udi-laz-2026-09-07-udi | 18 | 5 | 0.61 | 0.61 | 1.0 | 0.0 | 8.32 | 0.99984 | 0.00 | None |
| aec-cfb-smu-flst-2026-09-07 | 6 | 0 | 0.58 | 0.58 | 0.0 | 0.0 | None | None | None | None |
| aec-setkameua-derdmy-abaspa-2026-09-08 | 6 | 0 | 0.37 | 0.37 | 0.0 | 0.0 | None | None | None | None |
| aec-setkameua-hreiur-kuzgre-2026-09-08 | 6 | 0 | 0.36 | 0.36 | 0.0 | 0.0 | None | None | None | None |
| aec-setkameua-kuzgre-krovit-2026-09-07 | 6 | 0 | 0.69 | 0.69 | 0.0 | 0.0 | None | None | None | None |
| asc-cfb-smu-flst-2026-09-07-1h-neg-1pt5 | 6 | 0 | 0.49 | 0.49 | 0.0 | 0.0 | None | None | None | None |
| atc-arg2-chi-qui-2026-09-05-chi | 6 | 0 | 0.64 | 0.64 | 0.0 | 0.0 | None | None | None | None |
| atc-lpa-uni-iac-2026-09-07-draw | 6 | 0 | 0.43 | 0.43 | 0.0 | 0.0 | None | None | None | None |
| tsc-cfb-smu-flst-2026-09-07-total-53pt5 | 6 | 0 | 0.46 | 0.46 | 0.0 | 0.0 | None | None | None | None |
| tsc-mlb-stl-sf-2026-09-07-f5-2pt5 | 6 | 0 | 0.61 | 0.61 | 0.0 | 0.0 | None | None | None | None |
| tsc-sea-udi-laz-2026-09-07-2pt5 | 6 | 0 | 0.42 | 0.42 | 0.0 | 0.0 | None | None | None | None |

Path columns are DESCRIPTIVE (whole-market) and are not used as features anywhere above.

Labels: realized = the venue's own number (settlement rows only); derived = arithmetic on the ledger's fills / venue rows; measured = read from the venue's keyless public gateway at probe time; exploratory = in-sample (R5F day 1); hypothesis generation only; oos = out-of-sample (2026-09-08 00:51Z onward); evaluated against the mapping frozen before it was looked at; counterfactual = did not happen; fill-assumed, queue and depth not modelled
