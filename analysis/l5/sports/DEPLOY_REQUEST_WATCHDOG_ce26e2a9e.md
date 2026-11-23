# Deployment request 1 of 2 — watchdog (no orders, no arming)

Prepared 23 Nov 2026. **Not approved, not deployed.** This request covers
the watchdog service only. It places no order, arms nothing and changes no
trading limit. It is separate from the sports request and from any drill
with a live order.

| Item | Value |
|---|---|
| Service | `l5-halt-watchdog` |
| Running now | commit `faebeca6f6779f8a7f62a62a5bb52c44f234666f`, branch `fix/l5-watchdog-lane-clock-skew-20261117`, deployed 19 Nov |
| Candidate | branch `release/l5-halt-watchdog-ce26e2a9e`, commit `ce26e2a9eeb6c95e534e34f907686b1948b640e2` |
| Change | one source file (`src/l5_watchdog.py`), one new test file, one line in the build trigger |

## What changes

Today, when a bot's whole process dies, its registry is unreachable and the
watchdog cancels nothing it left resting (shown in a two-process drill).

- **Memory.** When a registry cannot be reached at all, the ids of its last
  good, fresh claim still count as claimed for a bounded time. The venue's
  own record must still agree on the market and show an automatic order. A
  registry that answers is never overridden.
- **Speed.** A registry unreachable for 30 seconds is managing nothing, so
  its remembered orders are selected at once instead of after the 300-second
  age rule. Worst case after a death: about 50 seconds.

Both are off unless their settings are present. With neither present the
candidate behaves exactly as the running watchdog.

## Final checks done

- The 20 test files that touch the watchdog: 493 passed on the running
  commit, 504 on the candidate (the 11 new tests).
- Image check: the entry module imports with only the 12 paths the
  Dockerfile copies; no missing import.
- Two-process kill drills, orders 900 seconds old and 20 seconds old:
  nothing cancelled without the change; the dead bot's two orders cancelled
  with it; another desk's order on the same account left alone both times.

## Steps, each needing this approval

1. Connect the service to the candidate branch and commit. The build
   trigger line changes, so it builds. No setting is touched.
2. Verify from logs: sweeps every 20 seconds, the same fields as before,
   three registries OK, zero open orders, no halt.
3. Add `L5_WATCHDOG_REMEMBER_CLAIMS_S=900` and
   `L5_WATCHDOG_DEAD_AFTER_S=30` (two new setting names; the service
   restarts).
4. Verify again as in step 2.
5. No-order check: when the sports bot is next restarted for its own
   release, the watchdog should report its registry unreachable for the
   restart and cancel nothing (there is nothing to cancel).

**Not proven by this request:** that the watchdog cancels a real resting
order after a real death. That is the death drill, which needs its own
exact authorization because it rests a live order.

## Rollback

Reconnect the service to `fix/l5-watchdog-lane-clock-skew-20261117` at
`faebeca6f`, or remove the two settings (which alone restores the running
behaviour).
