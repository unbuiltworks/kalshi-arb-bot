# Next rung (OWNER 5570456323 / 5570474405)

**Invariant:** L5 DECIDES -> EXECUTE WELL -> RECYCLE WHEN L5 COMPLETES -> MEASURE TOTAL ECONOMICS -> SCALE WHAT WORKS

**Objective:** (completed pair edge + credited incentives - residual loss - fees) per bankroll-dollar per hour, L5 behaviour preserved

Baseline (R4 window 10.671 h): net $0.1525 = 0.000131 per bankroll-dollar-hour; active share 0.1747; per-market mean $-0.2296, s.e. $1.4181 on 6 at-size markets; turnover per capital-hour ours 3.849 vs L5 85.62.

| lever | changes | expected delta per bankroll-$-hour (EST) | downside | preserves L5 | evidence |
|---|---|---|---|---|---|
| environment selection + rotation: serve the live universe to the selector (recorder pool refresh) and rotate when the account is flat instead of waiting for settlement | environment / rotation only; no model, cap, size, ceiling or concurrency change | -0.002054 | -26.02 | True | opportunity_utilization: 2.51 h of the 4.58 h R4 window idle with 95 executable on-distribution markets on the public gateway and a recorder pool of resolved markets; 0.36 h flat-account wait; L5 re-enters within 60 s of settlement 77% of the time |
| touch-following reprice inside the 30 s rest window (execution layer) | execution only; the L5 decision, price band and deadline are unchanged | None | more fills inside the same cap: no new worst case per market; a reprice that chases can pay 1-2 c more per share on completions | True | patience study: L5 reprices after a median 6 s (83.5% inside 30 s), 2 c median step; ours: 9 of 45 orders filled, 23 cancelled untouched at the deadline; fill share 0.2 |
| residual cap $2.50 -> $3.75 (1.5 action units) | a safety bound (owner parameter) | 9e-06 | -3.75 | improves EXTEND fidelity; still a translation | curve: on L5's history +7% total dollars, income per dollar of peak residual 0.153 vs 0.186; actions kept 39.6% vs 31.0%; live: 44 of 54 EXTEND draws refused at 1 unit |
| action size $2.50 -> $5.00 (10 shares), cap held at $2.50 | size (owner parameter); the cap falls to 0.5 units | 0.000131 | -2.5 | worsens EXTEND fidelity unless the cap moves with it | ramp: L5's early day-median action was $3.25; its mature 10-13 share median came later |
| concurrency 1 -> 2 | capital at risk x2 | -0.000395 | -5.0 | False | L5 never ran two windows of a lane at once |

**Answer:** environment selection + rotation: serve the live universe to the selector (recorder pool refresh) and rotate when the account is flat instead of waiting for settlement. it is the only lever that raises L5 sequences per bankroll-dollar-hour without changing what an L5 sequence is; its expected dollar effect is the per-market mean (-0.2296, s.e. 1.4181) times ~10.41 extra markets per R4-length window, so its sign is the sign of the mean -- which more sequences are also the fastest way to establish; downside per extra market is the existing $2.50 cap.

Second: touch-following reprice inside the 30 s rest window (execution layer). Not now: residual cap $2.50 -> $3.75 (1.5 action units); action size $2.50 -> $5.00 (10 shares), cap held at $2.50; concurrency 1 -> 2.

Conditions: R4 completes its 3-market cohort untouched; owner approval of the change and of a fresh rung identity; everything else frozen: model, cap $2.50, ceiling 1.00, size $2.50, concurrency 1, maker-only, deadman

an answer for the owner's decision, not a promotion; every expected value is an ESTIMATE whose sign is not established
