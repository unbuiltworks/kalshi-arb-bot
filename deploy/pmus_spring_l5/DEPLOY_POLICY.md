# The live probe does NOT auto-deploy on push

Its Railway watch set was changed to a sentinel path that never matches
(`deploy/pmus_spring_l5/DEPLOY_AT_BOUNDARY_ONLY`), so pushing a file the probe
used to watch — `pmus_l5_continuous_session.py`, `pmus_sports_l5_decision.py`,
`pmus_status_report.py` and the rest — **no longer restarts it**.

## Why

This process holds real inventory. Auto-deploy on push means any commit
touching a shared module restarts a live money process at a moment nobody
chose. The campaign has been in `SPRING_RESIDUAL_HOLD` all night and the
standing instruction has been not to touch it mid-hold; under auto-deploy,
honouring that meant never pushing probe-side code at all, which is why the
boundary reader could not be staged.

With deploy decoupled from push, probe-side changes can be **staged on the
branch** and applied at the clean boundary, which is the only moment a restart
is free.

## The hazard this creates, stated plainly

A push that *should* reach the probe now silently will not. That is the same
class of failure that bit this project twice on 2026-09-10 (`9912e9a8` read
SKIPPED and left broken code serving; `d2624218` deployed and collected
nothing). **Anyone changing probe code must deploy it explicitly and then
verify the deployment**, not assume the push did it.

## How to deploy the probe

Explicitly, via the Railway API — `redeploy`, or `create-deployment` against
the service — and then CONFIRM: read the deployment status AND the probe's own
status row (commit hash, `errors`, campaign state). SUCCESS alone is not
health.

## When

At a clean boundary only: `held: []`, status no longer `SPRING_RESIDUAL_HOLD`,
`flat_verified: true`, and the seven-check preflight green. That sequence is
pre-approved and needs no further owner turn.

## The action-window rule (MANDATORY, added 2026-09-13 after it was broken)

**The authoritative risk/position/open-order read that authorizes a cutover
MUST be taken inside the deployment action window. If the state changes
between authorization and action: ABORT and RE-EVALUATE.**

This is not advisory. It was broken on 2026-09-13 and the deploy log had to be
corrected: a flat state measured at 16:21:48Z and a funnel read at 16:29:10Z
were used to authorize a cutover at 16:33:43Z, and in that gap
`aec-cs2-mzp-ezt-2026-09-13` filled 8.11 NO contracts for $3.0443. Nothing was
lost, because `orphan_sweep` found zero resting orders and all eleven boot
assertions passed — but that was the gates working, not the operator.

The rule, concretely:

1. **Read inside the window.** Positions, open orders and the risk set are read
   at the moment of acting, not minutes before. Two GETs; there is no excuse
   for a stale read.
2. **The authorization expires.** `CUTOVER_MAX_AGE_S = 60.0`. Older is refused.
3. **Re-read at the moment of action and compare.** Any change to `held`,
   `open_orders_ours`, `open_orders_foreign`, `risk_bearing` or
   `unmatched_usd` ABORTS. The reason the authorization was granted may no
   longer exist.
4. **Unreadable is never a pass.** A venue read that raises, or returns
   something malformed, is a refusal carrying its reason.
5. **Time can only invalidate, never authorize.** No waiting turns a refusal
   into a pass — the same shape as the re-arm gate.

`src/pmus_cutover_preflight.py` implements this and
`tests/test_cutover_preflight.py` pins it, including the exact 715-second
failure above. The module is read-only by construction: a test forbids every
order and deploy verb from appearing in its source.

**Which boundary applies is still a choice, and it must be STATED.**
`require_flat=True` is the strict boundary (`held: []`). `require_flat=False`
is the revised dormant-residual boundary adopted 2026-09-11 — permitted, but
the residual it carries is reported either way, so the deploy log always shows
what was held.

## Deploy log

Each explicit probe deploy is recorded here, because "we deployed it" is a
claim and this file is where it becomes checkable.

- **2026-09-11 02:02Z — `5efc9a2f`, deployment `92d127ad`, SUCCESS.**
  Per-trade quantity evidence. Restarting a probe that held inventory tripped
  `boot_assertions → flat_held_state` and it went to `REARM_REFUSED`; the
  arming gate only arms from flat. No orders were lost (fresh venue read
  `{'ours': 0, 'foreign': 0}`). The lesson is in the gate's favour: it did
  exactly what it exists to do.
- **2026-09-11 02:16Z — `258dfb2e`, deployment `be0c0fec`, SUCCESS.**
  Moved the quantity reconciliation onto the `shutdown_idle` pass, because a
  halted probe never reaches `session_end`. It answered the question on the
  first cycle: venue 45.0100 against ledger 45.0100 on BOTH measures, and 12
  of 13 trades with `qty` disagreeing with `qtyDecimal`. The 774.62 phantom
  was a unit fallback, not a position.
