# Authorization for live execution

Two files, two separate decisions, deliberately not sharing a switch.

**`HALT`** — its presence stops the service. `shutdown_requested()` is consulted
before `armed()`, so the process enters `safe_shutdown`: cancel our outstanding
orders, verify against a fresh venue read, preserve held inventory to
settlement, never place, never liquidate. Removing it does **not** authorize
trading; it only un-halts the process.

**`live_mandate.json`** — the authorization to spend. Its **absence is the safe
state**: no file, no mandate, no orders. The schema is every field in
`pmus_live_mandate.REQUIRED_FIELDS`, all of them required, because a missing
limit must never fall back to a default. A default is how $0.05 became $7.50.

Both are COPYed into the image as a directory, so adding an authorization is a
reviewed commit rather than a Dockerfile edit.

## Why these are files and not Railway variables

On 2026-09-16 the probe had to be stopped and the only lever that worked was a
Railway `startCommand` exporting `PMUS_SPRING_SHUTDOWN` — the same class of
runtime override that condemned the legacy probe — because `set-variables` was
not offered to the session and `redeploy` had no snapshot to copy. A halt that
depends on a platform API being reachable is not a halt.

A variable also has no diff and no history. "How many markets, how many dollars,
until when" is exactly the kind of decision that should be reviewable after the
fact, and a commit is reviewable after the fact.

## A mandate expires, and that is the point

`expiry` is required and re-checked on every campaign pass, not once at boot. An
unattended process stops itself when its authorization runs out. On 2026-09-16
the only thing between a running campaign and eleven markets was a 20-minute
chat reminder that stopped being re-armed — safety cannot depend on a message
being read.

## Example

```json
{
  "authorization_id": "R1B-2026-09-17-01",
  "authorized_at": "2026-09-17T00:00:00Z",
  "service": "l5-r1-probe",
  "strategy": "LANE_A_EXECUTION_MEASUREMENT_PROBE",
  "strategy_version": "V0",
  "max_markets": 1,
  "max_total_markets": 1,
  "max_spend_per_market": "0.05",
  "max_total_spend": "0.05",
  "max_residual": "0.06",
  "max_daily_realized_loss": "1.00",
  "expiry": "2026-09-17T06:00:00Z"
}
```

A market beyond `max_total_markets` needs a **new** `authorization_id`. Nothing
in the running system — continuation, settlement, restart, resume,
`advance_gate`, or a historical rung approval — can enlarge, replace or extend
one of these.
