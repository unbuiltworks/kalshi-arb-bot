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
RN1 keeps about 0.4% of volume from trading. Bonereaper, by the same series, is negative on trading.

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