- **2026-09-11 — one-shot migration close.** `PMUS_MIGRATION_CLOSE_SLUG` is
  set to the exact cricket slug, so the boot runs the reduce-only close
  BEFORE the arming gate and a single boot can go residual → flat → armed.
  Unset the variable once the close has reported `CLOSED_FLAT`: leaving it
  set is harmless (a zero residual has no reducing side and refuses) but it
  is not meant to be a standing configuration.

- **2026-09-11 — residual quarantine promotion.** Deploying the CI-green
  boot-closure build. `92b39ca6` is the validated commit: GitHub Actions run
  `34591355951` (pmus-collector-ci run 418) concluded SUCCESS at 10:55:34Z;
  2,829 tests pass locally.

  An empty commit cannot trigger a Railway build — Railway matches CHANGED
  PATHS against the watch set, and an empty commit changes none. So strict
  tree identity and a push-trigger are mutually exclusive, and this entry is
  itself the trigger. The deployed commit differs from `92b39ca6` by exactly
  this documentation block: every path under `src/`, `tests/`, the Dockerfile
  and `railway.json` is byte-identical, which is the property that matters —
  the image built is the image CI validated.

  After the serving process is verified, `PMUS_RESIDUAL_QUARANTINE=1` is set
  and nothing else. The existing gates then decide: `blocking_held()` requires
  the cricket residual to be genuinely dormant (zero resting orders, venue
  quantity reconciling against the ledger, real inventory, still in `held`,
  still settlement-monitored), and `combined_loss_above_stop` separately
  prices its full downside against the −$15.00 stop. Either it qualifies and
  R5F re-arms, or the probe stays halted and names the assertion.

  **Outcome, 11:40Z.** Deployment `e48dcf90`, commit `383a69631848`.
  `boot_assertions ok: true` — all eleven pass. The one line that changed:

      quarantine OFF   flat_held_state ok=false got ['aec-county-not-ham-…']
      quarantine ON    flat_held_state ok=true  got []

  `combined_loss_above_stop` stayed `true` throughout; cricket's downside is
  still charged. Campaign reports `mode: ARMED`.

  **But it still does not acquire.** The very next row:

      resume_start  selection_skipped: "CAMPAIGN_HOLD:POSITION_HELD
                    (1/1 markets held, worked one session at a time)"
                    funnel: {admitted: 0, selection_skipped_position_held: 1}

  The BOOT gate is quarantine-aware; the RUNTIME acquisition path is not.
  Three coupled places still count a quarantined market as occupying the
  slot, all in the live order path:

    1. `capacity = (len(held_all) < concurrency and not _allowance_spent())`
       — cricket consumes the single acquisition slot.
    2. `over_concurrency_action(held_all, filled_markets, concurrency)` —
       freeing (1) alone would open a second market and then immediately trip
       `CAMPAIGN_DRAIN:OVER_CONCURRENCY` on the next cycle.
    3. `micro.account_block_reason` via `set_concurrent_owned`, which is
       passed `()` under every concurrency-1 rung. A NEW open's preflight
       requires an account-wide flat, so cricket blocks it as
       `nonzero_positions` regardless of (1) and (2).

  (3) is the sensitive one: it is the account-wide flat lock. Making
  quarantine work end-to-end means teaching that lock to accept a declared
  quarantined position — which is what quarantine is for, and is still a
  change to a safety lock on the money path. Not attempted here.

