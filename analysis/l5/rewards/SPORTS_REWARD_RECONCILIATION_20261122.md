# Sports liquidity rewards - reconciliation against actual orders, fills and settlements

22 Nov 2026. Read-only. Sources: the venue's `/v1/incentives/earnings` record as logged by the
sports bot (latest read 22 Nov 15:23Z); the bot's complete ORDER and FILL log for 14-18 Nov
(9,279 orders, 1,016 fill lines; `sports_orders_fills_20261114_20261118.json.gz`); the venue's
public final settlement price for every market left with unpaired contracts. No ledger was
altered. Rewards are counted once and are never inside the trading figures.

## 1. What the venue paid (CONFIRMED)
| Status | Rows | USD |
|---|---|---|
| PAID | 120 | 22.79 |
| PENDING | 2 | 0.04 (two Valorant markets, 21 Nov) |
| SKIPPED (not paid) | 63 | 3.96 |

All 185 rows are `liquidityProgram`, all on sports markets. None is crypto. Maker rebates credited
to this bot: $0.

| Reward days | Paid | Who earned it | When credited |
|---|---|---|---|
| 8-14 Sep (pre-bot) | 7.04 | older account activity, not this bot | transfers of 2.88, 1.65, 2.51 on 9, 10, 14 Sep |
| 14-16 Nov | 7.09 | this bot | between 17 Nov 12:19Z and 20:56Z |
| 17-18 Nov | 8.66 | this bot | between 19 Nov 15:01Z and 16:01Z |

Bot era: **$15.75 on 76 market-days, 14-18 Nov.** No reward day after 18 Nov has been paid
(the bot placed no orders 19-20 Nov; 36 unfilled orders on 21 Nov earned the pending $0.04).

## 2. Attribution to orders (CONFIRMED except $0.12)
- 74 of the 76 rewarded markets, $15.63, have this bot's orders in its log on the reward day.
- The Dota 2 row of $1.72 (14 Nov) that the bot's book no longer lists: 12 orders, 10 contracts,
  20:07-20:20Z, no fills. It is the bot's.
- NOT ATTRIBUTED: $0.12 on two markets with no order in the retrieved log
  (aec-atp-zacvii-geobla-2026-11-17 0.07, aec-valorant-xjg-ur-2026-11-18 0.05).
- The owner's manual sports fills (11 markets) do not overlap any rewarded market.
- Payment date per row is not in the venue record; only the two credit windows above.
- Sept rows: 8 of the 44 pre-bot paid rows are visible ($2.53); the other 36 ($4.51) scrolled out
  of every logged read and cannot be listed.

## 3. Trading on the same period, rebuilt from fills (CONFIRMED fills; settlement from venue)
14-18 Nov: 241 markets quoted, 173 with fills, 1,900.97 contracts bought for $953.91.

| Component | USD |
|---|---|
| 913.2 completed pairs (average cost 1.005) | -4.46 |
| Unpaired contracts in 47 markets, cost 36.25, ALL settled worthless | -36.25 |
| Trading before fees | -40.71 |
| Taker fees charged by the venue (older controller) | -4.36 |
| **Trading after fees** | **-45.07** |
| Liquidity rewards paid for those days | +15.75 |
| **Net, 14-18 Nov** | **-29.32** |
| 21-22 Nov campaigns (+0.35, -2.40), rewards pending 0.04 | -2.05 |
| **Net, 14-22 Nov** | **-31.37** |

This replaces the earlier range of -12 to -25: the venue's per-market "realized" field (-28.15)
leaves out almost all pair results, so it understated the loss. The bot's own book (-40.36
before fees) agrees with the fill rebuild (-40.71).

## 4. Rewarded versus non-rewarded markets
| Group | Markets | Rewards | Pairs P&L | Unpaired P&L | Net |
|---|---|---|---|---|---|
| Earned a reward | 74 | +15.63 | +0.85 | -15.85 (13 markets) | **+0.63** |
| Earned no reward | 167 | 0 | -5.31 | -20.40 (34 markets) | **-25.71** |

Inside the rewarded group:
| | Markets | Rewards | Trading | Net |
|---|---|---|---|---|
| Quoted, never filled | 6 | 1.81 | 0 | +1.81 |
| Filled and fully paired | 55 | 11.36 | +1.12 | +12.48 |
| Left with unpaired contracts | 13 | 2.46 | -16.12 | -13.66 |

Correction of an earlier statement in this session: most reward money did NOT come from quotes
that never filled. It came from markets where the bot filled and paired within minutes; the
venue's "realized" field shows 0 for netted pairs, which is what misled the first reading.

## 5. By sport (14-18 Nov)
| Sport | Markets | Rewards | Trading | Net |
|---|---|---|---|---|
| Table tennis (setkameua) | 37 | 6.30 | -8.06 | -1.76 |
| CS2 | 32 | 3.10 | -6.28 | -3.18 |
| Dota 2 | 10 | 2.29 | +0.10 | **+2.39** |
| Table tennis (setkamemd) | 10 | 1.92 | -0.33 | **+1.59** |
| College football | 34 | 0.80 | -6.56 | -5.76 |
| ATP | 20 | 0.49 | -2.59 | -2.10 |
| ITF women | 31 | 0.03 | -9.43 | -9.40 |
| ITF men | 21 | 0.02 | -3.07 | -3.05 |
| NBA | 9 | 0 | -1.43 | -1.43 |

