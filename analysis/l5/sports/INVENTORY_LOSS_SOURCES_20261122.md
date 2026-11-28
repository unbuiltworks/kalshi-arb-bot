# Where the sports bot's inventory losses come from (as of 22 Nov 2026 15:25Z)

Source: the bot's own ACCOUNT_AUDIT (venue trade and resolution rows since 14 Nov 15:30Z,
1,139 trades, 47 resolutions) and its book. Read-only.

## Lifetime (14-22 Nov), all controllers
| Component | USD |
|---|---|
| Completed pairs: 929 pairs, gross margin | -3.83 |
| Unpaired legs held to settlement (residual) | **-36.97** |
| Fees charged (taker, older controller) | -4.36 |
| Rebates credited | 0 |
| Book total, 175 settled markets | -40.36 |
| Venue-confirmed settled realized, 47 markets | -29.30 |

- **Unpaired legs are 82% of the loss** (36.97 of 45.16 across the three components).
- Completed pairs did not earn either: 929 pairs made -$3.83, i.e. an average pair cost a
  fraction above $1.00. The older controller completed pairs by paying up; it traded a
  residual loss for a guaranteed small one.
- The bot pairs most legs: 167 resolved under the recovery controller, 95.2% paired, median
  87 s to pair, 8 held to close. The ~5% that do not pair lose most of their cost.
- By sport (book, settled): ITF women -9.43, table tennis (setkameua) -8.06, CFB -6.41,
  CS2 -6.28, ITF men -3.07, ATP -2.59, NBA -1.23. Positive: UTR +1.15, Dota 2 +0.10.
  The worst are thin markets with no international price; they are now excluded by the
  price guard and depth gate.

## The arithmetic that decides RN1
A completed pair at two contracts earns about $0.06-0.08. An unpaired leg loses up to its
cost, $0.90-2.70. One stranded leg erases 15-40 completed pairs. Break-even needs roughly
97%+ of opened legs to pair, or stranded legs to be smaller and rarer than they are.

## RN1 controller so far (22 Nov, price guard on, venue cash-confirmed)
| Campaign | Pairs | Pair P&L | Unpaired | Result |
|---|---|---|---|---|
| 01:21-01:32Z NBA + CFB | 10 | +0.35 | none | +0.35 (cash) |
| 14:05-14:50Z Serie A | 6 | +0.28 | 4 Genoa NO, $2.68 | settles +1.32 or -2.68 |

Pairs now complete below $1 (0.95-0.97) instead of above it. The loss source is unchanged:
the leg that does not pair.

## What made today's unpaired leg twice as large
Genoa NO 0.66 x2 filled at 14:12:31 and could not be paired for 16 minutes. A same-side
extension (NO 0.68 x2) was placed at 14:20:57 and rested until Genoa scored at ~14:28; it was
filled into the jump, 21.5c per contract under water at the moment of the fill. Without it the
exposure would have been $1.32, not $2.68.

## Single highest-impact deficiency
Same-side extension while a leg is unpaired. It adds to exactly the inventory that is failing
to pair, on the side the market is moving against, and it is the only new-exposure order that
rests while the bot already holds risk in that market.
