# Inventory accumulation: what blocked it, what changed — 25 Nov 2026

Release candidate (not deployed): `5425d94d336d7ff13c7dafd55e55d64e7b369aa5`,
branch `release/pmus-sports-rn1-5425d94d3`, code `392d18ef8`
(= running `bc3a5b6b9` + read-only manual-order id log + price ladder).
Regression: 5,650 passed, the same 24 unrelated failures as the baseline;
7 new tests.

## Answers, from the code and today's logs

| Question | Answer |
|---|---|
| Does existing inventory block more same-side purchases? | No. A side keeps bidding while it holds inventory, bounded by its average cost + 2c, the 10 s cooldown and the $3 / $6 unpaired caps. |
| Do completing orders block opening orders? | No. The heavier side keeps its own bid; the lighter side's one bid is priced to complete the pair at 0.97 or better. |
| Are filled orders replenished? | Yes, 10 s after a fill on that side. |
| Can we hold several price levels at once? | **No — one bid per side per market.** Fixed (below). |
| Is churn preventing accumulation? | Partly: an opening bid lived 28 s at the median today; every 1-tick move cancels and re-places. The ladder keeps bids that land on another level. |
| Are inventory limits used efficiently? | No: today's peak unpaired was $1.57 of $6 and the account figure $3.39 of $13. Limits never bound. |
| Does quoting stop after a pair? | No: a balanced market quotes fresh entries. |
| Can capital move across positions? | **No: four pre-game UTR markets held all four slots for over two hours with bids that did not fill**, so nothing else was selected (no selection pass ran after 14:33). Existing setting fixes this: `PMUS_TWO_SIDED_SLATE=live`. |

## The change

`PMUS_RN1_LADDER_LEVELS` (default 1 = as before), `PMUS_RN1_LADDER_STEP_TICKS`
(default 2). Behind each opening or same-side extension bid, up to N−1
further bids of the same size rest, each 2 ticks deeper. A level is added
only while the market's worst case with every level filled stays inside
the per-market spend ($8), per-market unpaired ($3) and campaign caps; the
final gate, reservations and the account limit check every order.
Completions are never laddered.

## Validation configuration (for authorization)

| Setting | Now | Proposed |
|---|---|---|
| Build | `bc3a5b6b9` | `5425d94d3` |
| `PMUS_RN1_LADDER_LEVELS` / `_STEP_TICKS` | 1 / — | 3 / 2 |
| `PMUS_TWO_SIDED_SLATE` | both | live |
| Everything else | | unchanged: 2 contracts, 4 markets, competitions, depth 3, $13 account limit, $3 / $6 unpaired, $8 a market, $4 loss stop, $5 daily halt, maker-only, window to 19:58 UTC |

Effect of the caps at 2 contracts: a side's ladder holds about $3 of bids
at most, so each side gets 2–3 levels; the $6 campaign unpaired cap allows
full ladders in about two markets at once.

Risk: the ladder buys more as the price falls. Today both same-side
additions lost (11 and 18.5c within five minutes). The worst case per
market stays $3 unpaired; for the campaign, the $4 loss stop.