## 6. Reward intensity (bot-era, ESTIMATED denominators)
Quoting time per market is taken as first order to last order; resting commitment as the usual
order size x about $1 (both sides). Both are approximations from the log, not venue figures.
- Rewarded markets: $15.63 over 49 market-hours = about $0.32 per market-hour, and over about
  137 contract-hours = about $0.11 per dollar-hour committed.
- All markets: $15.63 over 165 market-hours and 358 contract-hours = about $0.04 per dollar-hour.
- 18 Nov, reward-first mode, 5-contract quotes, about 2.2 hours (12:53-15:05Z), cap $60:
  $8.21 of rewards (about $3.70 per hour of quoting), trading -12.76, net -4.55.
- Concentration: 3 markets paid $7.00; the top 10 paid 71%; 40 of 76 rows paid under 5 cents.
- Largest single rows were minutes of quoting: $3.40 for 4 orders over 4 minutes at 5 contracts
  (table tennis), $1.88 for 3 orders over 3 minutes, $1.72 for 13 minutes at 10 contracts with
  no fill. Rewards followed thin markets with little competing liquidity, not time or size.

## 7. Where the money was lost
1. Markets with no reward: -25.71. Two thirds of everything quoted earned nothing.
2. Unpaired contracts: -36.25 in 47 markets; every one settled at zero.
3. Completing pairs above $1: on 18 Nov 220 pairs cost 1.04 on average (-9.35); the older
   controller paid up to finish pairs.

## 8. Not reliable / open
- $0.12 of bot-era rewards and $4.51 of September rewards cannot be tied to orders.
- Quoting hours and committed dollars are log-derived estimates.
- Whether a reward is paid at all on a market is known only days later; the venue publishes
  pools, not who qualified.
- The thin markets that paid most (table tennis, Dota 2, CS2) have no usable international
  price, so the price guard cannot protect quotes there; CS2 is currently blocked outright.

## 9. Update, 24 Nov 2026 19:30Z: the 22 Nov Serie A rows were paid

Venue record at 19:29:59Z: PAID 122 rows / $24.09, SKIPPED 65 / $4.00, PENDING none.
Change since 22 Nov: two rows PENDING -> PAID (+$1.30); the two 21 Nov Valorant rows
PENDING -> SKIPPED ($0.04, not paid). No rows yet for 23 or 24 Nov.

| Reward days | Paid | Who earned it |
|---|---|---|
| 8-14 Sep (pre-bot) | 7.04 | older account activity |
| 14-18 Nov | 15.75 | this bot, 76 market-days |
| 22 Nov | 1.30 | this bot, Serie A window 14:05-15:20Z, 2 markets, 2-contract quotes |
| **Total** | **24.09** | |

22 Nov Serie A window, by market (fills and settlement from `sports/CAMPAIGN_20261122_SERIE_A_LOG.md`):

| Market | Reward | Trading | Net | What happened |
|---|---|---|---|---|
| Bologna-Udinese | 0.52 | +0.28 | **+0.80** | 6 pairs at 0.9533, flat |
| Sassuolo-Genoa | 0.78 | -2.68 | **-1.90** | 4 NO unpaired, settled worthless |
| Window | 1.30 | -2.40 | **-1.10** | |

Reward intensity, same estimate as section 6: $1.30 over about 2.5 market-hours = about $0.52
per market-hour (14-18 Nov rewarded markets: $0.32).

Bot era, 14-24 Nov, CONFIRMED rewards against settled trading:

| Period | Trading after fees | Rewards paid | Net |
|---|---|---|---|
| 14-18 Nov (older controller, wide slate) | -45.07 | +15.75 | -29.32 |
| 21 Nov NBA | +0.35 | 0 | +0.35 |
| 22 Nov Serie A | -2.40 | +1.30 | -1.10 |
| 22 Nov NBA | +0.54 | 0 (no row) | +0.54 |
| 23 Nov ATP | +0.33 | not posted | +0.33 |
| 24 Nov ATP 01:20Z | +0.03 | not posted | +0.03 |
| 24 Nov 17:00-18:50Z | -0.51 settled; one NO leg (cost 0.44) open | not posted | -0.51 |
| **Bounded campaigns since 21 Nov** | **-1.66** | **+1.30** | **-0.36** |
| **Bot era total** | **-46.73** | **+17.05** | **-29.68** |

The pattern of section 4 holds on the new rows: reward plus pairing is positive
(Bologna-Udinese +0.80); an unpaired leg outweighs the reward on its market
(Sassuolo-Genoa -1.90). Of the 50 unpaired positions left in settled markets since 14 Nov
(47 in 14-18 Nov, Sassuolo-Genoa, and the 24 Nov Samher-Yanale leg, with one still open),
49 settled worthless.
