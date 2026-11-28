# Tonight's live campaign — wider slate, same worst case (for authorization)

23 Nov 2026, 16:20 UTC. **Not authorized.** Depends on the death drill
(16:30 UTC) and, if the watchdog latches the halt, on a separately approved
halt reset. Supersedes the three-market draft of 16:00 UTC.

## The change from the earlier draft

Owner, 16:10 UTC: "can we loosen the filters? ... I need to go full tilt."
The international price check stays: fills in markets with no outside price
lost 2.7c to 3.9c each last week. What is loosened is everything around it.
Over the next 12 hours 31 games have an in-band market with an international
price: 19 ATP, 4 NHL, 3 NBA, and one each in Serie A, the Premier League and
La Liga. Only NBA is permitted today.

Breadth goes up. The risk numbers do not.

## Terms

| Item | Tonight's terms | Now |
|---|---|---|
| Build | `62dca41cd7167fab117753a2f010fddffd95d55c` | same |
| Window | six hours from the first quarter-hour at least 20 minutes after the halt is clear and approval is given; automatic relock at the end | — |
| Permitted competitions | NBA, NFL, NHL, ATP, Serie A, Premier League, La Liga | NBA, NFL |
| Soccer leagues the price guard may map | Serie A, Premier League, La Liga | Serie A |
| Market choice | the bot's own ranking among permitted markets; no pin | — |
| Markets at once | up to 6 (under the unpaired cap, four or five quoting both sides) | 2 |
| Phases | pre-game and in-play, with the kickoff hold and re-check | in-play |
| Order size | 2 contracts a side | 2 |
| Price band | 30c to 70c | 30c to 70c |
| Per-market spend / unpaired caps | $8 / $3 | same |
| Campaign spend / unpaired caps | $16 / $6 | same |
| Loss stop / daily halt | $4 / $5 | same |
| Pair cap, taker, hysteresis | 0.97, off, off | same |
| Capital committed | at most $16 | — |
| Maximum settlement loss | $6: the campaign's cap on unpaired exposure, which counts resting bids. The bot opens nothing new once it is reached | — |
| Loss stop | stops new exposure at $4 at risk; does not undo what is held | — |

Excluded as before: cricket, table tennis, Dota 2, and any market with no
international price.

Protections on for the window: price-move pause (3c in 30 s, calm 60 s) and
in-play size halving; late-fill latch at 30 s; audit latch; order-service
limit of 45 s for the watchdog; portfolio cap of $16 on resting orders plus
unpaired inventory.

Optional, the owner's call: restore the band to the stated 25c to 75c. It
adds a handful of markets and no other change.

## The risk-budget decision this needs

The $3 limit is unchanged and $1.33 is left under it. The maximum
settlement loss here is $6, so it can run only with the owner's explicit
authorisation of $6 for this one window, as on 22 Nov. The sports bot's
account check stays on observe for the window.

## What it will and will not show

- Fills, markouts, resting time and reward accrual by phase across several
  competitions at once, on the new build.
- It is the first live run of ATP, NHL, the Premier League and La Liga
  under the marginal test. There is no fill history for them on this build.
- It will not by itself show profitability. 22 Nov lost $2.40 trading on
  Serie A for $1.30 of pending reward.

## Reporting

After the window, in the owner's format, with credited and pending rewards
kept apart and nothing unrealized called profit.
