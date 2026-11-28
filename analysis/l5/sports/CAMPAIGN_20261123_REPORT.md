# Campaign report — 23 Nov 2026, wide slate (stopped early)

Source: production logs of the sports bot, the account ledger and the
watchdog, read between 17:27 and 19:10 UTC. Venue cash and venue fills are
the accounting basis. Markouts are marks, not profit. Reward rows for
23 Nov are not posted yet; nothing here counts a reward.

## Decision: adjust

Do not run another extended window on this build. The trading result was
small and positive, but the window was stopped by an operational defect,
not by the market, and three defects have to be fixed first (see
"What has to change"). Nothing here supports a larger size or wider limits.

## Result

**Venue-confirmed: +$0.33.** Seven completed pairs bought for $6.67 and
netted by the venue to $7.00. No unpaired inventory. No fees, no rebates,
no reward credited or verified.

| Item | Value |
|---|---|
| Build | `62dca41cd` (code `c337c3aef`), maker-only, taker off, 2 contracts |
| Authorised window | 17:15:00 to 23:15:00 UTC |
| Actually traded | 17:15:03 to 18:56:56 UTC (last order); last fill 18:31:48 |
| Stopped | plain lock set about 18:52; effective 19:07:31 (see below) |
| Orders sent | about 256 (in-play about 97, pre-game about 160) |
| Fills | 9 fills, 14 contracts, all in-play ATP tennis |
| Pre-game fills | none |
| Pairs | 7; average cost $0.953; locked +$0.33 |
| Inventory at the end | flat of unpaired contracts; no resting orders |
| Venue cash | $319.8038 before, $320.1338 after: +$0.33, all explained |
| Ledger booking so far | +$0.10 (Strombachs–Perez settled); the rest books as markets settle |
| Rewards | not verified: both reward reads (17:47, 18:47) were deferred |
| Account drawdown figure | $1.67 before, peak $2.99, $1.34 after (limit $3.00) |

## By market

| Market (ATP, in play) | Fills | Pairs | Pair cost | Result |
|---|---|---|---|---|
| Strombachs–Perez | 2 | 2 | $0.95 | +$0.10 |
| Karpol–Benwei | 2 | 1 | $0.96 | +$0.04 |
| Debru–Kopp | 5 | 4 | $0.9525 | +$0.19 |

Soccer (Serie A, La Liga, Premier League), NBA and NHL markets were quoted
pre-game only and never filled. No soccer market reached kickoff before the
stop. No NFL market was quoted.

## Fills and marks

Per contract, value of the side bought against its fill price.

| Fill (UTC) | Market | Side | Qty @ price | Role | 5 s | 30 s | 2 min | 5 min |
|---|---|---|---|---|---|---|---|---|
| 17:15:26 | Strombachs–Perez | YES | 2 @ 0.66 | open | −1.5c | −1.5c | +0.5c | −16.5c |
| 17:33:25 | Strombachs–Perez | NO | 2 @ 0.29 | complete | −2.5c | −4.0c | +3.5c | −3.5c |
| 17:33:54 | Karpol–Benwei | YES | 1 @ 0.65 | open | +0.5c | +2.5c | −2.5c | −2.5c |
| 17:34:37 | Karpol–Benwei | NO | 1 @ 0.31 | complete | −0.5c | +1.5c | +6.5c | +6.5c |
| 17:54:10 | Debru–Kopp | YES | 2 @ 0.66 | open | −0.5c | +2.5c | −0.5c | −7.5c |
| 18:02:38 | Debru–Kopp | NO | 2 @ 0.31 | complete | −0.5c | −1.5c | +4.5c | +3.5c |
| 18:10:03 | Debru–Kopp | YES | 2 @ 0.61 | extend | −0.5c | −1.5c | −16.5c | −14.5c |
| 18:27:32 | Debru–Kopp | NO | 1 @ 0.33 | complete | −1.5c | −1.5c | −1.5c | −2.5c |
| 18:31:48 | Debru–Kopp | NO | 1 @ 0.32 | complete | −1.5c | −1.5c | −8.5c | +3.5c |

Mean 30-second mark: −0.6c over the nine fills.

**Fill outside 30c to 70c:** one, the NO at 0.29. It completed a pair at
$0.95, so its edge is the 5c locked on that pair. The bot also rested
unfilled bids at 0.24 and 0.27 on that market: the 30c to 70c band is
checked when a market is selected, not on each quote.

## The three questions

**1. Which markets made money after rewards, rebates and fees?** The three
in-play tennis markets, +$0.33 together, with no fees and no rebates.
Rewards are unknown until the venue posts rows for 23 Nov. Pre-game quoting
made nothing and lost nothing.

**2. Inventory quality against adverse selection.** Every fill ended in a
pair, which is why the result is positive. The single fills were not good:
two of the three opening or extending fills were marked 14c to 17c down
within five minutes. Had either stayed unpaired and lost, it would have
cost about $1.25, four times the evening's result. Nine fills is too few
to call the method profitable.

**3. Operational reliability.**
- Clean: no safety latch, no halt, no taker order, no order above two
  contracts, no venue rejection, no fill seen more than 6 seconds late.
  Watchdog sweeps every 20 seconds throughout, nothing cancelled. Kickoff
  hold and resume worked on one tennis market. Ledger venue reconciliation
  clean on sports markets at each read apart from short-lived drift on
  just-paired contracts.
- Not clean:
  - **The account audit never ran.** Both hourly attempts were deferred by
    the one-request-a-second budget, and the code does not retry. This is
    what ended the campaign (owner's rule, authorization section 16).
  - **The lock took 15 minutes.** Set about 18:52; the platform created the
    new deployment at 19:06:43. Six more pre-game orders went out in
    between (none filled). A settings change is not an immediate stop.
  - **The $16 spend ceiling refused bids from 18:04** with $6.02 of fills.
    What it counts is not yet confirmed.
  - **The account figure reached $2.99 of $3.00** on one unpaired
    two-contract leg. The bot's account check was on observe; it returned a
    refusal on about half its readings.
  - The ledger could not read the sports bot's orders from 17:20 on, so its
    own exposure gate read "unknown" all evening.
  - Heavy churn: about 256 orders for 9 fills. A missing international
    price was the most common reason a side was not quoted. In-play tennis
    was paused for most decisions. 61 trading requests were deferred by
    the budget by 18:22.
  - The old process stopped quoting at 18:59:19 with
    `pilot_meter_unavailable`; cause not established.

## What has to change (owner's order)

1. Reserved request capacity for account-risk reads, the audit and
   reconciliation, ahead of quote traffic; a deferred audit retries.
   Regression-tested before another extended live campaign.
2. The spend ceiling: establish exactly what it counts; fix only a
   confirmed defect. No limit raised to compensate.
3. Adverse selection: whether the marked-down fills came during fast moves
   and whether the inventory charge deters adding to the same side.
4. Completion-only mode verified independently (completion size can never
   exceed the unpaired quantity) before it is ever used.
5. A stop that does not wait for a redeploy.

## State after the campaign

Sports bot locked (plain lock), same build; allowed competitions back to
NFL and NBA, two markets, in-game slate. No resting orders, no unpaired
inventory. Watchdog and ledger unchanged. No further window is authorised.

## Still to reconcile

- Reward rows for 23 Nov when the venue posts them (paid, pending, skipped
  kept separate).
- Ledger booking of Karpol–Benwei and Debru–Kopp at settlement, and the
  one-contract drift it shows on Debru–Kopp.
