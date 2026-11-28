# Campaign report — 22 Nov 2026, NBA Detroit–Atlanta

Source: production logs of the sports bot, the account ledger and the
watchdog, read at 23:45 UTC. Venue execution history and cash are the
accounting basis. Markouts are marks, not profit.

## Result

**Venue-confirmed realized net: +$0.54.** Eight completed pairs bought for
$7.46 and paid $8.00. No fees, no rebates, no rewards credited.

| Item | Value |
|---|---|
| Build and mode | `158685d3a`, maker-only, taker off, NFL and NBA allowed |
| Window opened | 21:35:00 UTC |
| First / last order | 21:35:18 / 22:10:31 UTC |
| First / last fill | 21:36:23 / 22:26:39 UTC |
| Relock | issued about 22:19 UTC, effective 22:41:19 UTC |
| Markets traded | one: `aec-nba-det-atl-2026-11-22` |
| Orders sent | 45, all two contracts (YES 23, NO 22; 5 opening, 31 adding, 9 completing) |
| Refused before sending | 7 by the bot's own stale-price check, 12 deferred by the request budget |
| Venue rejections | none |
| Pulled by the bot | 17 reactive pulls covering 25 orders, 5 of them on the international price guard |
| Fills | 8 fills, 16 contracts |
| Inventory at the end | YES 8, NO 8: flat, 8 pairs |
| Capital committed | $7.46 spent (YES $4.62, NO $2.84); average pair cost $0.9325 |
| Largest unpaired holding | 2 contracts (about $1.14 at most) |
| Campaign spend counter | $7.46 of $16 |
| Campaign loss counter | $0 of the $4 stop |
| Venue cash | $319.2638 before, $319.8038 after: +$0.54, all explained |
| Settled trading P&L | +$0.54 |
| Rewards and rebates credited | $0 (no reward row dated 22 Nov) |
| Total realized net | +$0.54 |

How the venue paid: it nets a YES and a NO into $1 as soon as the pair
completes, so the $8.00 arrived during the window, not at settlement. Cash
was already $319.80 at 22:27 UTC and the venue position has been zero since.
The market expired at about 23:06 UTC (NO won) with nothing left to pay.

## Fills and marks

Value of the side bought against its fill price, per contract. These are
marks. Every fill ended up in a pair, so none of them is a realized result
on its own.

| Fill (UTC) | Side | Price | Role | 5 s | 30 s | 2 min | 5 min |
|---|---|---|---|---|---|---|---|
| 21:36:23 | YES | 0.57 | open | −3.5c | −3.5c | −0.5c | +0.5c |
| 21:55:24 | NO | 0.38 | complete | +2.5c | −1.5c | +11.5c | +1.5c |
| 21:57:03 | YES | 0.55 | add | −5.5c | −2.5c | +2.5c | +7.5c |
| 21:59:45 | NO | 0.41 | complete | −3.5c | +2.5c | −1.5c | −2.5c |
| 22:00:02 | YES | 0.58 | add | −0.5c | +2.5c | +4.5c | +3.5c |
| 22:01:16 | NO | 0.40 | complete | −0.5c | −0.5c | −2.5c | −3.5c |
| 22:10:23 | NO | 0.23 | add | missed | missed | −10.5c | −8.5c |
| 22:26:39 | YES | 0.61 | complete | −3.5c | −2.5c | −2.5c | −24.5c |

Means: −2.1c at 5 s (7 fills), −0.8c at 30 s (7), +0.1c at 2 min (8),
−3.3c at 5 min (8). The 5-minute mean is dominated by the last fill.

Reading: the pairs made money because both sides filled inside the pair cap,
not because the individual fills were good. Seven of the eight fills were
marked down five seconds later. The last two were filled while the price was
moving hard; had either been left unpaired it would have been a real loss.

## Reconciliation and watchdog

- **22:10:20 UTC mismatch.** The bot's position check found the venue two
  contracts ahead of its own book: the NO fill at 0.23 was recorded 89
  seconds late. That was a stop condition. The bot blocked new exposure on
  its own from that point (its last order is 22:10:31).
- **Relock delay.** The relock was issued about 22:19 and reached the
  service at 22:41:19 because this session stalled. One resting completion
  filled in between (22:26:39, YES 0.61), which closed the last pair.
- **After the relock.** Zero resting orders, no orders or fills since.
  Inventory reconciliation at 22:45 and 23:45 UTC: clean, no mismatch, venue
  positions zero. Fills confirmed against venue rows: 16 of 16.
- **Watchdog.** Sweeping about every 20 seconds all evening, zero open
  orders, no halt, cancel or alert.
- **Account ledger.** It first retired the market at 23:06:36 at its $1
  bound with a result of $0, then corrected it at 23:24:29 to +$0.54 under
  the sports origin from the bot's authenticated fills. Day figure −$2.05 →
  −$1.51; lifetime −$41.63 → −$41.09; drawdown $1.67 of $3.00; its stale
  two-contract row is gone.

## What this does and does not show

- One market, 66 minutes, eight pairs, +$0.54. Too small to say the method
  is profitable.
- No NFL market was traded. One was selected at 22:38 but new exposure was
  already blocked. Why none was selected earlier is not established.
- No reward was earned or expected at this size.

## Blockers carried forward

1. A fill was seen 89 seconds late; cause not found.
2. The fuller audit runs hourly and only reports; it does not lock the bot.
3. A stalled session delayed the relock by 22 minutes. The bot's own block
   held, but supervision cannot depend on this session.
4. The account-level drawdown limit is not enforced on the sports bot.
5. The ledger books a sports market at its bound first and corrects it 18
   minutes later; in between the day figure is wrong.
6. Standing authorization for later windows is treated as suspended: this
   window was not clean. Nothing further is armed.
