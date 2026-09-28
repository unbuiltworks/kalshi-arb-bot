# Rung-A activation — ABORTED AT STEP 1. No build, no pin, no mandate, no order.

Owner approval received 2026-09-28 for artifact `e48960f7`, envelope $1.00.
Step 1 is "re-establish current Railway state first ... Do not rely on the
previous report." Three of the owner's own abort conditions were hit during that
step, so execution stopped there under "abort without improvisation."

Nothing was built, pinned, redeployed, restarted, armed or activated. No
variable was written. No mandate was created. `e48960f7` is untouched.

## Authoritative state established (project `arb-ev-wallet-shadow`, env `production`)

Target service `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`):

| property | value |
|---|---|
| latest deployment | `98d39cae`, SUCCESS, 2026-09-26T12:15:54Z |
| deployed commit | **`4b5b0e84`** on branch **`feature/l5-transfer-backtest`** |
| deployed Dockerfile | **`deploy/l5_live_probe/Dockerfile`** |
| start command | `python3 -m scripts.l5_pmus_live_oneshot` |
| restartPolicyType | `NEVER` (already correct) |
| replicas | 1, us-east4-eqdc4a |
| volume | `l5-live-probe-state` at `/state/pmus-ws` |
| staged env patch | `patch:cdf9a924` present but EMPTY (`changes: []`, non-destructive) |

The serving artifact is the Probe-08 read-only recovery build, **not** `e48960f7`,
and it is pinned to a different branch and a different Dockerfile than the
reviewed `deploy/l5_pmus_lock5_probe/Dockerfile`. Expected — step 2/3 exist to
change it — recorded so the change is explicit rather than assumed.

## ABORT 1 — the HALT sentinel does not gate this artifact

`HALT` is the environment variable `L5_LIVE_HALT`, read at
`src/l5_pmus_live_runner.py:247`. That file is **not in the probe's 44-file
reachable capability graph**, and `L5_LIVE_HALT` is read by **none** of the 44
files. It is also **not set** on `l5-live-probe-oneshot`.

Consequences for the approved plan:

- **Step 5** ("HALT removal ... separately recorded act") has nothing to remove,
  and removing it would grant no authority it is currently withholding.
- **Step 9** ("Reinstall HALT after the probe and stop") would install a
  sentinel this artifact never reads. That is worse than a no-op: it would leave
  a recorded safety act that does not do what its record says. The things that
  actually stop this artifact are the arm triple (`PMUS_Q1_LIVE=YES` +
  matching `PMUS_Q1_SESSION_ID`/`PMUS_Q1_ARM`, `src/pmus_auth.py:47`), the
  mandate, and `restartPolicyType NEVER`.

I am not resolving this myself. Adding a HALT check would modify the artifact
**after** you approved `e48960f7`, breaking the `strategy_version` pin — the
exact divergence between "the thing we reviewed" and "the thing that ran" the
pin exists to prevent.

## ABORT 2 — a second credentialed process is live on the same BTC15m lane

`l5-pmus-live` (`87f1834e-530e-4fc4-a310-91c8d3add196`) is **running now**. Log
lines at 2026-09-28T03:13:40Z show it evaluating
`cpc-btc-updown-15m-2026-09-28-0300z` — the current BTC15m window — on an
authenticated WebSocket book, minting client order IDs and producing order
intents continuously.

Every one is refused: `"reason":"NOT_ARMED"`, `"submitted":false`,
`"armed":false`. It is **not** trading. But it holds `PMUS_API_KEY_ID` and
`PMUS_API_SECRET_KEY`, it holds its own `PMUS_Q1_ARM` / `PMUS_Q1_LIVE` /
`PMUS_Q1_SESSION_ID`, it is a **continuous** runner rather than a one-shot, and
it targets the same series the Rung-A probe would bind.

Step 1 requires verifying "no other process can trade this lane." On the
evidence, no other process *is* trading it, but I cannot certify that none
*can*: that is one variable away on a process that never stops.

## ABORT 3 — arm-variable values cannot be read, so disarmament cannot be verified

The Railway MCP returns `valuesRedacted: true` for both services. I can enumerate
`PMUS_Q1_ARM`, `PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID` on each, and cannot read
either value. So I cannot verify from source that:

- `l5-live-probe-oneshot` is currently disarmed (no prior arm token left set); or
- `l5-pmus-live` is disarmed other than by inference from its log lines.

For `l5-pmus-live` the log evidence is strong — `armed:false` on every decision.
For `l5-live-probe-oneshot` there is no running process, so there is **no
evidence either way**. Arming it means setting the triple on a service whose
current arm state I could not establish; if a stale token is present, the arming
act is not the act it appears to be. That is "inability to establish
authoritative state."

## What I did NOT do

No image built. No service reconfigured. No branch or Dockerfile path changed.
No variable set, changed or cleared. No mandate file created. `HALT` not touched.
No deployment, redeploy or restart of any service, including the read-only
collectors. The $0.49320 remainder untouched and still without authority.

## What unblocks this, without changing the artifact or the envelope

1. **Decide the HALT question.** Either (a) accept that for this artifact the
   stop is the arm triple + mandate + `NEVER`, and restate steps 5 and 9 in
   those terms — no code change, `e48960f7` stands; or (b) require a real
   `L5_LIVE_HALT` gate in the probe, which is a **new artifact** needing its own
   review and a re-pinned `strategy_version`. (a) costs nothing and loses no
   safety; (b) is more honest to the original wording and costs a review cycle.
   My recommendation is (a).
2. **Quiesce `l5-pmus-live` for the probe window**, so exactly one process can
   reach this lane. Stopping it is a change to a running service and needs your
   say; I have not touched it.
3. **Give me a way to verify the arm variables**, or set/clear them yourself. If
   you clear `PMUS_Q1_ARM`/`PMUS_Q1_LIVE`/`PMUS_Q1_SESSION_ID` on
   `l5-live-probe-oneshot` and tell me the session id you will arm with, the
   arming act becomes verifiable from the probe's own preflight output rather
   than from a value I cannot read.

Steps 2–9 remain unstarted and are unchanged. Rung B is not in scope.
