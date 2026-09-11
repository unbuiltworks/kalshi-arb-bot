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
