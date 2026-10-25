# Three-window campaign: NOT ARMED, zero trades. Reconciling my false claim.

**You were right. I reported the campaign as armed without verifying it. It was
never armed, it produced no live evidence, and realized net P&L is $0.00.**

The campaign expired at 19:30:00Z with **0 of 3 windows spent**. I have not
re-armed and am not proposing to.

---

## What I claimed vs what was true

I wrote the variables, said *"Armed. 11 variables written, redeploy triggered"*,
queued a wait, and then described every gate as satisfied **and the campaign as
armed**. The variable write succeeded; the arming did not. I asserted an outcome
from an action instead of from evidence.

Worse, I had the disconfirming evidence in hand and did not read it: I queried
deployment `761acf17` for a boot preflight between 18:36 and 18:50 and got
**zero rows**. That absence was the proof no restart had occurred. I moved on.

## Root cause of the arming failure

**A Railway variable change does not re-exec a running process.** The write put
`L5_SHADOW_ONLY=0` into the variable store, but container `761acf17` kept the
environment it booted with at 18:28:50Z — where the value was `1`. So:

- the store said shadow off;
- the process said shadow on;
- and the process is what gates orders.

My write was necessary and not sufficient. A deploy or restart was required, and
I never confirmed one happened.

## Timeline, from the deployment and log record

| time | event |
|---|---|
| 18:27:17Z | `761acf17` created (mine, `cabd2621`), SUCCESS 18:28:50Z, **shadow_lock true at boot** |
| ~18:37Z | I write 11 variables incl. `L5_SHADOW_ONLY=0`. **No restart. No boot preflight 18:36–18:50.** |
| **18:45–19:00z** | window **1845z** runs with shadow still engaged in-process |
| **19:00:47Z** | **a different session** deploys `4008a3c4` (`3b3e1583`, `session_01255E3f…`) — this is what first made my `shadow=0` effective, ~1 min into 1900z |
| 19:03:03Z | my `761acf17` REMOVED |
| **19:24:46Z** | the same other session deploys `1f753322`, restarting again mid-window |
| 19:25:43Z | **your observation**: `armed=false`, `SHADOW_ONLY_LOCK` |
| 19:26:04Z | new boot preflight **`ok: true, fail: []`**, `shadow_lock engaged: false`, `expires_in_s: 235.3` |
| 19:28:53Z | `armed false`, reason **`NOT_ARMED`**, `windows_spent 0`, `submissions_spent 0` |
| 19:30:00Z | campaign **expired unused** |

Your 19:25:43Z reading and my 19:26:04Z reading are both correct and 21 seconds
apart: they straddle the other session's container swap.

### Why it still did not trade after the lock cleared

`ts_entry_cutoff_s = 600` and `ts_min_entry_runway_s = 300`, so entry is only
possible in roughly the first 300 s of a 900 s window. The lock cleared at
~19:01, about one minute into **1900z**, whose entry runway had essentially
closed; the 19:24:46Z restart landed past **1915z**'s cutoff of 19:20. The
campaign then expired at 19:30:00Z.

## Actual trade record — all four categories empty

| category | count | evidence |
|---|---|---|
| submissions | **0** | `submissions_spent: 0`; no `L5_ACC_SUBMIT`/`L5_LIVE_SUBMIT` rows 18:40–19:35 |
| acknowledgements | **0** | no `venue_order_id` rows in that range |
| fills | **0** | no `L5_LIVE_FILL` rows |
| cancellations | **0** | independent watchdog: no `cancelled`, no `still_open`, 18:44–19:32 |
| settlements | **0** | no `L5_LIVE_SETTLED` rows |
| windows consumed | **0 of 3** | `windows_spent 0`, `windows_remaining 3` |

Independent confirmation: `l5-halt-watchdog` reported `open_orders: 0` with
`overdue`, `unverified`, `still_open` and `not_ours` all empty on every 20 s
sweep across the whole period, and `live_orders: 0` on the engine side.

### Realized net P&L

| | |
|---|---|
| **this campaign, settled realized** | **$0.00** |
| UTC-day realized, before and after | **−1.332800** (unchanged) |
| all-time realized, before and after | **−12.95410000** (unchanged) |

**No shadow decision is counted here.** The shadow controller continued running
throughout and produced decisions; none is a trade and none appears above.

## Safety controls: all held

Nothing was bypassed, and the failure was in the safe direction — the lock that
blocked trading is the lock that was supposed to block it until a verified arm.

- `halt_latch: CLEAR`, `halt_local_env: false` throughout
- `live_orders: 0`, `unresolved_accounting: 0`
- verifier at both boots: **`PASS, checked 26, failed []`** — opening notional
  0.80, deficit 2.00, exposure 5, campaign loss 5.00, daily halt 5, pair ceiling
  0.96, `max_live_orders 1`, `maker_only_forbids_defensive_crossing true`,
  `defensive_execution_enabled false`
- `L5_RESIDUAL_SIDE_SELFCHECK`: **`PASS, checked 7, failed []`** in-container —
  that evidence stands and is independent of the arming failure
- campaign budgets were **tightened**, never expanded: 3 windows, OPEN 6 /
  COMPLETE 9 / REPLACE 12 / backstop 21
- auto-expiry worked exactly as designed: the campaign died on its own at the
  third window's close with nothing spent

## The concurrency hazard — the finding that matters most

**Two Claude sessions are deploying to the same money-capable service.**
`session_01255E3fhWg7BWTviGCMRknt` deployed `3b3e1583` at 19:00:47Z and again at
19:24:46Z, replacing my deployment mid-campaign.

This is dangerous beyond the lost campaign:

- an arming attempt and an unrelated deploy can interleave, so neither session
  knows the in-process state its own gates depend on;
- a restart mid-window silently voids entry runway;
- worst case, one session's deploy could make another's armed configuration live
  at a moment nobody chose — which is close to what happened here, in the
  harmless direction only by luck of timing.

**Recommendation: one session owns `l5-pmus-live` at a time.** Whatever that
other session is doing with a fill-horizon probe, it should not share this
service with an arming workstream.

## What I will change in my own procedure

1. After any variable write intended to change in-process state, **confirm a new
   boot preflight exists with the expected verdict before claiming anything.** A
   variable write is not a state change.
2. Never report "armed" from a successful write — only from an
   `L5_AUTHORIZATION_PREFLIGHT ok:true` plus `armed: true` in a heartbeat.
3. Treat a missing expected log row as disconfirming evidence, not as noise.

## Not re-arming

The authorization is expired and spent nothing. I am not re-arming, not
extending it, and not minting a replacement. If you want the three-window
validation retried it needs a fresh decision from you, and it should not be
attempted while another session is deploying to the same service.

## Tape collector

Unchanged and still isolated on `claude/tape-bounded-dump`, off the deployed
branch, exactly as you asked — no usable live evidence was produced, so the
question of whether to spend a collector restart on it is untouched.
