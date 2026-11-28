# RN1 and BreakTheBank — are they profitable without rebates, and what would replicating them take?

2026-11-21. Public data only (Polymarket international: `/v2/user-pnl`, `/v2/activity`, CLOB `prices-history`).
Label: HISTORICAL, another trader's results on another venue. Nothing here is our P&L.

## 1. Profitable without rebates: yes, both (platform's own series)
| | RN1 lifetime | RN1 30d | BreakTheBank lifetime | BreakTheBank 30d |
|---|---|---|---|---|
| realized market P&L (after fees) | +13.54M | +0.49M | +8.87M | +0.79M |
| maker + taker rebates | +1.90M | +0.21M | +0.56M | +0.17M |
| rewards | +0.37M | +0.05M | +0.12M | +0.06M |
| volume | 1.54B | 130M | 450M | 135M |
RN1 keeps about 0.4% of the platform's share-count `volume`, which is about 0.7–0.8% of dollars actually spent
(30-day reconciliation: +$431,949 on $61.2M bought, +0.71%; see `RN1_30D_FINDINGS.md`). Bonereaper, by the same
series, is negative on trading.

## 2. RN1, 19–21 Nov (2.85 days, 25,243 trades, $4.34M) — `RN1_RESULT.txt`
- Buys only; never sells; ~8,900 buys a day, median $20, across ~4,000 markets (NBA, CFB, soccer, tennis, esports).
- Buys both outcomes in 41% of markets, which hold 86% of his dollars. The second side comes a median 22 minutes
  after the first; only 17% of dollars are within a minute of a buy on the other side. Markets are worked for
  about the length of a game. So this is live, in-game trading, not simultaneous arbitrage.
- Pair cost is NOT tight: 27% of matched shares below 0.90 (+$139k), 38% above 1.02 (−$97k); net +$62k locked
  on 2.30M matched shares (average 0.973). 40% of shares in those markets stay unmatched.
- Price around his buys (400 tokens = 69% of dollars, 1-minute public prices): he pays 1.3c BELOW the public price
  30 s earlier, and the price is then +0.8c at 2 min, +2.2c at 15 min, +2.7c at 30 min in his favour.
  Our own 55 live BTC5m fills moved 2.0c AGAINST us in 30 s. That sign difference is the whole edge.
- He buys at every price level, including after large moves in both directions, and is right afterwards in each band.

Reading (inference): RN1 knows fair value better or sooner than the book — a live-score / sharp-line driven
quoter — and pairs are a by-product of being on the right side twice. A pair-cost rule alone does not reproduce it.

## 3. BreakTheBank, 14–21 Nov (12,301 trades, $17.5M) — `BREAKTHEBANK_RESULT.txt`
One-sided (both outcomes in 5% of markets), mean $1,426 a trade, concentrated on NFL/NBA/UFC moneylines and
spreads at 0.2–0.8. This is forecasting with size. Not replicable without his model. Not pursued.

## 4. What transfers to us
We trade Polymarket US, not international. The replicable form of RN1 is: have a fair value for a PMUS sports
market from a sharper source, and only buy when the PMUS price is on the wrong side of it. Candidate sources:
(a) the international Polymarket price for the same game (public, free, deeper book); (b) a sharp sportsbook feed.
The decisive read-only test: when international moves, does the PMUS book lag by long enough to buy, and is the
buy still positive after the PMUS taker fee (promo: 0.0695·p(1−p) with 70% rebate to 31 Dec) or as a resting bid?

## Limits
Three days of one wallet; 1-minute prices, so sub-minute timing is not resolved; maker/taker is not flagged in the
public feed; the settled-market sample in the profile omits losing positions (no REDEEM row) and is not used above.

## Addendum — 30-day reconciliation (`RN1_30D_FINDINGS.md`, `RN1_30D_RESULT.txt`)
Settled P&L rebuilt from 274,695 fills, losers settled from final prices: +$431,949 on $61.2M (+0.71%), within 2.6–4.5%
of the platform's own figure (daily correlation 0.996). About 1.2 standard errors from zero treating each game as a draw.
- 88% of dollars are maker fills (+0.80%); taker fills are flat after fees (+0.04%).
- Matched pairs +$869k (pair cost 0.9766); residual −$388k; one-sided markets −$49k.
- ALL of the profit is in fills over $500 (+1.04%). Fills under $500 are flat to negative, and markets where he spent
  under $100 in total lost 10.8%. We would trade small: this is the most important caution for transfer.
- 93.5% of dollars are bought after the game starts.

## Addendum — does the international market lead Polymarket US? (`CROSS_VENUE_20261121_RESULT.txt`)
Recording 20:27–22:17 UTC 21 Nov, 32 live/near-live games (CFB, NBA, NHL), both venues every 3 s, public quotes only.
Data: `cross_venue_20261121_2027_2217.jsonl.gz`. Label: MODELLED marks on quotes — not fills, not P&L. One evening.
- International leads: US mid changes follow international changes with correlation 0.21–0.31 at +3 to +30 s, ~0 the other way.
- Before 91% of US mid drops of >=1c (259 events) the international mid was already >=0.5c lower. Mirror case: 34%.
- US ask at least 0.5c BELOW the international bid: 273 signals over 12 games. Buying that ask, marked at the US mid
  60 s later, net of the promo taker fee: mean +2.1c, median +1.7c, 71% positive. At >=1c: mean +3.3c, 85% positive (178 signals).
- Merely touching (gap 0) is not a signal: median −0.8c, 33% positive.
- A resting US bid more than 1c below the international mid sits 1.4c under the US mid a minute later; a bid at or above
  the international mid only 0.2c — i.e. bids above the international mid are the ones about to be run over.
Not yet known: size available at the US ask during a cross (second recording running with sizes), our own order latency,
whether the effect persists on other days, and whether a MAKER version (bid only at/below international, pull on its move)
captures it — taker trading is not authorized and the promo fee ends 31 Dec.

## Addendum — second two-venue recording, with sizes (`CROSS_VENUE_20261121B_RESULT.txt`)
22:18–00:15 UTC, 41 usable games (CFB, NHL, NBA), 3 s. Data: `cross_venue_20261121_2218_0015_sizes.jsonl.gz`.
Replicates the first recording on a different set of games:
- 93% of US mid drops >=1c (357) were preceded by a lower international mid (mirror: 31%).
- US ask >=1c below the international bid: 231 signals in 20 games (119 an hour); +2.8c a share at 60 s net of the promo
  taker fee, median +2.2c, 80% positive. At >=0.5c: 357 signals, +1.7c, 69% positive. A touch is still not a signal.
- Size at the US ask at the signal: median ~3,500 contracts (p25 57, p10 1). International bid: median ~580.
- The gap stays open a median 9 s (p25 3 s).
- MODELLED, quotes not fills: a buyer capped at 50 contracts a signal marks +$276 over 116 min on $4,458 bought;
  capped at 200, +$1,040 on $16,611.
- A resting US bid at or above the international mid +0.5c ends up ABOVE the US mid a minute later (−0.2c): those are
  the bids that get run over. Bids >1c below sit 2.0c under.
Not established: that the lagging ask can actually be hit before it is pulled (no venue in-play delay is documented, but
none was tested); our order latency (the lane takes three round trips an order); persistence across days; the edge after
31 Dec when the 70% taker-fee rebate ends (at the 0.0695 fee unrebated, the >=1c edge is about +1.0c at p=0.5).
The decisive next evidence is fills, not more recordings: a bounded live taker probe needs explicit owner authorization.
