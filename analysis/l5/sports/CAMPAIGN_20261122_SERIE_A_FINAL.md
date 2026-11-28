# Serie A live campaign, 22 Nov 2026 14:05-14:50Z - final report

Release 08b1a7c93. Owner-authorized window, two matches, one outcome market each.
Detail and timeline: `CAMPAIGN_20261122_SERIE_A_LOG.md`. Orders: `campaign_20261122_serie_a_orders.json`.

## Venue-confirmed result
| Item | Value |
|---|---|
| Orders / fills | 65 / 8 (16 contracts), all 2 contracts, post-only, maker |
| Spent | $8.40 (cap $16) |
| Completed pairs | 6 on Bologna win at 0.9533 -> +$0.28, paid by the venue at pairing |
| Unmatched inventory | 4 Genoa-win NO, cost $2.68 -> settled worthless (Genoa won 2-0; settlementPx 1.0000) |
| Fees / rebates | $0 / $0 |
| Liquidity rewards | none credited (the venue pays days later, if at all) |
| **Settled net** | **-$2.40** |
| Venue cash | 321.6638 before -> 319.2638 after (-2.40), unchanged by settlement, matches to the cent |

Bot state 16:13Z: locked (lock = 1), 0 open orders, 0 inventory, reconciled at 15:25Z, watchdog healthy.
The bot's meter has not re-run since settlement (hourly); it will show Genoa as settled -2.68.

## BTC-service ledger (separate, NOT rebased)
l5-pmus-live adopted the sports positions at $1 a contract and booked their settlement:
- Bologna win: held "UP 4" (stale - the venue had already netted the pairs), booked -4.00.
- Genoa win: "DOWN 4", booked -4.00 (true cost 2.68).
Its day figure is now -13.00 (it was -5.00 before the campaign) and lifetime -52.58; the true
result for the same trades is -2.05 today (+0.35 overnight, -2.40 Serie A). Drift: -10.95 today.
would_halt false, latch clear, room 37 of 50. This grows with every sports campaign until the
ownership fix is built; it does not affect the sports bot.

## What the test showed
- The controller pairs at a profit when both sides arrive: 6 pairs at 0.953, all fills but one at
  or better than the international price.
- One unpaired position decided the result. A same-side add (NO 0.68) rested 8 minutes and was
  filled into the Genoa goal, 21.5c under water at the fill; it doubled the stranded cost.
- The bot's own controls worked: window opened and relocked on time, cancelled its orders at
  14:50:00Z, stopped new exposure itself at 14:29 when worst-case loss reached $4, depth gate and
  price guard withheld orders live.
- What failed was outside the bot: this session's relock arrived 45 minutes late, and the bot
  learned of two fills 30 s after the venue. Both are addressed in release ca13dc245 (not deployed).

## Evening NFL/NBA window
On hold. The owner asked for the safety release to be deployed before another live campaign and
has made reward-eligible markets the priority. No window will be proposed until ca13dc245 is
deployed locked and verified, and any window needs the owner's explicit approval.
Note: the allowlist variable still includes `sea`; the lock variable is `1`.
