# l5 Polymarket replay lane

Status: **READ-ONLY / OBSERVATIONAL**

Target public profile wallet: `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`

## Purpose

Reconstruct the chronology of public BTC/ETH 5-minute and 15-minute Polymarket trades to test, without hindsight, the working hypothesis that the trader:

1. repeatedly scales into a currently favored/undervalued outcome;
2. may switch sides when the market reverses;
3. sometimes creates matched Up/Down inventory whose weighted combined acquisition cost is below $1;
4. may retain unmatched directional residual inventory through resolution.

Public fills support or reject behavioral patterns; they do **not** reveal the trader's private fair-value model or prove maker/taker intent.

## Safety boundary

`src/l5_polymarket_replay.py` uses only the unauthenticated public Polymarket Data API. It has no order submission, cancellation, credential, or balance code.

## Run

```bash
python -m src.l5_polymarket_replay --latest-markets 20
```

Optional bounded history:

```bash
python -m src.l5_polymarket_replay --start <epoch_seconds> --end <epoch_seconds> --latest-markets 100
```

## Output

Each market includes chronological steps with:

- timestamp and seconds to expiry;
- BUY/SELL and Up/Down outcome;
- price and size;
- cumulative Up/Down quantity;
- cumulative Up/Down VWAP;
- matched quantity;
- weighted matched-pair cost;
- gross `$1 - pair_cost` where both sides are present;
- remaining directional side and quantity.

The summary counts repeated-entry markets, buy-side switches, sub-$1 matched-inventory observations, and buys at or above 60c. These are descriptive statistics only and must not be called realized profit or a proven trading edge.
