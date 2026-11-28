# Pre-game reward quoting — ranking and break-even loss

22 Nov 2026, snapshot 23:03 UTC (in-play comparison 23:11 UTC). Read-only:
public venue data only, no orders, bot locked throughout.

**Every reward figure here is theoretical.** It is the venue's published
scoring rule (`quote_estimate` in `src/pmus_sports_rewards.py`) applied to
the public book. Nothing has been credited. Assumptions that are not yet
verified are listed at the end.

How to read the columns:

- **Within the pair cap** means the two resting bids sum to no more than
  $0.97 (the owner's limit). In a one-cent-wide market that puts one side
  at the best price and the other two cents back, where it usually scores
  nothing, so the capped figure is roughly the reward for one side.
- **Reward per contract-hour** is the reward for one YES and one NO
  contract resting for an hour. It barely changes with size at these sizes,
  so it is the number to compare markets by.
- **Break-even loss** is the same number read the other way: the trading
  loss per resting contract per hour that cancels the reward.

## Soccer and hockey, pre-game (first priority)

Only one soccer or hockey match was in a paying pre-game period with a
reward worth measuring: Querétaro v Necaxa (Liga MX, kick-off 01:10 UTC).
Its three markets are the top three.

| | Draw | Querétaro | Necaxa |
|---|---|---|---|
| Market | `atc-lmx-que-nec-2026-11-22-draw` | `…-que` | `…-nec` |
| Pool, pre-game period | $180 over 6 h ($30/h) | same | same |
| Target size per side | 10,000 | 10,000 | 10,000 |
| Bid / ask | 0.25 / 0.26 | 0.43 / 0.44 | 0.31 / 0.32 |
| Resting at best bid / ask | 2,481 / 16,137 | 8,718 / 13,505 | 14,432 / 9,692 |
| Resting in top three levels, bid / ask | 22,259 / 36,980 | 30,725 / 52,377 | 37,071 / 41,562 |
| Traded in the market's whole life | 576 | 9,829 | 7,251 |
| Price movement, last 8 h (international one-minute) | none | none | one cent |
| Reward per contract-hour, both at best price | 0.36c | 0.23c | 0.21c |
| Reward per contract-hour, within the pair cap | 0.27c | 0.12c | 0.10c |
| 10 a side, within cap | $0.027/h | $0.012/h | $0.010/h |
| 50 a side, within cap | $0.133/h | $0.060/h | $0.052/h |
| Size for this market alone to earn $1 over a full 6 h | 63 a side | 140 a side | 161 a side |
| Size for $1 in the 2 h that were left | 185 a side | 414 a side | 476 a side |
| Break-even loss per resting contract per hour | 0.27c | 0.12c | 0.10c |
| Break-even on one full one-sided fill, 2 h left | 0.54c per contract | 0.24c | 0.21c |
| Break-even on one full one-sided fill, full 6 h | 1.6c per contract | 0.7c | 0.6c |

Reading the break-even: a fill that ends up one cent wrong costs about four
hours of the draw market's reward on that contract, and eight to ten hours
on the other two. Over a whole six-hour pre-game period each resting
contract can afford roughly one such fill in the draw market and less than
one in the others.

Hockey is not worth quoting for rewards at any size we could run:

| Market | Pool | Resting at best bid / ask | Reward per contract-hour | Size for $1 over 6 h |
|---|---|---|---|---|
| `aec-nhl-edm-stl-2026-11-22` | $33 over 6 h | 40,474 / 70,818 | 0.007c | 2,458 a side |
| `aec-nhl-chi-van-2026-11-22` | same | 65,830 / 51,811 | 0.005c | 3,145 a side |
| `aec-nhl-pit-dal-2026-11-22` | same | 105,618 / 170,224 | 0.003c | 6,410 a side |

Hockey prices moved zero to two cents in eight hours. Serie A and La Liga
matches a day ahead are in the "early" period, which pays effectively
nothing (under 0.001c per contract-hour).

## Tennis and esports, pre-game (second priority)

| Market | Pool | Target | Resting at best bid / ask | Traded | Reward per contract-hour, within cap | 10 a side | Size for $1 over 6 h |
|---|---|---|---|---|---|---|---|
| `aec-utr-brucjac-stearc-2026-11-22` | $10 over 6 h | 250 | 78 / 16 | 13 | 0.96c | $0.096/h | 24 a side |
| `aec-utr-jonsco-gaacor-2026-11-22` | same | 250 | 85 / 39 | 0 | 0.91c | $0.091/h | 25 a side |
| `aec-utr-ishhar-meharj-2026-11-22` | same | 250 | 118 / 65 | 2 | 0.54c | $0.054/h | 38 a side |
| `aec-atp-ognmil-keesmi-2026-11-22` | $40 over 6 h | 19,000 | 444 / 607 | 2 | 0.22c | $0.022/h | 79 a side |

- UTR tennis pays three to four times more per contract than Liga MX
  because the books are thin. Thin also means no reference price: there is
  no international market to check our quote against, so the international
  price guard cannot apply, and a fill there is more likely to be someone
  who knows more than we do.
- Most ATP matches showed zero: their books do not reach the 19,000 target,
  and by our reading of the rule a side that falls short is not scored.
- No esports market was in a paying pre-game period. Counter-Strike pays
  only in-play.
- Table tennis shows the highest theoretical figures (0.9c to 5c per
  contract-hour) with nine contracts at the best price and two-cent
  spreads. It stays excluded: the 14 to 18 Nov fills there lost 3.0c each.

## What the numbers say

1. **Pre-game is safe and pays almost nothing at our size.** Prices barely
   move and almost nothing trades (576 contracts ever in the draw market,
   against 2,481 ahead of us in the queue). We would rarely be filled, and
   when we were it would mostly be because the price moved through us.
2. **Under the current limits the reward is zero.** Two contracts a side in
   two markets earns about one cent an hour, and a day's rewards under $1
   are not paid at all.
3. **The smallest test that should produce a credit** is about $1.50
   predicted in one programme-day. Two Liga MX matches, all six markets,
   25 a side for the full six hours predicts $1.47, with about $145 resting.
   A full day at 50 a side predicts roughly $3 to $6 on about $290 resting.
4. **The same match in-play pays 30 to 50 times more per contract.**
   Guadalajara v Cruz Azul at 23:11 UTC, four minutes after kick-off,
   within the pair cap: draw 9.3c, Guadalajara 3.0c, Cruz Azul 13.5c per
   contract-hour; 10 a side across the three markets about $2.60/h. This
   assumes the $1,440 in-play pool is paid over three hours. The break-even
   loss is correspondingly larger, and so is the risk: one goal moves these
   prices 10c to 25c, and one goal caught with stale quotes at 10 a side
   costs roughly $4, about a match's worth of reward.
5. **Rewards at reachable sizes are an addition, not the engine.** Nothing
   here approaches the $480 a day target on its own.

## Not yet verified

- That each market has its own pool for each period, rather than one pool
  shared across the programme's markets. If shared, every figure shrinks.
- That the $1 minimum applies to a day's total per programme (inferred from
  September and November reward rows).
