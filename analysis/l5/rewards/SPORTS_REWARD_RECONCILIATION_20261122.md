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
(Sassuolo-Genoa -1.90). Unpaired positions that have settled since 14 Nov: 49 (the 47 of
section 3, Sassuolo-Genoa, and the 24 Nov Samher-Yanale leg). All 49 settled worthless. One
more (Juagom-Bengeo, 24 Nov) is still open.

## 10. Independent audit of the unpaired residuals, 24 Nov 2026 21:10Z

Owner: "49 out of 49 unpaired positions settling worthless is an extraordinary result ...
verify that winning residual positions aren't being excluded or misclassified."

Rebuilt from the 1,016 FILL lines alone and settled from the venue's public market record
(`residual_audit_20261124/`: `rebuild.py`, `fetch.py`, `residuals.json`).

- 173 markets with fills, 1,900.97 contracts, $953.91; 913.2 pairs (-4.46); **47 residual
  markets (23 YES, 24 NO), cost 36.2485**. The list matches section 3 exactly by market,
  side, quantity and cost. No fill lacks a side; no duplicates; no sells (the older
  controller's "exits" were buys of the other side); each fill's logged position equals the
  running total.
- All 173 markets read RESOLVED with outcome prices 0/1. **Residuals: 0 won, 47 lost, 0
  unknown: -36.25.** Settling every market in full, without any pairing convention, gives
  -40.71, the same total.
- Side mapping checked four ways: the code sends YES as the venue's long side; the two
  `atc-` markets name the long side "Yes"; 10 of 11 markets last quoted above 0.70 for YES
  settled YES; an inverted mapping would turn the period into +33.86, which the bot's own
  book contradicts.
- The three markets with positive venue "realized" figures: Gaskan-Hanbra and CS2 All-Nvo
  were fully paired (+1.55, +4.32), not residuals; Det-Mia had 1 losing residual (-0.48)
  inside the 47. The venue figures themselves (+5.00, +3.18, +1.64) cannot be reproduced
  from the fills; that field's definition is unknown.
- With the 22 Nov Sassuolo-Genoa leg and the two 24 Nov legs (venue-settled at 0), the count
  is 50 of 50.

**Why it is not a coincidence, and what it does and does not show.** A completion bid rests
on the opposite side at a fixed price. When the held side is winning, the opposite side's
price falls through that bid and the position is paired. When the held side is losing, the
opposite price rises away and the bid never fills. So the legs that would have won are the
ones that get paired, for a few cents, and only losers reach settlement unpaired. The 50 of
50 is a selection effect of the pairing rule, not evidence that every opening fill is wrong.
It does show the shape of the payoff: a win is capped at the pair margin (about 3 to 6 cents
a contract at a 0.94 to 0.97 pair cost), a loss is the whole leg (about 44 to 67 cents).
Break-even needs roughly nine or more legs paired for each one left unpaired.

Not re-derived: the fees (-4.36); fills after the log's last line in a market (none affects a
residual: every market that ended later was fully paired).

## 11. Opening fills against the pairing rule, 24 Nov 2026 21:10Z

Files: `pairing_cost_20261124/` (`analyze.py`, `result.md`, `result.json`). Before fees and
rewards; every market settled from the venue's public record.

**Correction to section 10.** It said the legs that would have won are the ones that get
paired. The data does not support that half. Paired legs were a fair mix: 47.2% of them went
on to win in 14-18 Nov (they had been opened at 0.471 on average) and 50% in 22-24 Nov. What
the data does support is the other half: legs whose side is losing do not get paired, and
every unpaired leg lost (0 of 74.6 contracts, 0 of 6).

| | 14-18 Nov (173 markets) | 22-24 Nov (11 markets) |
|---|---|---|
| Exposure-adding fills, held to settlement | -33.91 (988 contracts, -3.4c each, win 43.6% at 0.471) | -4.23 (30 contracts, -14.1c each, win 40% at 0.541) |
| Actual: pairs + residuals | -4.46 - 36.25 = -40.71 | +1.28 - 3.73 = -2.45 |
| Effect of completing (actual minus hold) | -6.80 (95% range -57 to +42) | +1.78 (95% range -3.7 to +8.1) |

- **The loss is in the opening fills.** About -34 of the -40.7 is their adverse selection at
  settlement. Even that is only about 1.2 standard errors from zero when markets, not
  contracts, are treated as the independent draws.
- **Completing cost roughly nothing.** Its effect is within noise in both periods and has
  opposite signs. What it does is move the loss into the residuals and cut the spread of
  results about threefold.
- A completion pays only if the held leg loses more often than the completion's price.
  14-18 Nov: needed 53.6%, observed 52.8%. 22-24 Nov: needed 42.6%, observed 50%.
- Given that every unpaired leg loses, the current controller's pairs (5.3c margin) need
  about 92% of opening contracts to pair; 80% did in 22-24 Nov.
- No split (pair cost, time to completion, sport, day) is distinguishable from noise.

Limits: the counterfactual keeps the same opening fills; the older controller paid above $1
and used taker exits; rewards and fees excluded; the Serie A and 24 Nov fills come from
reports, not raw fill lines.
