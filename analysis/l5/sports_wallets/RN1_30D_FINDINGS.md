# RN1 — 30-day reconciliation from its own public activity

2026-11-21. Wallet `0x2005d16a84ceefa912d4e380cd32e7ff827875ea` (Polymarket international). Public data only.
Label: HISTORICAL, another trader on another venue. Nothing here is our P&L. Full tables: `RN1_30D_RESULT.txt`.

Files: `rn1_activity_20261022_20261121.jsonl.gz` (315,550 rows, 2026-10-22 00:00 → 11-21 21:19 UTC, the API paginated
all the way back), `rn1_taker_trades_20261022_20261121.jsonl.gz` (27,279 taker fills), scripts `fetch_activity.py`,
`fetch_taker_trades.py`, `fetch_gamma_markets.py`, `rn1_30d.py`. The gamma metadata cache (36,794 of 36,795
conditions) is in the session scratchpad, not the repo; `fetch_gamma_markets.py` rebuilds it in about 12 minutes.

## VERIFIED (computed from public rows)
- **Activity**: 274,695 trades, all BUY, $64.11M; 40,747 redeems, $64.25M. No sells, merges, splits or conversions.
- **Settlement method**: payout = shares × gamma final outcome price for every closed condition, so losers count.
  For all 23,957 included conditions that have a REDEEM row, redeemed USDC equals the computed payout (none off by
  more than 0.1%). 11,368 included conditions ($2.00M cost) have no redeem and paid $20: these are the losers a
  redeem-only tally drops. Redeem-only would have shown +$2.43M; the correct figure is **+$431,949**.
- **Coverage**: 35,325 conditions, 7,739 games, $61.20M = 95.5% of the window's buy dollars. Excluded: games starting
  before 10-23 12:00 UTC (4.0%, cost may be incomplete), not yet closed (0.5%). The 36 h rule is safe: only 0.06% of
  dollars are bought more than 12 h before game start.
- **Result**: +$431,949 net of taker fees, before rebates/rewards = **+0.71% of dollars bought**. 47% of conditions
  made money. 18 of 30 days positive; mean +$13.8k/day, sd $62.6k/day.
- **Reconciliation with the platform's `realized_market_pnl`** (by market close day, all resolved conditions):
  30 days 10-22..11-20: platform +$433,076, mine +$413,485, **gap −$19,591 (−4.5%)**; 28 days 10-24..11-20:
  platform +$386,670, mine +$376,721, **gap −$9,949 (−2.6%)**. Daily correlation 0.996. The platform figure is net
  of fees (treating it as before fees leaves a +$120k gap). Rebate/reward rows in the file equal the platform deltas
  to the dollar (maker $148,113, taker $71,293, rewards $50,514).
- **Maker/taker**: data-api `/v2/trades` returns only the wallet's taker fills by default (`takerOnly=true`);
  `takerOnly=false` returns all. Joined on (transaction hash, token). Independent confirmation: `usdc_size − size×price`
  is exactly zero on every non-taker row and equals the gamma fee schedule (rate × shares × p × (1−p)) on taker rows:
  $141,649, against the platform's fees_paid change of $141,576. **Taker = 9.9% of fills, 12.0% of dollars;
  maker = 88%.** Taker fee averages 1.84% of taker notional. Maker buys +0.80% per dollar ($429k); taker buys
  +0.04% after fees ($3k).
- **Pairs vs residual** (both-sided conditions: 44% of conditions, 89.5% of dollars): matched pairs +$868,753 on
  $36.2M (average pair cost 0.9766); residual −$387,513 on $18.6M; one-sided conditions −$49,291 on $6.4M.
  The residual side won in 49.6% of conditions.
- **Timing**: 93.5% of dollars are bought after game start. Second side appears a median 24 minutes after the first
  (6% within 60 s, 22% over an hour), after a median of 2 same-side buys; 96% of second sides are opened in-game;
  78% of both-sided conditions are opened maker on both sides.
- **Size**: median condition $198, mean $1,732, p90 $3,530; median fill $30. Fills over $500 are 72% of dollars.

## ESTIMATED
- **Statistical weight**: treating each game as one independent draw, the standard error of the 30-day P&L is about
  $355k, so +$432k is 1.2 standard errors from zero. Thirty days of this wallet do not by themselves establish a
  positive trading edge before rebates. The matched-pair component (+$869k, se $134k) is the only large cell clearly
  different from zero; most league, market-type, timing and size cells are within 2 se of zero.
- Pre-game/in-game split depends on gamma `gameStartTime`, which can be a scheduled rather than actual start
  (tennis especially).
- Rebates and rewards (0.42% of dollars bought, $270k) are credited in bulk rows and cannot be attributed to
  conditions; adding them to trading P&L gives about $702k for the window, a sum of a per-condition figure and
  window totals over slightly different sets.

## UNKNOWN
- Order-level behaviour: resting quote size, quote price relative to the book, cancels, fill rate. Rows are fills; a
  maker fill's size is set by the incoming order, so "small fill" is not "small order".
- Whether one-sided positions in three-way (negRisk) events are hedged by another condition of the same event
  (5,737 one-sided negRisk conditions, $2.06M); cross-condition pairing is not modelled.
- Source of the $10k–20k reconciliation gap. Candidates not tested: the platform's day boundary/realisation time
  versus gamma `closedTime`, and conditions with buys before the window.
- Why small conditions lose (see below): the condition's total dollars is an outcome of his own decisions, not
  something known when the first buy is made.

## What looked wrong or needs care
- `FINDINGS.md` §1 quotes volume 1.54B / 130M: that is the platform's `volume` field (shares-like). USDC bought is
  `volume_usdc`, $64M in the window. P&L as a share of dollars is 0.7%, not 0.4%.
- The earlier profile's "SAMPLE settled" (+7.9% of cost) was redeem-only and overstated profit by an order of
  magnitude; this file replaces it.
- 39 byte-identical TRADE rows exist; they are real separate fills (the redeem cross-check balances with them in).
- Pair cost is not a selection rule: 35% of matched dollars were paired above 1.04 (−$1.52M) and 20% below 0.90
  (+$1.99M); the net pair gain is the difference of two large numbers.
- Conditions with under $100 in total lose 10.8% of dollars (−$38k, se $8k, 14,411 conditions), driven by prices
  below 0.40. Fills under $20 overall are +0.3% (not different from zero); maker fills under $20 at 0.20–0.95 are +2.5%
  ($11.8k on $466k, se $5.1k) — one cut among many, treat as weak.
