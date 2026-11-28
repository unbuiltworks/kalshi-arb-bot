# Selection funnel: what the controller rejects and why (25 Nov, 23:11 UTC)

Read-only. Sources: `funnel.py` and `funnel_now.json` (every in-play
market), and `rewards/scan_20261125/net_all_2310.json` (every market in
play or starting within 12 hours). The net figures are THEORETICAL: reward
under the venue rule minus expected fill cost, with the same model as
`PMUS_RN1_NET_RANK`. Production settings: price band 0.30–0.70 on the YES
bid, at most 6 ticks of spread, a touch depth of at least 3 contracts (our
own excluded), a per-bid range of 0.20–0.92, competitions setkameua,
setkamemd, utr, atp and wta, cricket and esports excluded, the
international price not required.

## In-play markets: 56

| Filter | Rejects, on its own | Rejects in the bot's order | Only reason for |
|---|---|---|---|
| cricket / esports (incl. eBattles) | 17 | 17 | 0 |
| competition not in the list (22 college basketball, 3 Czech table tennis, 1 NBA, …) | 43 | 26 | 1 (NBA, net negative) |
| no two-sided book | 10 | 0 | 0 |
| price band 0.30–0.70 | 24 | 9 | 3 (all negative: no reward at 0.03, 0.26, 0.89) |
| spread over 6 ticks | 12 | 1 | 0 |
| no paying programme | 27 | 0 | 0 |
| touch depth under 3 | 19 | 0 | 0 |
| international price | 0 (not required; the guard applies only where a price exists, at quote time) | 0 | 0 |
| expected net not positive | | 3 of the 3 left | |

Three markets pass every filter (UTR `kawgab-duadan`, UTR `garcar-borvic`,
ATP `olibon-luimig`). Their reward is $0.01–0.02 an hour, so they are
negative at every loss assumption tried:

| Loss per filled contract (at the best bid / one tick behind) | Best of the three |
|---|---|
| campaign, 5c / 3.5c | −$0.08/h |
| measured markout today, 6c / 1.4c | −$0.02/h |
| half the campaign figure | −$0.03/h |

**The loss assumption decides nothing tonight.** Where the reward is
near zero, no loss figure makes a market positive. Where the reward is
positive, the market is positive at every loss figure tried.

## Where the positive markets are, and what rejects them

| Market | Reward /h | Net /h | Rejected by |
|---|---|---|---|
| CS2 `alka-ln` (in play, pool $26.7/h) | 6.61 | +5.56 | esports rule, competition, touch depth (bid 0.4 contracts) |
| CS2 `tet-axe` (in play) | 1.88 | +0.83 | esports rule, competition, touch depth |
| eBattles FIFA markets (in play, pool $2/h) | ~0.9 | ~+0.9 | esports rule, competition, touch depth |
| UTR `cekkuz-avdfil` (in play, 0.79/0.84) | 1.85 | +0.35 to +1.10 | price band, touch depth (bid 0.1 contracts) |
| 30 markets before the match (Setka ×22, ATP doubles ×1, eBattles, NFL props) | 0.4–1.1 | +0.0 to +0.76 | slate (in play only); touch depth on 28 of the 30 |

**Every positive market outside esports has a dust touch.** The best bid
and offer are 0.1–0.8 contracts, and the real book sits 5–12 ticks further
out. Examples:
- `setkameua-usdan-slavad`: 0.44 (0.5) / 0.45 (0.5) on top, real book
  0.32 / 0.57.
- `atpdb-delanedelane`: 0.35 (0.3) / 0.36 (0.5) on top, real book
  0.20 / 0.88.

The reward is large because almost nothing qualifying rests near those
dust quotes. The touch-depth gate exists for exactly this book: a bid
next to a half-contract quote sits far above the real bid. That is the
correction to my earlier proposal. **Revised Campaign B as written
(depth 3) would have quoted none of these markets, including the 21:13
doubles books.** Its "positive" figures assumed quoting them.

## Is the controller rejecting attractive inventory?

- **Price band 0.30–0.70:** tonight it costs one market (UTR `cekkuz`).
  The bot's own per-bid range of 0.20–0.92 already keeps it out of the
  cells where RN1 lost (under 0.20). The selection band is the narrower,
  more arbitrary of the two.
- **Loss assumptions:** not binding (see above).
- **International price:** not required, so it rejects nothing at
  selection.
- **Competitions:** what they reject has no programme or no reward,
  except esports. Esports holds the largest positive market (CS2
  `alka-ln`). Its settlement terms, read from the venue listing: "settle
  to the winner of the … CS2 match … If the match ends in a tie, the market
  will settle to $0.50. If the match does not begin due to a forfeit or
  disqualification, the market will settle to the last fair market price."
  The bot's esports late rule needs the match format (it fails closed
  without it), and today's listing shows only "Map 1".
- **Slate (in play only):** shuts out every positive market before the
  match.
- **Touch depth:** the binding filter on everything that is positive. It
  is not arbitrary. Whether dust books pay is the unmeasured question:
  takers who would only get 0.32 from the real book sell to a 0.43 bid.
  Tonight's in-play fill and loss rates were measured in real books.

## Targeted corrections, for approval (settings only; no code; hard limits unchanged)

| # | Setting | Now | Proposed | What it buys | Risk |
|---|---|---|---|---|---|
| 1 | `PMUS_TWO_SIDED_SLATE` | live | both | the 30 positive markets before the match, and rotation into in-play doubles as they start | fills before the match are unmeasured; the kickoff pull (300 s) still withdraws openings |
| 2 | `PMUS_TWO_SIDED_MIN_TOUCH_QTY` | 3 | 0 | lets the net ranking quote dust-touch books, which is where every positive market is | the bid can sit far above the real market; adverse fills are likely when the dust lifts |
| 3 | `PMUS_RN1_NET_SIZES` | 1,2,5 | 1,2 | caps the clip at 2 contracts while #2 is unmeasured | less reward (in thin books 2 contracts take most of it) |
| 4 | `PMUS_TWO_SIDED_BAND_LO` / `_BAND_HI` | 0.30 / 0.70 | 0.20 / 0.80 | in-play markets like UTR `cekkuz`; matches the per-bid floor of 0.20 | near-decided markets move in jumps; the jump and volatility pauses still apply |
| 5 | esports (CS2 match winner) | excluded | **not proposed tonight** | the largest positive market | needs the format rule decided; separate decision |

Unchanged: account limit $20, campaign loss stop $10 (counting resting
bids), daily halt $10, sports lane $20, per-market and campaign unpaired
caps, at-risk budget, pair-cost limit, maker-only, no taker, jump and
volatility pauses, kickoff and late rules, reconciliation, watchdog.

## What this run would establish

With #1–#4 and two-contract clips, the worst case per market is about
$2–3 resting on the dearer side. Modelled reward is about $0.5–1 an hour
per market across 2 markets, so about $1–2 an hour. That clears the $1
minimum in an hour if paid. Fill losses in dust books are unknown: they
could exceed the reward. The run measures them (fill markouts) and the
first paid rows at this size. The loss stop ($10) bounds the worst case.
