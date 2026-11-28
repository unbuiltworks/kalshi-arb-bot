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

---

# CORRECTION, written 2026-11-13 15:1xZ from the live run

Two numbers above were wrong. Both were written from memory rather than
read from the system, and both were checked against source before acting.

## 1. The $55 raise was never needed

Step 1 above says to raise `L5_MAX_L5_CUMULATIVE_LOSS_USD` to 55 because
`loss_room_usd` was `-1.18` against a $50 ceiling. **The ceiling was
actually 20, and the gate already passes.**

Verified without risking money: the campaign was set with
`L5_SHADOW_ONLY=1` and no arm token, which the authorization layer reports
as `SHADOW_ONLY_LOCK: submit() is structurally blocked; arming would
change nothing`. `Session.gates()` calls `risk_fn` on EVERY step and emits
`L5_BTC5M_BLOCKED` (never `L5_BTC5M_STEP`) when it refuses. The probe
produced **20 STEP records and 0 BLOCKED records**, so
`loss_room_usd >= exposure_ceiling()`, which is
`min(adapter ceiling, approved 1.00) = 1.00`.

Do not raise a limit to clear a refusal you have not actually observed.

## 2. The raise would have restarted the live BTC15m trader

The runbook says to set it on "the service `L5_ACCOUNT_RISK_URL` points
at". That is `l5-pmus-live` -- which is **the live BTC15m trader**: 1078
references to `cpc-btc-updown-15m-*` against 210 to 5m, 31
`L5_LIVE_DECISION` records, and at the time it held an open residual
position (1.00 UP @ 0.4500 basis, matched 2.51) having just cleared an
order-state uncertainty at 14:54:59.

Raising the ceiling there is not a BTC5m-local change: it restarts a
money-capable service mid-position and raises the account-wide limit for
the 15m lane too. It belongs to the BTC15m session, not this one.

## 3. The real blocker is the ACCOUNT EXPOSURE CAP, not loss room

Armed at 15:10z the straddle placed **nothing**, refusing every leg:

```
account:  other_position_contracts 15,  other_usd "15.00",  own_usd "0"
L5_ACCOUNT_MAX_EXPOSURE_USD = 8.00
refused x28: ACCOUNT_EXPOSURE_CAP
```

Another lane already holds $15.00 against the $8.00 account-wide cap. Our
own usage is zero. For a $0.80 straddle to fit, other-exposure must fall
to $7.20.

This is the shared cap working correctly. It is a DIFFERENT limit from the
cumulative-loss ceiling the owner raised, and it is account-wide across
every lane, so it is not ours to raise alone.

Owner's call 2026-11-13: first **leave armed, change no limit**, then
**raise `L5_ACCOUNT_MAX_EXPOSURE_USD` 8.00 -> 17.00**. Note the cap is
BTC5m-LOCAL: only `l5_btc5m_execution.py` reads it, so raising it grants the
15m lane nothing -- it raises BTC5m's own refusal threshold, measured
against total account exposure. There is no code clamp (`account_cap()`
accepts any value >= 0).

### RESULT of the armed campaign (approval b5m-12cfb0ae, 1510z-1545z)

| window | outcome | settled |
|---|---|---|
| 1510z, 1515z, 1520z | refused `ACCOUNT_EXPOSURE_CAP` (15m lane held $15 vs $8) | 0 |
| **1525z** | **PAIR -- UP and DOWN both filled 0.08 x 5** | **+4.20** |
| 1530z | UP only filled | -0.40 |
| 1535z | UP only filled | -0.40 |
| 1540z, 1545z | refused `AUTHORIZATION:BTC5M_DAILY_LOSS_LIMIT` | 0 |

**Campaign net +3.40**, all closes clean, no unresolved orders.

Three things this run actually established, none of them flattering to the
tidy story:

1. **The pair came BEFORE the raise, not because of it.** During 1525z
   `account_total_usd` was `7.40` -- under the ORIGINAL 8.00 cap, because
   the 15m lane had just settled. The raise bought windows 1530z and
   1535z, and both LOST. Credit the 15m lane settling, not the cap change.
2. **A redeploy orphaned the winning window.** The cap-raise redeploy
   killed 1525z mid-flight before it recorded settlement, leaving $0.80
   stranded. `recover_settlements()` booked the +4.20 at next boot
   (`already_settled: false`). Without it that win was invisible.
3. **The daily-loss stop fired on a stale figure.** 1540z/1545z were
   refused on `BTC5M_DAILY_LOSS_LIMIT` computed WITHOUT the unbooked
   +4.20, i.e. a day figure $4.20 too pessimistic. The limit worked; its
   input was stale because settlement booking lags an orphaned window.

Sample size: ONE pair in THREE windows that actually placed. The tape says
10.2%. Three windows cannot distinguish 33% from 10%, and this is not
evidence the straddle works.


## What to check first next time

Read `account.other_usd` against `L5_ACCOUNT_MAX_EXPOSURE_USD` BEFORE
arming. In shadow that block reads all-null, so the shadow probe cannot
see this refusal; only an armed step can. That asymmetry is why the
campaign looked clean in shadow and placed nothing live.