- **2026-09-11 — quarantine's runtime half.** The refusal above named the
  condition; this is the condition fixed, not routed around.

  Dormancy now has ONE definition, `dormant_held()`, read by both halves:
  the BOOT gate through `blocking_held()` (unchanged behaviour) and the
  RUNTIME scheduler directly. The slot arithmetic has one definition too,
  `acquisition_state()` — a pure function, specifically so it can be tested,
  because the three places that disagreed disagreed precisely by each
  recomputing it inline inside a 900-line loop where nothing could reach it.

  What changed, in the scheduler:

    1. `capacity` counts ACQUIRING markets, not held ones.
    2. The over-concurrency DRAIN counts acquiring markets. Without this,
       freeing (1) opens a second market and the next cycle declares
       `CAMPAIGN_DRAIN:OVER_CONCURRENCY` — quarantine would buy exactly one
       market and re-deadlock. The BREACH branch is untouched and still
       reads every held market: a position this campaign did not open halts
       it, quarantine or not.
    3. A market established as dormant is DECLARED to the account preflight
       through `set_concurrent_owned` — the same audited `allow_slugs`
       channel the concurrency>1 rung has always used. Nothing else about
       the preflight changed, and `open_orders:N` still refuses a first leg
       for ANY resting order in the account regardless of what is declared.

  A short-lived cache on `dormant_held` was written and then removed. It
  saved a handful of read GETs and bought a window in which `venue_confirmed`
  could already be false while it still answered "dormant" — the 774.62
  shape, on the money path, in the function whose whole job is establishing
  that a position is safe to stop watching.

  Still true, each with a test that fails if it is removed: quarantine OFF is
  byte-identical to the pre-quarantine loop; a dormant market stays in
  `held`, so combined_loss, settlement monitoring and the round-robin all
  still see it; it is still worked for COMPLETE; it is never re-selected as a
  new first leg; no cap, ceiling, concurrency, residual bound or daily stop
  moved.

  **Deploy trigger, 12:0xZ.** CI-green commit for the runtime half is
  `6d097516` — GitHub Actions run `34596461473` (pmus-collector-ci run 421),
  conclusion SUCCESS at 2026-09-11T11:59:31Z; 2,850 pass / 0 fail / 27
  skipped locally. As before, an empty commit cannot trigger a Railway
  build (Railway matches CHANGED PATHS), so this deploy-log entry is itself
  the trigger, and against `6d097516` it changes exactly one documentation
  file — every path under `src/`, `tests/`, the probe Dockerfile and
  `railway.json` is byte-identical, which is the property that matters: the
  image built is the image CI validated.

  **Outcome, 12:03–12:11Z. Deployment `6cfbd53c`, commit `319bd682ce92`.**
  `boot_assertions ok: true`, all eleven, `mode: ARMED` — and this time the
  new row fired:

      quarantine_slot  quarantined ['aec-county-not-ham-2026-09-08']
                       held        ['aec-county-not-ham-2026-09-08']
                       acquiring   []

  Four minutes later the campaign had opened, filled and PAIRED a different
  market — the structural proof, from the venue's own numbers:

      12:03:16  boot_assertions ok: true          (11/11)
      12:03:20  quarantine_slot  acquiring []
      12:04:07  session_end  aec-itfwo-kaycro-sofjoh-2026-09-11
                status SPRING_MATCHED   filled_actions 2   submitted 5
                spring_open_4     BUY YES 5.60 filled 5.6  CE4K0JQEETN3
                spring_complete_5 BUY NO  5.6  filled 5.6  CE4K0S6A2TN5
                inventory up 5.6 / dn 5.6 / matched 5.6 / RESIDUAL 0.0
                avg_up 0.4200  avg_dn 0.5700  spent 5.5440
                pair_at_chosen 0.9900  above_par false  fee_safe true
                venue_vs_ledger venue 11.2000 ledger 11.2 delta 0.0000
                                agrees true    flat_verified true
      12:07:31  session_end  aec-cs2-wc-nem-2026-09-11        SPRING_NO_FILL
      12:10:39  session_end  aec-itfme-rustiu-dmipop-…        SPRING_NO_FILL

  Three new markets worked in seven minutes, where the same engine holding
  the same residual opened nothing in 3h20m. No `concurrency_drain`, no
  `concurrency_breach`, no `CAMPAIGN_DRAIN:OVER_CONCURRENCY`. Cricket stayed
  in `held` and was still resumed between them, and its full downside stayed
  charged against the −$15.00 stop throughout.

  LABELLING. That pair is SUBMITTED → FILLED → PAIRED. It is **not**
  realized: the market has not settled, no rebate has been credited, and
  the +$0.056 implied by a 0.99 pair cost on 5.6 units is gross of fees and
  of nothing else. Realized comes from the venue's settlement number or it
  does not exist.

## The boundary standard, revised 2026-09-11

The "When" section above says `held: []`. That was written when a restart
holding inventory tripped `flat_held_state` and went to `REARM_REFUSED` —
the 02:02Z entry below is exactly that. With residual quarantine live, a
restart holding a DORMANT market boots and arms: proven at 12:03:16Z, all
eleven assertions green with cricket held and quarantined.

So the operative boundary is now:

- no session actively working inventory (between sessions, not mid-order);
- zero resting orders at the restart moment;
- every held market expected to satisfy `rq.eligibility` — zero resting
  orders, venue-confirmed quantity, real inventory, still settlement-
  monitored — so `blocking_held()` returns `[]`.

`held: []` remains the *safest* boundary and is preferred when available.
It is no longer the only one, because a continuously acquiring campaign may
not reach it for hours and that would mean never deploying observability.

