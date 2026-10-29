# opportunity_utilization (OWNER 5570474405)

read-only; the gate names are the probe's own; the public-universe view is one snapshot (2026-09-07T11:09:22Z) applied to the intervals around it; nothing here admits a market, changes the recorder, the selector, a cap or a rung

Campaign window 2026-09-07T08:09:54Z -> 2026-09-07T11:37:18Z, 10.671 h. Hours by kind: {"ACTIVE": 1.864, "STRANDED": 0.416, "IDLE": 8.031, "FLAT_WAIT": 0.36}. Active share **0.1747**; idle-bottleneck hours **7.985** (0.7483 of the window).

| from | to | h | kind | market | pool (rows / closed / thin) | live events seen | gate | verdict |
|---|---|---|---|---|---|---|---|---|
| 2026-09-07T08:09:54Z | 2026-09-07T08:55:33Z | 0.761 | ACTIVE | aec-atpcq-aleher-tundur-2026-09-07 |  |  |  | in market |
| 2026-09-07T08:55:33Z | 2026-09-07T09:03:44Z | 0.137 | STRANDED | aec-atpcq-aleher-tundur-2026-09-07 |  |  | HOLD residual to settlement (owner rule); residual held until the venue resolved | residual held by design; rotation blocked by concurrency 1, an owner parameter |
| 2026-09-07T09:03:44Z | 2026-09-07T09:06:31Z | 0.046 | IDLE |  |  |  | strict-evidence refusals | pool had live rows refused on evidence; see reasons |
| 2026-09-07T09:06:31Z | 2026-09-07T09:25:02Z | 0.309 | ACTIVE | aec-atpcq-norgom-ollwal-2026-09-07 |  |  |  | in market |
| 2026-09-07T09:25:02Z | 2026-09-07T09:46:40Z | 0.36 | FLAT_WAIT | aec-atpcq-norgom-ollwal-2026-09-07 | 80 / 71 / 9 | 59 | CAMPAIGN_HOLD:POSITION_HELD (one market at a time; the campaign waits for settlement even with a flat account) | flat account but no rotation until settlement: an implementation gate, not an L5 behaviour (L5 re-enters within 60 s of settlement 77% of the time, never before) |
| 2026-09-07T09:46:40Z | 2026-09-07T17:45:46Z | 7.985 | IDLE |  | 80 / 75 / 5 | 49 | recorder pool composition (strict_evidence_market_closed / strict_evidence_thin_flow on every row) -> CAMPAIGN_NO_CANDIDATE | selection/coverage bottleneck: the recorder pool (80 rows) held 75 resolved markets and 5 thin-flow ones, nothing live and evidenced, while the public gateway showed 257 in-distribution live markets (95 executable on public features, 14 of them live). The strict-evidence gate could not admit what the recorder was not serving. |
| 2026-09-07T17:45:46Z | 2026-09-07T18:33:26Z | 0.794 | ACTIVE | aec-atp-petbru-alecan-2026-09-07 |  |  |  | in market |
| 2026-09-07T18:33:26Z | 2026-09-07T18:50:09Z | 0.279 | STRANDED | aec-atp-petbru-alecan-2026-09-07 | 80 / 75 / 5 | 49 | HOLD residual to settlement (owner rule); residual held until the venue resolved | residual held by design; rotation blocked by concurrency 1, an owner parameter |

Public universe at 2026-09-07T11:09:22Z: 1021 markets in 50 live events, 352 scored, 257 in-distribution; scorecards: 114 on-distribution, 221 with 5-share depth both sides, **95 executable on public features (14 live, 14 rebate-eligible at price)**. 24 of the top-25 in-distribution live markets were absent from the recorder pool.

Expected economics (ESTIMATES): {"labels": "ESTIMATES; not realized; L5 priors from its own history, ours from settled markets", "l5_completed_share_of_bought_usd": {"complete": 0.9883, "extend": 0.7986, "open": 0.7176}, "l5_net_per_usd_by_relation": {"complete": 0.0201, "extend": 0.0037, "open": 0.0269}, "l5_time_to_complement_p50_s_by_tte": {"tte1-3m": 24, "tte3-10m": 52, "tte<60s": 13, "tte>10m": 56}, "our_r4_turnover_per_market_usd": 6.869, "our_r4_net_per_turnover": 0.0074, "our_r4_capital_hours_per_market": 5.516, "our_at_size_net_per_turnover": -0.02494}

Capacity foregone in bottleneck hours: {"markets_at_r4_pace_ESTIMATE": 9.94, "turnover_usd_ESTIMATE": 68.3, "labels": "capacity foregone, not P&L; the sign of the per-market net is not established"}
