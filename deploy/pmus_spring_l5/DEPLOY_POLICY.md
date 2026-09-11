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
