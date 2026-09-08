# L5_COMPLETE_REQUOTE_AB_v1 — bounded live A/B design

**Authorises nothing.** an explicit owner go, naming the arm split and the stop conditions. PMUS_SPRING_COMPLETE_REQUOTE defaults OFF and no code here sets it.

## Arms

- `control_no_complete_requote` / `treat_complete_requote`, assigned by market slug
- deterministic hash of the slug, so a market's arm never changes between sessions or across a restart -- a market that flips arms mid-residual would be neither arm
- not per session: a residual can span sessions after a restart. Randomising per session would let the same held position be worked under both arms and belong to neither.
- not by clock: sports liquidity has a time-of-day shape; alternating by clock would confound the arm with the slate.

## Non-contamination of the frozen gate

every session_end row carries requote_arm. The extension evaluator counts CONTROL-arm sessions only, so L5_COMPLETION_EXTENSION_GATE_v1's baselines, criteria and sample minimums are untouched.

**Cost.** the extension accrues evidence at roughly half rate: its remaining ~20 firings move from about 5 forward hours to about 10.

**What would break it.** an unlabelled session. A session_end row without an arm field must be counted in NEITHER arm and must be reported, never defaulted to control -- defaulting would silently feed treated sessions into the extension's evidence.

L5_COMPLETION_EXTENSION_GATE_v1 keeps its frozen sha. This design changes what the evaluator COUNTS, never what the gate REQUIRES.

## Primary endpoint

**realized dollars per capital-dollar-hour, treat minus control**

> the offline replay says two thirds of these completions are ABOVE PAR. A completion-rate endpoint would score locked losses as wins, which is the exact failure this whole analysis exists to avoid.

Secondary:

- residual units carried to settlement per settled market
- pair cost distribution, and the share above par
- complete rejections per session, treated arm only
- complement-proof revocations per 100 completes

## Bounds

Unchanged:

- ~$2.50 per action
- $7.50 per market
- concurrency 1
- maker-only
- post-only
- the daily loss stop
- the entry band
- the frozen model sha
- the frozen lookup v3
- COMPLETE_REJECT_STREAK_MAX = 3
- the extended action budget

New bounds this change requires:

- `max_complete_requotes_per_session` = 4
- `requotes_charge_the_action_budget` = True
- `rejection_breaker_covers_requotes` = True
- `and_the_gemini_cap_argument` = our Gemini shadow's touch_cancel_30s shell issued 2465 cancels for a 4.2% first-leg fill rate. Uncapped re-quoting degenerates into cancel-churn that never rests long enough to be hit, which is why the cap is 4 per session and not unbounded.

> a rejected complete at a non-crossing price revokes the complement proof, which is the deadlock that took the lane down at 13:09 on 2026-09-08. Re-quoting must not buy fills by spending proofs.

## Sample minimums

- settled_venue_confirmed_markets_per_arm: **12**
- sessions_that_opened_a_residual_per_arm: **30**
- sessions_where_a_requote_fired: **20**

## Early stops (safety, not efficacy)

- complement_proof_revocations_in_treat_exceed_control_by: 3
- treat_arm_daily_loss_stop_hit: True
- any_bound_violation: True

> these are safety stops, not efficacy reads. Hitting one halts the treated arm and leaves control running.

