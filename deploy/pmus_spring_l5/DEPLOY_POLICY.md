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
