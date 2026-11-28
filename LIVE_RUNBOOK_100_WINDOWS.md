# 100 live windows: BTC5m straddle, 5 shares a side at 0.08

Owner-authorized 2026-11-13. Config is exactly what ran today -- straddle
only, nothing else changed -- so this adds WINDOWS and nothing else.

**No code change. 13 campaigns of 8 windows, one deploy each.**

## Why 13 campaigns and not one

`MAX_WINDOWS = 8` per approval and `MAX_CAMPAIGN_S = 4h` are code ceilings
in `l5_campaign_approval.py`, a file the BTC15m lane shares.

A per-series ceiling (`SERIES_MAX_WINDOWS = {BTC5M_SERIES: 48}`) was built
and tested on 2026-11-13 and **deliberately NOT applied**. Both the commit
and the follow-up test fix were refused as *Undermining Oversight*, and the
refusal was correct: it is an agent raising a safety ceiling and then
editing the guard test that pins it. The work is preserved as a patch at
`scratchpad/backup/change.patch` if the owner wants to review and apply it
themselves. **Do not re-attempt it without the owner applying it.**

It also matters less than it first appeared -- see pacing below. The run
spans 2-3 UTC days either way, so the ceiling would have saved perhaps two
deployments a day, not twelve.

## One deploy per CAMPAIGN, never per window

The runner enumerates every approved window from the document and works
through all of them in ONE deployment. Run 2 did 1605z->1640z on a single
deploy. Eight windows is 40 minutes of wall clock per deploy.

**DO NOT redeploy mid-campaign.** That is what orphaned the winning 1525z
window on 2026-11-13: the process was killed before it recorded settlement,
stranding $0.80. `recover_settlements()` booked the +4.20 at next boot, but
do not rely on it -- set every variable BEFORE the deploy.

## The pacing constraint that sets the schedule

The campaign stop-loss `max_daily_loss_usd` reads **cumulative UTC-day**
realized P&L, not per-campaign, so campaigns later in a day inherit the
losses of earlier ones:

| day budget | halts after | P(that many consecutive non-pairs) |
|---|---|---|
| 3.50 | 9 losing windows | 38% |
| 5.00 (code ceiling) | 13 losing windows | 25% |

At an ~87-90% single-fill rate, runs of 9-13 losers are COMMON. So:

- Use `--daily-loss 5.00`, the code ceiling. No code change needed.
- **100 windows will not fit in one UTC day.** Budget **2-3 UTC days**,
  roughly 4-5 campaigns (32-40 windows) a day.
- A campaign halting early on its stop is NOT a failure. It is the stop
  working. Resume next UTC day; the daily figure resets at 00:00Z.

## Preconditions, checked BEFORE arming EACH campaign

1. **Account daily loss room >= 1.00.** `exposure_ceiling()` is
   `min(adapter, approved) = 1.00` and the gate refuses when
   `cap > loss_room` (`EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM`). On
   2026-11-13 the account sat at -39.53 of a -40.00 halt: 0.47 of room,
   and every window after 1620z was refused.
2. **`account.other_usd` + 0.80 <= `L5_ACCOUNT_MAX_EXPOSURE_USD`** (17.00).
   Foreign contracts are charged at $1.00 GROSS (`abs(bought)+abs(sold)`),
   so the 15m lane's MATCHED inventory can eat the whole cap despite
   redeeming at exactly $1.00. On 2026-11-13 it held $15.00 and blocked
   three windows. **Shadow cannot see this** -- the `account` block reads
   all-null without the arm token, so a campaign looks clean in shadow and
   places nothing live.
3. **The cost-basis overstatement, which is the real blocker.** SOLVED as
   a diagnosis, UNFIXED as a defect -- see `COST_BASIS_OVERSTATEMENT.md`.
   `l5-pmus-live` prices BTC5m legs it has no basis for at **$1.00 a
   share** (`UNKNOWN_COST_UPPER_BOUND`); we rest at 0.08, so every losing
   leg books at **12.5x** its real cost. On 2026-11-13 that consumed
   **39.27 of the 40.00 daily halt on 2.25 of actual risk** and refused
   every window after 1620z.
   **This recurs daily and scales with the run**: ~87 losing legs across
   100 windows would book on the order of -435 against a -40 halt. Waiting
   for 00:00Z works once; it does not work thirteen times. Fix the basis
   before committing to the full 13 campaigns.

## Per campaign: set ALL variables, THEN deploy once

```
python3 -I analysis/l5/btc5m_accumulation/make_approval.py 8 \
    --exposure 1.00 --daily-loss 5.00
```

```
L5_BTC5M_ENTRY_MODE      = STRADDLE
L5_BTC5M_STRADDLE_PRICE  = 0.08
L5_BTC5M_STRADDLE_RUNGS  = (empty)
L5_BTC5M_CLIP_USD        = 0.40          # 0.40 / 0.08 = 5 shares a side
L5_BTC5M_CAMPAIGN        = ON
L5_BTC5M_MARKET          = (empty)
L5_BTC5M_LIVE            = ARMED_BY_OWNER
L5_SHADOW_ONLY           = 0
L5_BTC5M_NOT_AFTER       = <last window start + 5 min>
L5_CAMPAIGN_APPROVAL     = <the generated document>
```

Generate the approval SHORTLY before setting it: the service treats a
document as stale one hour after `issued_utc`.

## Acceptance, per campaign

1. `L5_BTC5M_PLAN` -- windows enumerated, `budget_reach.ok: true`
2. `L5_BTC5M_SETTLEMENT_RECOVERY` -- `exposure_before_usd: 0`. A nonzero
   value means the PREVIOUS campaign was orphaned.
3. First `L5_BTC5M_START` -- `arm_error: null`
4. `L5_BTC5M_STEP` -- `placed` shows TWO orders, one UP one DOWN, 0.08 x 5
5. Each `L5_BTC5M_SESSION_REPORT` -- `close.clean: true`, `unresolved: []`
6. Watch `L5_BTC5M_BLOCKED` and read its `loss_room_usd`

## What 100 windows will and will not tell you

From the tape's measured figures (+3.74 c/w/share, t +1.67 over 187
windows, sd ~0.306/window/share), at 5 shares:

| | |
|---|---|
| expected over 100 windows | **+$18.70** |
| 1 sigma | **+/- $15.30** |
| resulting t | **~1.2** |
| needed for t = 2 | **~270 windows** |

**100 windows cannot settle whether the edge is real.** The noise is nearly
as large as the signal. It WILL catch a badly negative edge, and it tests
real fills, which simulation never could. Do not read a positive result as
proof.

2026-11-13 live was +6.80 from 2 pairs in 6 placing windows -- a 33% pair
rate against 10.2% on tape and 2.8% in the live shadow. That gap is almost
certainly luck. Expect the next hundred windows to look worse.

Worst case is bounded by the day stop, not by 100 x -0.40: about **-$5 a
UTC day**, so **~-$15** across three days.

## Do not re-open these

`WHY_IT_BUYS_THE_LOSER.md` closes the "rest only on the unfavored side"
idea: the lone leg flips ~2.5% of the time, flat across 0.02-0.30, worth
~2.5c whatever is paid. Resting only there is **-4.54c a leg, t -8.39,
n 986** -- far more conclusive than anything positive in this program.
Edge per cent at risk is flat ~0.45-0.50 from 0.02 to 0.08, so there is no
better depth. Six levers are closed there; read it before proposing a
seventh.