**This is a change to when a live money process restarts, so it was not made
unilaterally: OWNER decision 2026-09-11, on the specific state below.**

- **2026-09-11 — read-only risk-set audit.** CI-green `854e3b4d`
  (pmus-collector-ci SUCCESS; 2,906 pass / 0 fail). Deployed at a boundary
  where `held: 1` — `aec-cs2-rev-is-2026-09-11`, 5 units unmatched YES at
  0.35 (~$1.75), static book 0.35/0.36, in `resume_wait` between sessions
  for 25+ minutes with no session working it.

  WHY THIS SHIPS. `aec-county-not-ham-2026-09-08` left `held` between
  12:03:20Z and 13:18:11Z and its state could not be established from the
  logs — not because the evidence was ambiguous but because there was none:
  the running probe never emitted its risk set. `unsettled` appears only in
  `rung_markets_reached`, which a continuous campaign never reaches, and in
  the `shutdown_idle` re-price, which needs a halt.

  ACCEPTANCE, narrow and owner-set: same risk limits, same R5F logic, no
  order-path change, clean boot, then the first emitted row shows BOTH the
  scheduler set and the ledger-backed `unsettled` risk set. If cricket is
  still in `unsettled` it is still being charged; if it is absent, a terminal
  ledger transition occurred even though the settlement row was missing from
  the logs we inspected.

  Additive logging only. No cap, ceiling, budget, bound or gate changed.

- **2026-09-11 — ARMING THE R5FT CANARY.** The start command goes back to
  the trading campaign after the read-only settlement sweep, on the owner's
  explicit "go 3" (a q=1 live canary at a relaxed target) and his choice of
  "Join the touch, hard cap <=1.00". Approval row
  `R5FT-CANARY-2026-09-11`, rung `R5FT`, shell
  `R5FT/cap30.00/unmatched-none/complete-l5touch/conc1/maker-only/rest-model5s/action1.00`.

  WHAT IS DIFFERENT FROM R5F, and it is two things, not one. Price: the
  complement joins the best non-crossing maker price, with the pair ceiling
  STATED at par instead of inheriting `DEFAULT_PAIR_COST_MAX = 0.98` — which
  is what the rung's own 2026-09-07 approval text said all along ("above-par
  pair cost is reported/accounted, not behaviorally gated"). Size: the owner
  raised the per-market cap to $30.00 and the daily realized-loss stop to
  -$30.00 on 2026-09-11, reversing his earlier "Keep the daily stop at -$15"
  after being told the worst day becomes roughly $60 against a ~$109 balance.

  THE START COMMAND CARRIES `PMUS_SPRING_RUNG_MARKETS=1`, and that is
  load-bearing. Durable state persists the R5F campaign, whose
  `markets_per_campaign` is CONTINUOUS, so a campaign booting without the
  allowance never reaches its advance gate, never consumes the R5FT
  approval, and RESUMES R5F trading at 0.98. The variable is set on the
  service as well, but that write cannot be read back, so the repo states it
  where a test can hold it.

  WHAT THIS DEPLOY CANNOT DO. If `continuation_preflight` fails — any filled
  market not venue-confirmed settled, any open or foreign order, a position
  held, a ledger/venue quantity mismatch, buying power below the $30.00
  worst case, or an unknown deployment identity — the campaign waits at
  `CAMPAIGN_WAITING:CONTINUATION_PREFLIGHT` and never selects a market. If
  the gate cannot fire at all it waits at
  `CAMPAIGN_WAITING:RUNG_MARKETS_REACHED`. Both are silent, fail-closed
  stalls: the canary not starting is a normal outcome of this deploy, and
  is diagnosable from the preflight row rather than from an absence.

  `restartPolicyType` stays NEVER: a money-moving bounded session must not
  relaunch itself after a crash or a completed campaign.

  ACCEPTANCE: clean boot; the `campaign_settled_seeded_from_ledger` row
  names the markets the previous process failed to record; the advance gate
  fires; the preflight row is emitted in full; and if it passes, the first
  complement preflight shows `pair_cost_max` 1.00 and
  `opp_ask - chosen_px` 0.00 rather than the 0.02 that put us three ticks
  and ~200,000 shares back.

