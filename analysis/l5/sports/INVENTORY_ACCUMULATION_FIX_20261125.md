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

## Update 17:57 UTC — release `5b4584db4` supersedes `5425d94d3` and `0abb52a38`

Release `5b4584db418787d183afba7d58e48513b6766484` (branch
`release/pmus-sports-rn1-5b4584db4`, code `b88375075`). Regression 5,658
passed, the same 24 unrelated failures.

Two defects found by replaying the live configuration before resuming:

1. Under the in-play-only setting, a market already tracked from before
   still placed pre-game bids (live 17:08 UTC). Fixed: no opening or
   extension in a pre-game market under the live slate.
2. One fill collapsed both ladders to a single completing bid: the filled
   side's cooldown cancelled its deeper bids, and the other side became a
   lone completion. Fixed: a side in cooldown keeps its resting deeper
   bids (unless a pause or other block applies); behind a completion the
   ladder rests ordinary opening bids, each cheaper than the completion.

Confirmed by test, on the live configuration (in play, 2 contracts, 3
levels 2 ticks apart, $3 / $8 / $6 caps):

| Requirement | Test |
|---|---|
| Several levels rest at once in a live market | 3 YES + 3 NO resting |
| Same-side inventory builds through successive fills | YES fills at 0.47, 0.45, 0.43: 6 contracts, average 0.45, $2.70 unpaired; then no further YES bid (next clip would pass $3) |
| A filled level is replenished | after the 10 s cooldown the 0.47 level is placed again; the deeper bids stayed |
| Both sides accumulate independently | after a YES fill, NO rests its completion plus two cheaper opening bids; after pairs form both sides ladder again |
| Pauses still pull everything | a volatility pause removes the whole ladder, cooldown or not |
| Caps hold through any fill sequence | filling every YES bid pass after pass never takes unpaired above $3 |
