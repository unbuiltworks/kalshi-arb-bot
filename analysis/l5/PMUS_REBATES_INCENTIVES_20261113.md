# Polymarket US maker rebates and incentives for BTC15m (checked 2026-11-13 ~15:40Z)

Sources read directly: docs.polymarket.us/fees (schedule effective 2026-11-11 13:00 ET),
docs.polymarket.us/incentives/{liquidity,market-maker,volume}.md, and the public
GET gateway.polymarket.us/v1/incentives.

* Maker rebates exist but start at **$10,000,000** of 30-day maker volume (Tier 2,
  coefficient -0.0035, about 5% of the taker fee). Tier 1 (< $10M) gets **no maker rebate**.
  Tier 3 $50M-$200M (-0.0070), Tier 4 $200M+ (-0.0104). Same coefficient in crypto markets.
* Taker rebates start at $250,000 30-day taker volume (3%), up to 30% at $50M+.
* "Accelerated Tier Placement": verifiable proof of 30-day volume on another prediction
  market can place an account in the matching rebate tier.
* Liquidity Incentive Program: per-market pools, scored every second by
  DiscountFactor^(ticks from best) x size. `GET /v1/incentives?symbols=` returned
  **no programs** for cpc-btc-updown-15m-2026-11-13-1530z/1545z/1600z. A 40-page walk of all
  programs (4,000 rows: AI/tech/finance/people markets) contained no btc or cpc- market.
* Market Maker Program: approval-based; contact institutional@qcex.com.

Fee rebate at p=0.50 for Tier 2: 0.0035 x 0.25 = $0.000875 per contract.
