# Sports bot stall vs the independent watchdog — finding, fix, drill, test design

22 Nov 2026. Nothing here was deployed or armed. Production is unchanged:
`pmus-spring-l5-probe` on `08b1a7c93`, submission locked, zero orders.

## 1. Finding

The watchdog (`l5-halt-watchdog`, commit `faebeca6f`) reads the sports bot's
registry document two ways. Both lists contain the sports registry
(seen in its sweep output, 16:39:31Z):

| Use | Rule in watchdog code | Bound |
|---|---|---|
| Lane exemption (do not age-latch these orders) | readable, `manages_own_resting: true`, fresh `as_of_utc` | 60 s |
| Ownership claim (ids it is allowed to cancel) | readable, fresh `as_of_utc` | 120 s |

- Running build `08b1a7c93`: the stamp is the HTTP request time. A wedged
  trading loop stays exempt for ever. The watchdog does nothing.
- Previous candidate `ca13dc245`: the stamp freezes when the loop stalls.
  The exemption ends, but the ownership claim goes stale too. The watchdog
  latches the account halt and cancels nothing.

## 2. Fix

`29c64d1f5f4062c965e61afd8ddb285675724b0c`
(`release/pmus-sports-rn1-29c64d1f5`) = `ca13dc245` + one commit. When the
loop has made no progress for 120 s the registry reports
`manages_own_resting: false`, `loop_stalled: true`, keeps a fresh stamp and
still lists the bot's order ids. Exemption ends; claim stays valid.
It does not contain the reward-aware controller change (that is only on
`dev/pmus-rn1-replica`, head `e39a97a0b`).

Regression at `29c64d1f5`: sports 1,946 passed / 6 failed; full 5,082
passed / 24 failed. Same 6 and 24 failures as every release today.

## 3. Local drill (done)

Real watchdog code from `faebeca6f` in one process, real bot registry code
in another over HTTP, a fake venue with two bot orders 15 minutes old and
one old automated order belonging to nobody we know.
Scripts: `watchdog_drill/registry_side.py`, `watchdog_drill/watchdog_side.py`.

| Build | Loop alive | Loop wedged |
|---|---|---|
| `29c64d1f5` code | both orders exempt, no halt | halt `UNMANAGED_RESTING_ORDER`; both bot orders cancelled; the unknown order left alone (`NOT_IN_OWNERSHIP_REGISTRY`) |
| `ca13dc245` | both orders exempt, no halt | halt latched; `CLAIM_STALE:125.1s>120.0s`; nothing cancelled, all three still resting |

This proves the two code bases agree. It does not prove production wiring,
credentials or the venue's cancel path.

## 4. Expected timing in production

Stall detected 120 s after the loop's last progress. The watchdog sweeps
every 20 s and latches only on orders older than 300 s. So from the moment
the loop wedges: about 140 s for an order already older than 3 minutes,
up to about 320 s for one placed just before the wedge.

## 5. Production stall test (design only — needs separate approval)

There is no way to wedge only the trading loop from outside the process, so
the test needs a small drill switch in the bot. It is not written.

Drill switch (to build): `PMUS_TWO_SIDED_STALL_DRILL=window:<startZ>/<endZ>`,
maximum 10 minutes, ignored unless well formed. Inside the window the loop
thread sleeps without reporting progress; the registry thread keeps
serving. At the end the loop resumes and its normal lock handling runs.

**Stage 1 — no orders, no money.** Locked deploy of the build with the
switch; set a 5-minute drill window; bot stays locked with zero orders.
- Pass: within about 140 s the watchdog's lane note for the sports registry
  changes from `OK:0` to `NOT_A_MANAGED_LANE`, the registry stays `OK` in
  its ownership list, no halt is latched, and both revert after the window.
- Needs approval as a deployment and a variable change, not as trading.

**Stage 2 — one live order.** Requires explicit live authorization.
- One market, one post-only 2-contract order (about $1 at risk), every
  existing limit unchanged, 10-minute submission window.
- The drill starts once that order is acknowledged.
- Pass: the watchdog latches `UNMANAGED_RESTING_ORDER` and the venue shows
  the order cancelled within about 340 s of placement, with no session
  action; no other order on the account is touched.
- Fail: order still resting at 400 s. The drill window then ends by itself,
  the loop resumes and cancels it; setting the lock to `1` also cancels it.
- Costs: the account halt stays latched until the reset procedure is run
  (BTC services are monitor-only, the ledger service is shadow-locked). If
  the order fills first, 2 unpaired contracts are held to settlement.

## 6. Not covered

If the whole bot process is gone its registry is unreachable. The watchdog
latches the halt and cancels nothing, as designed for a shared account.
Railway restarts the process; under a lock the bot cancels its own orders
on boot, inside an armed window it resumes managing them.
