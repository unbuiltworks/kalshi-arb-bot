# Live run: straddle only, 8 windows, 5 shares a side

Owner-authorized 2026-11-13: *"raise to $55, run 8 windows at 5 shares,
straddle only."* Railway's MCP was unreachable from the session, so this is
written to be executed by hand or by a session that can reach it.

## The config, and why each number

| setting | value | why |
|---|---|---|
| mode | `STRADDLE` | the only configuration that ever made money live (+$6.29) |
| price | `0.08` both sides | best on the 189-window tape |
| rungs | **empty** | a ladder is leverage, not diversification |
| clip | `$0.40` | 0.40 / 0.08 = **5 shares a side** |
| exposure | `$0.80` both sides | the campaign cap is set to 1.00 |
| worst window | **-$0.40** | one side fills and loses |
| worst run | **-$3.20** | all eight windows lose |

## 1. Raise the cumulative ceiling to $55

On the service that publishes the risk document (the one
`L5_ACCOUNT_RISK_URL` on `l5-btc5m-live` points at -- check that variable
to confirm which; it runs `l5_pmus_live_runner`):

```
L5_MAX_L5_CUMULATIVE_LOSS_USD = 55
```

Verify afterwards that the risk doc reports
`cumulative_loss_ceiling_usd: 55.00` and `loss_room_usd` is POSITIVE.
It was `-1.18` against a $50 ceiling, so $55 should give about **+$3.82**.
**If loss_room is still negative, stop** -- another lane has lost more
since, and the number needs revisiting rather than forcing.

## 2. Set these on `l5-btc5m-live`, with skipDeploys

Set variables BEFORE triggering any build. Railway snapshots env at
deployment creation, so a build started first comes up on stale values --
that cost two campaigns on 2026-11-12.

```
L5_BTC5M_ENTRY_MODE      = STRADDLE
L5_BTC5M_STRADDLE_PRICE  = 0.08
L5_BTC5M_STRADDLE_RUNGS  = (empty)
L5_BTC5M_CLIP_USD        = 0.40
L5_BTC5M_CAMPAIGN        = ON
L5_BTC5M_MARKET          = (empty)
L5_BTC5M_LIVE            = ARMED_BY_OWNER
L5_SHADOW_ONLY           = 0
L5_BTC5M_NOT_AFTER       = 2026-11-13T15:35:00Z
L5_CAMPAIGN_APPROVAL     = <the JSON below>
```

**Issued 14:47:23Z. STALE at 15:47:23Z.** Windows run 14:55z to 15:30z,
so set `L5_BTC5M_NOT_AFTER = 2026-11-13T15:35:00Z`.

Windows already past when you arm are simply skipped -- the runner takes
the remaining enumerated ones -- so arming at 15:05z gets you six windows,
not eight. That is fine; it is not an error. But if more than about half
have gone, regenerate rather than running a stub:

```
python3 -I analysis/l5/btc5m_accumulation/make_approval.py 8 \
    --exposure 1.00 --daily-loss 3.50
```

```json
{"approved":true,"nonce":"b5m-dcbe0a531dd1750b34444738","series":"cpc-btc-updown-5m-","issued_utc":"2026-11-13T14:47:23Z","expires_utc":"2026-11-13T16:05:00Z","max_windows":8,"max_total_exposure_usd":"1.00","max_daily_loss_usd":"3.50","window_starts":["2026-11-13T14:55:00Z","2026-11-13T15:00:00Z","2026-11-13T15:05:00Z","2026-11-13T15:10:00Z","2026-11-13T15:15:00Z","2026-11-13T15:20:00Z","2026-11-13T15:25:00Z","2026-11-13T15:30:00Z"]}
```

Self-check at generation: campaign planner accepted YES, per-order claim
accepted YES, nothing clamped by code ceilings.

## 3. Then trigger the build

Touch `deploy/l5_btc5m_live/DEPLOY_TRIGGER`, commit, push to
`integration/l5-safe-release-candidate-20261109`. The deployment created by
that push snapshots the variables set in step 2.

## 4. What to check, in order

1. `L5_BTC5M_PLAN` -- 8 markets enumerated, `budget_reach.ok: true`
2. `L5_BTC5M_SETTLEMENT_RECOVERY` -- should be a no-op now
3. First window's `L5_BTC5M_STEP` -- **`submitted: 2`**, one UP and one DOWN,
   both at 0.08, 5 shares each. Anything other than 2 is wrong: 1 means a
   side was refused and the straddle is a directional bet.
4. `L5_BTC5M_SESSION_REPORT` -- `settled_net_usd`, `reconcile.agrees: true`

## What this can and cannot tell us

**CAN:** whether fills arrive as the model assumes. The shadow fills us
whenever the touch reaches our price, with no queue position and no size --
that assumption has never been tested and is the single biggest difference
between simulation and reality. Live settles it.

**CANNOT:** whether the edge is real. Eight windows cannot. At a 10.2% pair
rate, P(no pair in 8) = 43% -- so a blank run is the most likely single
outcome and means nothing either way. Settling the edge needs ~300 windows.

**The honest prior:** the straddle is +3.74 c/w on the tape (t +1.67,
unproven) and NEGATIVE at the live shadow's observed 2.8% pair rate. Every
dollar it has ever made came from pair windows -- 2 of 5 in the +$6.29 run.
If no pair lands in these 8, expect about -$3.20.