- **2026-09-11 21:22Z — FAILED DEPLOY `eb3fc076`, and what it taught.** The
  arming deploy above failed. The build was clean — 37/37 layers, image
  pushed, `sha256:125456e8…` — and the volume mounted at 21:22:51Z, but
  there is **no "Starting Container" line in the deploy log at all**. The
  container was never created. Between mount and "Stopping Container" the
  application produced no output whatsoever, which is the signature of a
  start command that never execs rather than a process that started and
  died.

  CAUSE: the start command was
  `PMUS_SPRING_RUNG_MARKETS=1 python3 -m src.pmus_l5_continuous_session`.
  An env-var prefix is a **shell** feature, and a Railway start command is
  not guaranteed a shell — it can be exec'd as argv, in which case
  `PMUS_SPRING_RUNG_MARKETS=1` is read as the executable name and nothing
  runs. The service's own `CMD` uses an explicit `["sh", "-c", ...]` form
  for exactly this reason; overriding the start command dropped that shell.

  FIX: the allowance moves to the Dockerfile `ENV`, where it is in the
  repo, in the image, and under test, and cannot be mangled by start-command
  parsing. The start command returns to the plain module form that every
  previous probe deployment used successfully.

  NOTE ON THE `PMUS_SERVICE_ROLE` GUARD: a start-command override also
  replaces the `CMD`'s role check. That is not new here — every previous
  probe start command, including the settlement sweep, did the same, and
  the role is set by image `ENV` regardless. Recorded so it is not
  mistaken for a regression introduced by this change.

  NOTHING TRADED. A container that never started placed no orders, and the
  ledger is untouched.

- **2026-09-13 16:3xZ — BOUNDED CANARY CUTOVER, candidate `710d1bd7`.**
  The corrected engine: P0.3 behavioural inventory fidelity, P1 rotating
  terminal reconciliation, P0.2 self-healing REARM, and the risk-set
  observability split. Previous serving commit `8606c87f`, deployment
  `718ca492`. **Rollback SHA is `8606c87f`.**

  THE BOUNDARY, measured rather than assumed:

      16:21:28Z  asc-lal-lev-fcb-2026-09-13-pos-2pt5 REALIZED +$2.0380
                 settled_by position_resolution_activity
                 venue_qty 16.3400 == ledger_qty 16.34
                 matched 7.07 @ pair 0.9125, residual 2.20 LONG
      16:21:48Z  risk_set n_risk_bearing 0, n_scheduled 0
                 combined_loss +5.6734 against the -30.00 stop
      16:29:10Z  funnel not_started {no_candidate: 37}
                 filled 35 static, selection_skipped_position_held 80 static
      16:25:36Z  last session aec-cs2-mzp-ezt SPRING_NO_FILL,
                 4 submitted, 0 filled, every order cancelled or repriced

  So: no position held, no session working inventory, zero resting orders,
  every risk-bearing market venue-reconciled, ten terminal markets recorded
  today. This is `held: []` — the strictest boundary, not the revised one.

  ONE EXECUTOR. `pmus-q1-executor` measured idle over two hours: CPU 0,
  memory 0, network 0, 25 samples. `pmus-spring-l5-probe` is the only
  money-moving process.

  BUILDCHECK ON THE EXACT CANDIDATE SHA, isolated service, deployment
  `7f92818f`:

      CANDIDATE_IMPORTS_OK
      REARM_RUNG=R5FT                      (the start-command sed applies)
      SETTLE_AUDIT_BATCH=5
      SHUTDOWN_IDLE_S=300.0                (unchanged)
      TERMINAL_VER=canonical-terminal/v1
      CAP1_NONDORMANT_SWEEP=False          (the dependency, in the image)
      SENSITIVE_ENV_NAMES=PMUS_Q1_STATE_DIR,PMUS_SERVICE_ROLE,
                          PMUS_SPRING_MAX_ACTIONS,PMUS_SPRING_RUNG_MARKETS
      SHA=710d1bd71f67efa14a98dc31c246b1a9b6f53f51

  No credentials reach the buildcheck: those four are non-secret config.
  An earlier claim of mine that the count implied leaked credentials was
  wrong and is corrected here — the regex matched the `PMUS_` prefix.

  TESTS: 3184 pass. One pre-existing failure,
  `test_sports_shadow_breadth::test_watch_set_keeps_ineligible_markets_so_they_can_become_eligible`,
  date-sensitive and red at the baseline this branches from. Zero new
  failures. NOT green overall, and said plainly.

  DIFF `8606c87f..710d1bd7`: four modules, 631 insertions / 95 deletions,
  plus one line in `railway.json` adding `pmus_r5ft_ledger_repair.py` to the
  watch list (the Dockerfile already COPYs it at line 53; consistency only).
  Grepped for every limit token: **no stop, cap, ceiling, band, size,
  concurrency, residual bound, pair ceiling, taker or post-only rule
  changed.** The only `daily_loss_stop_usd` hits are the reporting kwarg.

  NOTHING IS RAISED. Same bankroll, same $30.00 per-market cap, same 6-unit
  residual cap, same concurrency 1, same 1.00 pair ceiling, same maker-only,
  same -$30.00 daily stop, same R5FT rung and safety shell.

  MECHANISM. `create-deployment` creates a NEW service and is therefore not
  usable here (no duplicate services). `redeploy` reuses the prior build AND
  spec — measured today at 15:47Z, when it replayed the old start command.
  A variable write triggers a genuinely fresh build from branch HEAD, which
  is what `PMUS_FORCE_REDEPLOY_NONCE` exists for and what produced the
  candidate buildcheck twice today.

  WHAT THIS DEPLOY CANNOT DO, unchanged: if `boot_assertions` or
  `continuation_preflight` fails, the campaign stops or waits and never
  selects a market. A canary that does not start is a normal outcome and is
  diagnosable from the preflight row. `restartPolicyType` stays ALWAYS for
  the read-only loop; money-moving sessions still fail closed.

  **CORRECTION, same entry, written 16:40Z. PRODUCTION WAS NOT FLAT WHEN I
  TRIGGERED THIS.** The entry above says "no position held" and "every
  risk-bearing market venue-reconciled". That was true when I measured it and
  stale when I acted on it.

  I verified the risk set at **16:21:48Z** (empty) and the funnel at
  **16:29:10Z** (`no_candidate`, `filled` static at 35), then triggered the
  cutover at **16:33:43Z**. In that window `aec-cs2-mzp-ezt-2026-09-13`
  filled **8.11 NO contracts for $3.0443**, and by 16:34:33Z the outgoing
  process was reporting it as `SPRING_RESIDUAL_HOLD`, residual −8.11,
  `unmatched_usd 3.0443`, four post-only extends all rejected
  `post_only_cross_within_1_tick_of_lagging_ask`.

  I asserted a precondition I had not re-read inside the action window. The
  correct procedure is to re-read the risk set immediately before the
  trigger, not four and twelve minutes before it.

  WHAT MADE IT SAFE ANYWAY — the gates, not the operator:

      16:36:34Z  orphan_sweep   seen 0, cancelled [], foreign []
      16:36:49Z  boot_assertions ok: true  (11 of 11)
                 no_open_orders_fresh_read {'ours': 0, 'foreign': 0}
                 flat_held_state []        <- quarantine: mzp-ezt is DORMANT
                 combined_loss_above_stop true
                 rung R5FT, band 0.25-0.75, pair ceiling 1.00, streak 3
      16:37:03Z  risk_set  n_risk_bearing 1  acquisition_slots_in_use 0
                 total_unmatched_usd 3.0443  portfolio_worst_case_usd 3.0443
                 daily_realized_pnl_usd 5.6734   combined_loss_usd 2.6291

  Zero resting orders at the restart moment, so nothing was orphaned and
  nothing was lost. `flat_held_state []` passed because residual quarantine
  had established mzp-ezt dormant — which is the revised boundary standard
  this file adopted on 2026-09-11, not the strict one I claimed to be using.
  So the deploy met the standard that actually applies; my description of
  which standard it met was wrong.

  AND THE NEW OBSERVABILITY EARNED ITS PLACE IMMEDIATELY. `daily_realized_pnl_usd
  5.6734` is the number I previously had to reconstruct by hunting for moments
  when the risk set happened to be empty; it agrees with that reconstruction to
  the cent. `acquisition_slots_in_use 0` beside `total_risk_bearing_markets 1`
  is precisely the slots-are-not-exposure distinction, now unmissable in one row.

