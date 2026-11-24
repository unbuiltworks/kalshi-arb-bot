# Campaign report — 24 Nov 2026, ATP window 01:20–02:20 UTC

Source: production logs of the sports bot, the watchdog and the account
ledger, read at 01:25, 01:54 and 02:27 UTC. Venue cash and venue fills are
the accounting basis. Markouts are marks, not profit. No reward row for
this date exists yet.

## Verdict

**Risk controls: passed. Execution: failed.** One pair in sixty minutes.
The bot quoted for six minutes, abandoned all three markets, sat idle for
45 minutes, and traded once more only because a new match came into band.
The cause is a defect that is now fixed in the release candidate, not
deployed.

| Terms | ATP only, strict scope; in-play; up to 3 markets; 1 contract; maker-only; $3.00 account limit enforced; build `e6e8b4e60` |
|---|---|

## 1. Venue-confirmed fills and inventory

| Time (UTC) | Market | Side | Qty @ price | Role | Order age at fill |
|---|---|---|---|---|---|
| 02:14:09 | Cruz–Guimarães (`aec-atp-cescru-joagui-2026-11-23`) | YES | 1 @ 0.31 | open | 22 s |
| 02:14:54 | same | NO | 1 @ 0.66 | complete | 5 s |

- One pair, cost $0.97, locked $0.03. Inventory at the end: 1 YES + 1 NO,
  paired. No unpaired contract at any check.
- 29 orders in the window: 25 between 01:20 and 01:26 on three markets,
  4 between 02:13 and 02:15 on a fourth. (An earlier message said 32 for
  the first six minutes; the count from the logs is 25.)
- The opening fill came on a two-tick jump against it, like every opening
  fill on 22 and 23 Nov.
- The YES fill at 0.31 is at the edge of the 30–70c band; the NO at 0.66
  is inside it.

## 2. Settled realized trading profit

- **Settled: $0.00.** The market has not settled.
- **Venue cash: $320.1338 before, $320.1638 after: +$0.03**, which equals
  the pair's locked edge (the venue credits a pair as it forms). No log
  line attributes that cash to this pair yet: the bot's own profit audit
  last ran at 01:50, before the fills. To be confirmed by the next audit.
- Fees $0, rebates $0.

## 3. Confirmed rewards and rebates

- For this window: none posted yet (rows appear about a day later).
- For 23 Nov: still no row.
- Account totals, unchanged: paid $22.79, pending $1.34 (including the two
  Serie A rows of 22 Nov, $0.52 and $0.78), skipped $3.96.
- Reward-qualifying time in this window: 351 of 589 contract-seconds
  resting (60%). The absolute amount is tiny: under ten contract-minutes
  of resting in an hour.

## 4. Account-risk enforcement under live conditions

- The limit ran in enforce mode throughout: $3.00 limit, ledger figure
  $1.3444 before, $1.6544 while the single leg was unpaired, $1.3144 after
  the pair formed.
- Room moved with resting orders as designed: $1.66 with nothing resting,
  $1.13 with one bid resting.
- **One refusal:** 01:24:46, a bid refused for lack of room; the same bid
  was sent nine seconds later once another order had come off. First
  production refusal by this limit.
- Strict scope: the two leftover NHL markets were blocked at 01:20:02 and
  dropped at 01:22:04 with no order. Every order was on an ATP market.
- Size and execution: every order 1 contract, maker-only. Five bids that
  would have crossed were refused before sending.
- Pair cost: every resting YES+NO pair summed to 0.97 or less.

## 5. Order replenishment and capital utilization

- **Replenishment after a fill: seen once.** After the YES fill the bot
  re-posted the completing NO bid twice and it filled 45 seconds later.
- **Capital utilization: very low.** Of the $1.66 of room, at most about
  $0.97 was in use, and for roughly seven minutes of sixty. For 45 minutes
  nothing was resting.
- **Order persistence: poor.** 25 orders in six minutes on three markets;
  12 pulls by the bot itself; most quote decisions were a pause or a
  missing international price.

## 6. Operational failures

1. **All markets abandoned after six minutes (the main failure).** A flat
   market with nothing to quote for two minutes gives up its slot; a
   price-move pause counted as "nothing to quote"; a released market was
   never considered again. In-play tennis pauses constantly. One of the
   three (Kokiso–Hikshi) then traded in band with good depth while the bot
   could not return to it. Fixed in development (pause keeps the slot;
   released markets can be reselected), in release candidate `0969ddadf`.
2. **The audit-under-traffic test did not happen.** The hourly audit
   completed cleanly at 01:50:54 (174 safety requests in total, none
   refused), but the bot was idle at the time.
3. **No position walk since the fills.** Three venue reads agree on zero
   open orders (watchdog 02:26:17, ledger 02:25:17, bot audit 01:50:53),
   but the bot's audit predates the 02:14 fills. The next one covers them.
4. **Request budget:** six order-related requests deferred in the first
   five minutes.
5. **Ledger:** Karpol–Benwei (+$0.04, 23 Nov) is still not booked; the
   venue-confirmed result for 23 Nov stays +$0.33 against the ledger's
   +$0.29.
6. **Limits not independently re-checked at the order gate** on this
   build (size, campaign caps, pair cap). None was breached. The
   independent gate is in the release candidate.

Clean: no safety latch, no halt, no taker order, no oversize order, no
order outside ATP; watchdog swept every 20 seconds and cancelled nothing;
**automatic relock at 02:20:03** with nothing resting.

## What this window shows and does not show

Shows: the $3 limit, strict scope, maker-only and the automatic relock
work at the venue with live orders.

Does not show: profitability (two fills); the audit under quote traffic;
accumulation (the one leg paired within a minute); anything about rewards.

## Decision for the 12-hour path: adjust, then proceed

Do not run this build again. The next live run should be the combined
release with the pause hold and reselection on, after the watchdog drills.
Tonight's failure is the best argument for it: on this build a 12-hour
campaign would have gone idle in the same way.