- That a side whose book falls short of the target size is not scored.
- That the in-play pool is paid over about three hours.
- Fill rate. The minute-by-minute record of book and traded volume started
  at 23:03 UTC and runs to kick-off; it is the first direct measurement.
- Whether predicted rewards match credited ones. Only a live test with a
  credit to compare can settle this, and none is authorised.

## Files

- `scan/breakeven.py`, `scan/breakeven_20261122_2303.json` — every pre-game
  market that was paying, at 2, 10, 25 and 50 a side.
- `scan/pregame_sampler.py` — the once-a-minute recorder.
- `scan/intl_history_*_20261122.json.gz` — eight hours of one-minute
  international prices for the hockey and Liga MX matches.

## Addendum, 23 Nov 01:35 UTC — pre-game against in-game, measured

Once-a-minute record of the public book and cumulative traded volume, 23:01
to 01:34 UTC (about a minute missing at 01:01). "Pre-game" is up to five
minutes before the start; "in-game" is from ten minutes after it. Averages
are weighted by market-hours. This measures the market, not our fills: the
bot was locked.

| League, phase | Markets | Hours | Best-price changes per hour | Price path per hour | Traded per hour | Average size at best price |
|---|---|---|---|---|---|---|
| Liga MX, pre-game | 3 | 6.1 | 1.6 | 0.8c | 1,261 | 10,551 |
| Liga MX, in-game | 6 | 5.8 | 49.6 | 65.8c | 74,736 | 10,033 |
| NHL, pre-game | 3 | 3.7 | 0.5 | 0.3c | 22,349 | 91,848 |
| NHL, in-game | 3 | 3.2 | 31.5 | 42.1c | 64,883 | 20,691 |
| ATP, pre-game | 10 | 25.4 | 0.3 | 0.2c | 68 | 1,648 |
| UTR tennis, pre-game | 4 | 6.8 | 3.4 | 6.2c | 20 | 99 |
| La Liga and Serie A, a day ahead | 6 | 15.2 | 0.0 | 0.0c | 8 | about 15,000 |

"Price path" is the sum of every move of the mid, up and down. The single
calmest market was the Querétaro–Necaxa draw before the game: no price
change at all in two hours and 53 contracts traded an hour.

What it adds to the break-even table:

- **Pre-game, Liga MX.** The price moves about one tick an hour. If every
  such move fills the bid it runs through, that is roughly 0.4c per
  contract-hour on each side, against a reward of 0.10c to 0.27c. The draw
  market, which did not move, is the one pre-game market here where the
  reward clearly exceeds the measured movement; the two team markets do
  not, unless a later move back completes the pair.
- **In-game, Liga MX.** The price path is about 66c an hour against a
  theoretical reward of 3c to 13c per contract-hour. Whether that is a
  profit or a loss depends on how much of the path is back-and-forth (which
  completes pairs inside the 0.97 cap) and how much is one-way (goals). The
  book alone cannot answer that; only fills can.
- **Queue position.** Pre-game, about 1,300 contracts an hour trade against
  roughly 10,500 resting at the best price, so an order at the back of the
  queue is rarely reached unless the price moves. In-game the best-price
  size turns over about seven times an hour.
- **Still unmeasured:** our own fills by phase, their markouts, and any
  credited reward. The development build now counts all of these by phase.