- **2026-09-13 23:5xZ — OBSERVATION-ONLY INSTRUMENTATION CUTOVER.** Previous
  serving commit `f7a3e224`, deployment `86eb63ff`. **Rollback SHA is
  `8606c87f`, deployment `718ca492`.**

  WHAT SHIPS: the cross-shadow instrumentation only. `_recorder_book()` keeps
  the `bid_qty`/`ask_qty` it was already receiving and discarding, records
  `age_at_probe_receipt_s` beside (never instead of) `authenticated_age_s`, and
  a post-hoc SHADOW-ONLY evaluator scores whether a bounded `<=0.97` taker
  COMPLETE would have been executable and net-positive after the confirmed
  taker fee AND the forgone maker rebate. `would_submit` is false on every
  path; no submission capability exists in the module by construction. The
  live `$1.00` pair ceiling does not move and maker-only stays.

  **WHY THIS IS NOT SIMPLY `75c557fe`, AND THE ONE THING HELD BACK.** The
  probe's watch set is a sentinel, so it never auto-deployed: production sat at
  `f7a3e224` while TWENTY-ONE further commits landed on the branch. Deploying
  branch HEAD would therefore have shipped far more than observation. Audited
  file by file, the probe IMAGE surface of `f7a3e224..75c557fe` is exactly
  three files — and one of them is not observational:

      src/pmus_cross_shadow_live.py        +556  new, SHADOW ONLY
      src/pmus_spring_l5_session.py         +96  observational, 0 deletions
      src/pmus_l5_continuous_session.py     +23  CANDIDATE ADMISSION  <-- held

  That third one is `392b5c0e`'s evidence-age re-stamp: `now` was sampled
  BEFORE `load_status_document()`, so freshness was computed as
  `time_before_request - time_during_request` and went negative by the fetch
  latency — measured live at 19:16:39Z as
  `strict_evidence_from_future:-7.617` on three MLB moneylines. It is a real
  defect and a good fix. It is also a change to WHICH MARKETS ARE ADMITTED: a
  market refused today would be admitted tomorrow. This cutover was authorised
  as observation only, so it is **DEFERRED, NOT DISCARDED**, and
  `src/pmus_l5_continuous_session.py` is reverted here to the exact content
  `f7a3e224` is serving. Production is not losing the fix — production never
  had it.

  Re-landing is one command and is its own deploy decision at its own
  boundary:

      git show 392b5c0e -- src/pmus_l5_continuous_session.py | git apply

  `tests/test_evidence_freshness_ordering.py` keeps every gate-level test
  green and carries the caller-ordering test under `xfail(strict=True)`, so
  the suite goes RED the moment the fix returns and the marker cannot be
  silently left behind. The gate itself
  (`pmus_spring_l5_probe_entry.strict_evidence_reason`) is byte-identical
  across the whole range and is not touched by any of this.

  IMAGE EQUIVALENCE, ASSERTED NOT CLAIMED. Every path the probe Dockerfile
  COPYs was compared blob-for-blob against `f7a3e224`. All identical except
  the two observational files above and the new shadow module. The model and
  gate artifacts — `l5_action_model_v2.json`, `l5_fidelity_pass.json`,
  `l5_fidelity_gate.json`, `venue_maintenance_windows.json`,
  `l5_cadence_behavioral_unit.json`, `l5_campaign_approvals.json` — are
  byte-identical, so THE MODEL IS UNCHANGED. `pmus_priority_queue.py` and the
  recorder modules also changed on the branch but are NOT in this image and
  cannot reach the probe. The start command's `REARM_RUNG` sed target is still
  present, so the R5FT transform still applies.

  NOTHING IS RAISED. A 36-point audit against `f7a3e224` reports
  EXECUTION_SEMANTIC_DIFF = NONE and RISK_ENVELOPE_DIFF = NONE: residual cap
  6, band 0.25-0.75, spring target 0.98, `$1.00` hard pair ceiling, streak 3,
  complete actions 12, requote default off, COMPLETE_BOOK 8 / 2.0s, control
  book 24 / 5.0s, trim 10.0s / 750ms, rest 30s, safety-shell string — all at
  their serving values. Every `place_limit_buy` is still `post_only=True` and
  `post_only=False` appears nowhere. The session module gains 96 lines and
  loses ZERO.

  MECHANISM: a variable write on `PMUS_FORCE_REDEPLOY_NONCE`, which forces a
  genuinely fresh build from branch HEAD — `redeploy` replays the old build
  and spec, and `create-deployment` would make a second service. The sentinel
  watch set is NOT touched and `DEPLOY_TRIGGER` is NOT created.

  BOUNDARY (the revised dormant-residual standard adopted 2026-09-11, stated
  rather than assumed): one market held, `aec-mlb-sea-ath-2026-09-13`,
  PENDING_SETTLEMENT with its residual held to settlement; campaign in
  `CAMPAIGN_HOLD:POSITION_HELD`; last session `SPRING_SKIP_NOT_QUALIFYING`
  with zero orders; `acquisition_slots_in_use 0`. The authoritative
  position/open-order read that authorises the trigger is taken INSIDE the
  action window per the rule above — `CUTOVER_MAX_AGE_S = 60.0` — and any
  change to `held`, `open_orders_ours`, `open_orders_foreign`, `risk_bearing`
  or `unmatched_usd` between authorisation and action aborts.

  ONE EXECUTOR: `pmus-q1-executor` measured idle over three hours — CPU 0,
  memory 0, network 0 across 181 samples. `pmus-spring-l5-probe` is the only
  money-moving process.

  The deployment id, build result and post-cutover verification are reported
  by the session that triggers it; this entry is the authorisation record and
  is deliberately the LAST commit before the trigger, so branch HEAD and the
  deployed SHA are the same object.

  ---

  **CORRECTION, same entry, written 2026-09-14 after the trigger. THE ACTION-
  WINDOW RULE WAS VIOLATED. The paragraph above that says the authorising read
  "is taken INSIDE the action window" is FALSE for this deployment. It is left
  standing, uncorrected in place, because it is what was claimed; this block is
  what happened.**

      authorising read            2026-09-14T00:03:48Z
      deployment created          2026-09-14T00:28:08Z
      elapsed                     24m 20s  (1460 s)
      CUTOVER_MAX_AGE_S              60.0 s
      therefore                   the authorisation had EXPIRED ~24x over

  AND THE STATE CHANGED IN THE GAP, which is independently an abort condition
  under rule 3 of the action-window rule:

      00:00:12Z   total_unmatched_usd  2.8806     <- the authorising reading
      00:11:38Z   total_unmatched_usd  3.3702
      00:13:39Z   total_unmatched_usd  3.7463
      00:22:26Z   total_unmatched_usd  4.4577     <- last reading before action

  `unmatched_usd` moved +$1.5771 as EXTENDs filled on
  `aec-wta-cribuc-biaand-2026-09-13`. What did NOT change: `held`,
  `risk_bearing`, `n_risk_bearing` (1), `acquisition_slots_in_use` (0),
  `open_orders_ours`/`open_orders_foreign` (0/0). So one of the five watched
  fields moved and four did not — and rule 3 says ANY of the five aborts.

  CAUSE, not excuse: the Railway MCP transport disconnected during the
  `set-variables` call and the write landed on reconnect. The operator did not
  re-read before the write and did not notice the gap until reading the
  deployment's own `createdAt` afterwards. A transport that stalls between
  authorisation and action is exactly the case `CUTOVER_MAX_AGE_S` exists for,
  and the correct behaviour was to re-read and re-authorise — or abort — rather
  than let a queued write land unattended. THE CUTOVER WAS NOT COMPLIANT.

  WHAT MADE IT SAFE ANYWAY — the gates, not the operator, for the second time
  in two deploys:

      00:29:58Z  orphan_sweep    seen 0, cancelled [], foreign [], errors []
      00:30:15Z  boot_assertions ok: true  (11 of 11)
                 no_open_orders_fresh_read {'ours': 0, 'foreign': 0}
                 flat_held_state []      <- quarantine: cribuc-biaand DORMANT
                 combined_loss_above_stop true
                 rung R5FT, band 0.25-0.75, pair ceiling 1.00, streak 3
      00:30:25Z  quarantine_slot quarantined ['aec-wta-cribuc-biaand-2026-09-13']
                 held [same]  acquiring []  concurrency 1
      00:30:31Z  risk_set  n_risk_bearing 1  acquisition_slots_in_use 0
                 total_unmatched_usd 4.4577
                 total_matched_capital_usd 1.690863865546218487394957983

  Those last two figures are byte-identical to the final pre-cutover reading at
  00:22:26Z, so the ledger crossed the restart with no drift. Zero resting
  orders meant nothing was orphaned. None of that makes the procedure correct.

  THE OUTCOME THIS ENTRY PROMISED TO RECORD. Deployment
  `fd496caf-1d11-4423-bf23-547618bf4699`, build SUCCESS (00:28:08Z ->
  00:29:56Z), serving commit `75edc4bd` — verified from the probe's OWN rows
  (`"commit":"75edc4bdcbe3"`), not from the branch name. One executor;
  `pmus-q1-executor` untouched and idle. Volume `/state/pmus-spring` intact. No
  restart or crash loop: one boot, one sweep, no traceback. Rollback `8606c87f`
  / `718ca492` unused and still available.

  THE INSTRUMENTATION WORKED ON THE FIRST CYCLE. Within four minutes, 8 of 8
  COMPLETE orders carried `cross_shadow_live`, four of them with an OBSERVED
  executable depth — against 0 of 589 order rows before this change. Four rows
  reached the PRICE TEST, which no row in the entire 260-decision pre-registered
  corpus ever did. Every row reported `would_submit: false`, arms `["0.97"]`,
  and `live_pair_ceiling_unchanged: "1.00"`. The depth mapping was confirmed
  live against a real residual of -13.39 (holding DOWN -> COMPLETE buys UP ->
  `ask_qty`). No taker order was placed; every order was post-only maker.

  AND THE FIRST FINDING IS ABOUT THIS FILE'S OWN BLIND SPOT.
  `authenticated_age_s` read 0.9 s while `age_at_probe_receipt_s` read 7.08 s
  and 9.23 s — a `probe_transport_delta_s` of 6-12 s on the status fetch. Under
  the old instrumentation those quotes looked contemporaneous. Nothing is
  concluded from eight observations; it is recorded because it is the reason
  the field was added.
