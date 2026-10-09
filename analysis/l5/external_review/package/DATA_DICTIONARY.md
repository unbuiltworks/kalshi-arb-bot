# L5 fill corpus — data dictionary

Four files. Everything here is **observed public on-chain/exchange activity
of one anonymous trader**, as recorded, with identity removed. No labels,
no features and no conclusions of ours are included.

## `l5_fills_sanitized.csv.gz` — 760,817 rows, one per executed fill

| column | type | meaning |
|---|---|---|
| `market_id` | string | stable pseudonym for one market (`M` + 10 hex). Not resolvable to a real market by design. |
| `asset` | string | underlying, where the market structure names one (`BTC`, `ETH`, `SOL`, …). Empty when unknown. |
| `window_seconds` | int | the market's contract window length in seconds (300 = 5-minute market, 900 = 15-minute). Empty when the structure did not state one. |
| `fill_index` | int | 0-based position of this fill within its market, in time order. |
| `timestamp_utc` | ISO 8601 | fill time, **absolute UTC, unperturbed**. |
| `epoch_s` | int | the same instant in Unix seconds. |
| `side` | `A` / `B` | the two complementary outcomes of the binary market. `A` is the market's first-listed outcome, `B` the second. A matched A+B pair pays exactly 1.00 at resolution. |
| `price` | float | execution price per share, 0–1. The cost of *that* side. |
| `size` | float | shares executed. |
| `regime` | `early` / `late` | whether the fill is before or after 2026-06-23 (see below). Provided only because the split is used in the questions; it is a date cut, not a label. |

Every fill is a **buy**. This trader's recorded activity contains no sells;
positions are exited by holding to resolution.

## `l5_markets_sanitized.csv` — 17,046 rows, one per market

`market_id`, `asset`, `window_seconds`, `n_fills`, `first_epoch_s`,
`last_epoch_s`.

## `l5_representative_sequences.json`

Six **complete** fill sequences, readable end to end, three per regime,
chosen by sequence *structure* (median length, busiest, shortest with at
least two fills) and explicitly **not** by profitability — a reviewer handed
only winning sequences would be reviewing the selection rather than the
trader. Each carries `selected_because`.

## `MANIFEST.json`

Row counts, the regime break, and the corpus span
(2026-03-19 → 2026-08-31 UTC).

---

## What was removed, and why

Wallet address, username, transaction hashes, condition ids, real market
slugs, and every identifier of the reviewing party's own accounts. The
intent is that the corpus cannot be traced back to a public profile whose
existing commentary could substitute for the reviewer's own analysis.

## What was deliberately **not** touched

Prices, sizes, sides, and timestamps to the second. Rounding a price or
jittering a time would destroy the sequential structure that is the whole
object of study. Timestamps stay absolute because inter-fill gaps,
time-of-day effects and the mid-corpus regime change are first-order facts.

## The one structural fact you need

These are binary prediction markets. For a given market, side `A` and side
`B` are complementary claims: exactly one pays 1.00 per share at
resolution, the other 0.00. Therefore **one share of A plus one share of B
pays exactly 1.00, with no directional risk**, and the cost of assembling
that pair is `price_A + price_B`. Holding unequal amounts of A and B leaves
a directional residual.

## Known limitation of this data

These are **fills only**. Orders that were placed and never executed leave
no trace here, so nothing in this corpus reveals the trader's resting
quotes, cancellations, order-to-fill ratio, or whether any given fill was
passive (maker) or aggressive (taker). Any claim about order *placement*
is an inference, and should be labelled as one.
