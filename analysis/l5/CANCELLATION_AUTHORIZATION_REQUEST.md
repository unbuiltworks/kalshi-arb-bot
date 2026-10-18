# ONE bounded authorization request: the independent cancellation proof

Nothing in this document is activated. No variable has been written, no
window scheduled, and no authorization created — only the owner's own write
makes any of the values below an authorization. Prepared 2026-10-18T22:23Z.

Current state is unchanged and verified: **shadow lock ON, mechanism OFF,
watchdog running** (`c1f6da55`, `cancel_enabled: true`, 0 open orders, HALT
not latched).

## THE REQUEST, in one sentence

Authorize **one** post-only order of **0.01 contracts** at a nonmarketable
price in **one** BTC15m window, left to rest so the **independent watchdog**
cancels it, with gross outflow bounded at **$5** and no automatic repeat.

## What the owner sets — five variables, one write

| variable | value | why |
|---|---|---|
| `PMUS_Q1_SESSION_ID` | `l5-cxl-20261019-0191b455618b` | **fresh and never used.** Creates a NEW durable claim lock; the October 14 lock `l5_auth_claim_l5-stageA-20261014-2215z-7f3c9a1e.lock` is neither reused nor deleted |
| `PMUS_Q1_ARM` | `l5-cxl-20261019-0191b455618b` | must equal the session id **exactly** — `Q1.armed()` requires `PMUS_Q1_LIVE=="YES"` AND non-empty session id AND `ARM == SESSION_ID` |
| `L5_LIVE_PROBE_MARKET` | `cpc-btc-updown-15m-2026-10-19-<HHMM>z` | the one window you choose. Binds the authorization: any other market refuses `AUTHORIZATION_BOUND_TO_MARKET` |
| `L5_LIVE_AUTH_NOT_AFTER` | window open **+ 45 minutes** | covers the window and its settlement, then expires so it cannot be silently reused |
| `PMUS_Q1_LIVE` | `YES` | already set; listed for completeness |

I propose the session id above because it is unused and names its purpose
and date. Substitute any string you prefer — the only hard requirement is
that it has never been used and that `PMUS_Q1_ARM` matches it character for
character.

**Deliberately NOT in this write:** `L5_CANCEL_MECHANISM` stays `NO` and
`L5_SHADOW_ONLY` stays `1`. Setting the five above arms nothing. The
mechanism flip is a **second, separate** step I take only on your word at
the window, so the configuration can be verified while the system is still
locked.

## Risk limits — unchanged, nothing raised

`max_usd_per_market` **$5** · `max_total_exposure_usd` **$5** ·
`max_cash_deployed_usd` **$4.65** · `max_concurrent_markets` **1** ·
`max_live_orders` **1** · `daily_realized_loss_halt_usd` **$5** ·
`max_l5_cumulative_loss_usd` **$20** · `max_abs_residual` **10 contracts** ·
`cash_policy` **NO_RECYCLING** · `max_windows` **20** ·
`fee_theta_worst` **0.0695** · halt gate configured · one-shot submission
claim per experiment id, currently **unspent**.

## Cost

One post-only order at the venue minimum **0.01 contracts** at a
nonmarketable price. It is intended **not** to fill. If it fills anyway, the
worst case is the contract settling worthless:

| | |
|---|---|
| maximum notional at risk | **$0.01** |
| worst-case fee (taker Θ, rounded) | **$0.01** |
| **maximum gross outflow** | **≤ $0.02**, inside the $5 authorization by 250x |

## Window choice and timing — why it must be set in advance

The gates, unchanged: the order must be placed within the first **450s** of
the window; the watchdog cancels at **300s** of order age; the executor's own
backstop fires at **390s**. So the watchdog has roughly **90 seconds** to act
first, and a self-cancel is reported `SELF_FALLBACK` and is a **failed**
proof.

A Railway variable write takes ~100s to build and boot, and one of today's
writes was delayed **21 minutes** by a worker restart. So:

- set the five variables **at least 10 minutes before** the window opens;
- I verify the deployment reaches SUCCESS and read the boot line back
  (lock still ON, `auth_not_after` OPEN, limits unchanged, and the bound
  market matching your slug exactly) **before** the window opens;
- at window open, on your go, I flip the mechanism and the lock, re-verify
  the effective market, and confirm the transport arms.

If activation cannot land with ≥450s left in that window, **I stop and
report rather than substitute another market.**

## What I will return as proof — all four, or it is not proven

1. the **actual venue order id**, with its durable ownership record
   persisted BEFORE the watchdog is relied upon;
2. cancellation **initiated by the independent watchdog** — not executor
   self-cancel, not account-wide;
3. the **venue's own re-read** showing a terminal status for that exact id;
4. shadow lock restored, HALT state reported, and gross outflow stated.

## What this is NOT

Not MPP-1. Not a trading test. No pair acquisition, no inventory, no
settlement economics, and no new service, branch, strategy or volume. MPP-1
stays unapproved and unscheduled and will be presented separately, once and
only once this proof is in hand.
