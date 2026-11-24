# Cost of pairing winners / retaining losers — 24 Nov 2026 (offline; fills + venue settlement only)

Script: analyze.py; full tables: result.json. Settlement = venue public record (YES = long side), all markets resolved 0/1.
Period (i) 14-18 Nov: 1,016 fills, 173 markets. Period (ii) 22-24 Nov bounded campaigns: 33 fills, 11 markets
(22 Nov Serie A 8 fills from the campaign log, 22 Nov NBA + 23 Nov ATP 17 fills from fills_context.json,
24 Nov 01:20Z Cruz pair, 24 Nov 17:00-18:50Z 6 fills as given; the last 6 have no timestamps on file).
Before fees and rewards. Pair matching is FIFO by lot; the average-cost convention of the reconciliation is also given.

## 1. Fill classes (from running position)
| | (i) fills / contracts / $ | (ii) fills / contracts / $ |
|---|---|---|
| Exposure-adding | 510 (+2 split) / 987.77 / 464.78 | 18 / 30 / 16.23 |
| Exposure-reducing | 504 (+2 split) / 913.20 / 489.13 | 15 / 24 / 10.22 |
Logged position = rebuilt position on all 1,016 fills.
Stage vs class (i): complete 678.4 and exit 212.85 contracts are all reducing; open/open_rewarded/cheap_first/extend
987.77 adding; 21.95 contracts (19 fills) labelled "open" actually reduced exposure (other side of a two-sided quote).
(ii): every open/extend is adding, every complete is reducing.

## 2. Hold-to-settlement counterfactual (adding fills only)
| | (i) | (ii) |
|---|---|---|
| contracts / markets | 987.77 / 173 | 30 / 11 |
| cost -> payout | 464.78 -> 430.87 | 16.23 -> 12.00 |
| P&L | -33.91 | -4.23 |
| avg price | 0.471 | 0.541 |
| win rate (contracts / dollars) | 43.6% / 45.6% | 40.0% / 39.8% |
| per contract vs fair coin | -3.4c | -14.1c |
| binomial SE on win rate | 1.6 pts (z -2.2 if independent) | 9.1 pts (z -1.6) |
| by market: winners / losers, SE of total, t | 74 / 99, +-29.3, t -1.16 | 4 / 7, +-3.8, t -1.10 |

## 3. Actual, and the effect of completions
| | (i) | (ii) |
|---|---|---|
| Pairs (avg-cost convention) | 913.2 at 1.005: -4.46 | 24 at 0.947: +1.28 |
| Residuals | 47 markets, -36.25, 0 won | 3 markets, 6 contracts, -3.73, 0 won |
| Actual total | -40.71 | -2.45 |
| (FIFO split) | pairs -6.01, residual 74.57 contracts -34.70 | same |
| Hold (section 2) | -33.91 | -4.23 |
| Net effect of completions | -6.80 | +1.78 |
| market-bootstrap SE / 95% CI | +-25.1 / -57 to +42 | +-3.0 / -3.7 to +8.1 |
| completion cost, avg price | 489.13, 0.536 | 10.22, 0.426 |
| paired legs: won / lost | 430.87 / 482.33 = 47.2% won | 12 / 12 = 50% won |
| avg opening price of paired legs | 0.471 | 0.521 |
| upside given up (legs that won) | -218.46 | -4.75 |
| loss avoided (legs that lost) | +211.66 | +6.53 |
| paired legs if held instead | +0.79 (actual -6.01) | -0.50 (actual +1.28) |
| unpaired legs: won | 0 of 74.57 | 0 of 6 |

Reading: the paired legs were NOT disproportionately winners. They won 47.2% at an average price of 0.471: a fair
mix. The asymmetry is one-sided: legs still unpaired at the end are all losers (a leg can only stay unpaired if the
other side never came back to the bid), and those 74.6 contracts (7.5% of opening contracts) carry the whole loss.

## 4. Splits (paired lots; completion effect = actual pair result minus holding the opening leg)
(i) by pair cost: <=0.97: 220.6 pairs, pair +13.87, hold +20.34, effect -6.47, held leg won 55.3% (breakeven needs <=52.4%)
    0.97-1.00: 233.8 pairs, +2.66, +21.77, -19.11, won 58.6% (breakeven 50.4%)
    >1.00: 458.9 pairs, -22.54, -41.32, +18.78, won 37.5% (breakeven 41.6%)
(i) by time: <1 min 254.7 pairs effect +3.37 (won 46.5%); 1-10 min 550.1, -1.79 (45.7%); >10 min 108.4, -8.38 (56.2%)
(i) by label: complete 678.4 pairs effect +3.23; exit 212.9 pairs effect -9.74
(ii) by pair cost: <=0.97: 22 pairs, +1.24, hold +0.66, effect +0.58, won 54.5%; 0.97-1.00: 2 pairs, effect +1.20; >1.00 none
(ii) by time: <1 min 2 pairs +0.03; 1-10 min 8 pairs +0.78; >10 min 12 pairs +0.75; unknown 2 pairs +0.22
By sport (i), hold / actual / completion effect: cfb -12.21/-6.56/+5.65 (33 mkts); itfwo -20.50/-9.43/+11.06 (24);
setkameua -13.52/-8.06/+5.45 (20); cs2 +6.06/-6.28/-12.35 (21); atp -3.17/-2.59/+0.58 (16); itfme +7.42/-3.07/-10.49 (14);
setkamemd +11.47/-0.33/-11.80 (7); nba -5.70/-1.43/+4.27 (7); dota2 -2.39/+0.10/+2.48 (8); wtt +6.18/-0.60/-6.78 (2);
others in result.json. (ii): atp +1.01/-0.59/-1.60 (8 mkts); nba -1.86/+0.54/+2.40 (1); sea -3.38/-2.40/+0.98 (2).
By day (i) completion effect: 14 Nov -22.94, 15 Nov +1.01, 16 Nov -4.60, 17 Nov +5.76, 18 Nov +13.98.

## 5. What the data supports
- (i) Not completing would have ended -33.91 instead of -40.71: better by 6.80, with a market-level SE of about 25
  (95% CI -57 to +42). (ii) worse by 1.78, SE about 3. Both are "about the same"; neither sample can tell the sign.
- A completion at price c pays only if the held leg goes on to lose more often than c. (i): needed 53.6%, observed
  52.8% (binomial SE 1.7 pts, more with clustering). (ii): needed 42.6%, observed 50% (SE 10 pts).
- The loss is in the opening fills (-3.4c per contract, about -34 of the -40.7), not in the pairing rule. Pairing
  re-sorts that loss into the residuals and cuts the spread of results about threefold (SE 9.9 actual vs 29 held).
- Breakeven pairing ratio when every unpaired leg loses: (ii) pairs earn 5.3c, unpaired legs cost 62c -> 92% of
  opening contracts must pair; observed 80% (24/30). (i) at <=0.97 pairs (6.3c, residual 46.5c) -> 88%; observed
  92.5% overall, but the average pair cost was 1.005-1.007, so no ratio broke even.

## 6. Limitations
Opening fills are themselves selected by resting bids; the hold counterfactual keeps the same openings, although
without completions the bot would have hit caps and quoted differently. 14-18 Nov controller paid above $1, used taker
exits (fees -4.36 not included) and 5-contract reward quotes on 18 Nov. Rewards (+15.75, +1.30) excluded. Contracts
in one market share one outcome: 173 and 11 independent draws, not 988 and 30. Buckets and sports are noise-level.
24 Nov 17:00-18:50Z fills and Serie A fills come from reports, not raw fill lines. FIFO lot matching is a convention.
