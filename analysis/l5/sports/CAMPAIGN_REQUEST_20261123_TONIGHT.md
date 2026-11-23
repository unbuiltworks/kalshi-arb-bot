# Tonight's live campaign — terms for authorization

23 Nov 2026, 16:00 UTC. **Not authorized.** Depends on the death drill
(16:30 UTC) and, if the watchdog latches the halt, on a separately approved
halt reset.

## What it is

The 22 Nov design, which is the only one with a venue reward record ($1.30
pending on two Serie A markets at this size), run on the new build with the
new protections on, in-play and pre-game, measured by phase. No size or
limit increase.

## Terms

| Item | Value |
|---|---|
| Build | `62dca41cd7167fab117753a2f010fddffd95d55c` (running now) |
| Window | 19:40 UTC 23 Nov to 01:40 UTC 24 Nov (six hours, one campaign, automatic relock at the end) |
| Markets, by pin, both sides | `atc-sea-mon-fio-2026-11-23-fio` (Serie A, kick-off 19:45); `aec-nba-por-phi-2026-11-23` and `aec-nba-mem-cha-2026-11-23` (NBA, tip-off 00:00) |
| Markets at once | 2 |
| Order size | 2 contracts a side |
| Quotes | maker-only, each side on its own marginal test against the international price, pair cap 0.97, taker off, hysteresis off |
| Price band | production's 30c to 70c, unchanged |
| Per-market spend / unpaired caps | $8 / $3, unchanged |
| Campaign spend / unpaired caps | $16 / $6, unchanged |
| Loss stop / daily halt | $4 / $5, unchanged |
| Capital committed | at most $16; about $3.90 resting at a time |
| Maximum settlement loss | $6: the campaign's cap on unpaired exposure, which counts resting bids. A completed pair cannot lose |
| What the loss stop does | stops new exposure at $4 at risk; it does not undo what is already held |
| Competitions | NBA (already permitted) and Serie A for this window |

Protections switched on for the first time in a live window: pre-game and
in-play slate with the kickoff hold and re-check; price-move pause (3c in
30 s, calm 60 s) and in-play size halving; late-fill latch at 30 s; audit
latch; order-service limit of 45 s for the watchdog; portfolio cap of $10 on
resting orders plus unpaired inventory.

## The risk-budget decision this needs

The $3 limit is unchanged and $1.33 is left under it. This campaign's
maximum settlement loss is $6. It can run only if the owner authorises $6
of maximum settlement loss for this one window, as was done for the 22 Nov
window. The sports bot's account check stays on observe for the window;
enforcing it would stop the first two-sided quote.

## What it will and will not show

- Fills, markouts, resting time and reward accrual, separately for
  pre-game and in-play, on the new build.
- A Serie A in-play reward comparable to 22 Nov ($0.52 and $0.78 a market)
  if the venue's programme behaves the same. NBA pools are out of reach at
  this size, so expect no NBA reward.
- It will not show profitability on its own: 22 Nov lost $2.40 trading on
  Serie A for $1.30 of pending reward. The new fair-value rules and pauses
  are aimed at that loss; this is the first live measure of them.

## Reporting

After the window, in the owner's format: build and mode; boundaries;
orders submitted, accepted, rejected, cancelled, filled; inventory;
capital committed and unpaired; spend and loss counters; venue-confirmed
settled result; credited and pending rewards kept apart; total realized
net; watchdog and reconciliation status; blockers.
