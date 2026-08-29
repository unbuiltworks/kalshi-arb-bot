# README FOR ANALYSIS — L5 Polymarket historical reconstruction

Read-only exporter and analyzer for Polymarket wallet
`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68` (profile `l5zn1bwom8etsk`), scoped to
**BTC and ETH 5-minute and 15-minute Up/Down markets**.

Code lives in `src/l5/`. Tests live in `tests/test_l5_*.py`.

---

## Status: no live data has been retrieved yet

The code is complete and tested, but **the CSVs in `data/l5/` have not been produced from
real data**. The environment this was built in blocks outbound HTTPS to
`data-api.polymarket.com`, `gamma-api.polymarket.com` and `clob.polymarket.com` at the
egress proxy (HTTP 403 on CONNECT — an organization network policy, not a Polymarket rate
limit). Nothing in `data/l5/` is real; the only tables present are the clearly-labelled
synthetic ones under `data/l5/sample/`.

To produce the real tables, run `python -m src.l5 all` from a machine with ordinary
internet access. No credentials are required.

---

## Second input path: screenshot transcriptions

Because the API is unreachable here, `src/l5/manual.py` ingests hand-transcribed
activity-feed rows from `data/l5/manual/*.json` and converts them into **exactly the record
shape the API returns**, so the whole downstream pipeline runs unchanged. Screenshot rows
carry `source=screenshot` and are never merged into the API sources (they have no
transaction hash, so cross-source dedup cannot match them — a later fetch can be compared
against them instead of double-counting).

Three properties of the feed drive that module:

1. **Window bounds are exact.** Titles name the span — `"Bitcoin Up or Down - August 29,
   5:00PM-5:15PM ET"` — so the window length is *derived*, not guessed, and
   `window_source` is `metadata`. This also exposed a gap in the classifier: the real
   titles carry a clock range, not a `"5m"` label, so `window_minutes_from_time_range()`
   was added and is trusted above the label regex.
2. **Displayed cents are rounded.** A row reading `Up 92¢, 30.5 shares, $28.17` is really
   0.9236. `dollars / shares` is authoritative; the badge is kept as
   `price_cents_displayed` and disagreements are counted.
3. **Times are relative and coarse.** `"4m ago"` against a clock legible only to the
   minute bounds a fill to a **two-minute interval**, not an instant. Seeing the same row
   in two screenshots narrows it by intersection. Each row carries
   `timestamp_uncertainty_s`. Ordering therefore comes from feed position (`feed_seq`),
   which is exact, never from the derived timestamp.

Sequential screenshots are stitched by their **seam** — the longest suffix of one page
matching the prefix of the next. Unlike a global dedup this preserves a fill that genuinely
repeats (identical price and size, two separate cards, which does occur), and a
non-contiguous pair of pages is flagged as a gap rather than silently joined.

### Partial windows

A window straddling either edge of the capture is missing fills, so its final inventory,
matched quantity and balanced-finish flag describe a fragment. Those carry
`capture_complete=false` with a `capture_note`, and **every window-level statistic
(questions 3–9) excludes them**. Per-trade statistics (question 2) use every fill.

## Read-only guarantees

- Every network call is a `GET`. `src/l5/http.py` has no code path that can `POST`,
  `PUT`, `PATCH` or `DELETE`.
- No API key, signature, private key, or session cookie is read or sent. The package does
  not import `config.settings`, `src.auth`, or any credential source.
- No order entry, cancellation, or any other mutation of any venue.
- The SOL executor and every other service in this repository are untouched — `src/l5/`
  imports nothing outside itself and the standard library.
- Only markets this wallet actually traded are looked up. There is no broad market crawl.

## Dependencies

Standard library only (`urllib`, `csv`, `json`, `math`, `random`, `dataclasses`).
Nothing was added to `requirements.txt`. `pytest` is needed for the tests.

---

## Running it

```bash
# Everything: fetch -> CSVs -> analysis -> findings document
python -m src.l5 all

# Or step by step
python -m src.l5 fetch      # public APIs -> data/l5/cache/*.jsonl   (the only online step)
python -m src.l5 build      # cache -> the three CSVs                (offline)
python -m src.l5 analyze    # cache -> CSVs + l5_analysis.json + L5_STRATEGY_FINDINGS.md
```

Useful flags:

| flag | effect |
| --- | --- |
| `--cache-dir` | where raw JSONL lives (default `data/l5/cache`) |
| `--output-dir` | where CSVs land (default `data/l5`) |
| `--start-ts` / `--end-ts` | restrict the `/activity` sweep to a unix-second range |
| `--append` | add to the existing cache instead of truncating it |
| `--findings PATH` / `--no-findings` | control the generated markdown |

`build` and `analyze` never touch the network, so once the cache exists the whole analysis
is reproducible offline and deterministically (the bootstrap is seeded —
`BOOTSTRAP_SEED` in `src/l5/config.py`).

---

## Data sources

| Endpoint | Used for | Limit that matters |
| --- | --- | --- |
| `data-api.polymarket.com/activity` | the fullest fill history | `offset` stops being honoured a few thousand rows deep; the fetcher walks backwards with an `end` cursor instead |
| `data-api.polymarket.com/trades` | executed fills, cross-check | offset paging only, no time cursor — marked truncated if it hits the cap |
| `data-api.polymarket.com/positions` | `realizedPnl` per market | closed positions may age out |
| `gamma-api.polymarket.com/markets` | market metadata used to derive window geometry | queried only for condition IDs this wallet touched |

`/trades` is called with `takerOnly=false`. The default hides fills where this wallet was
the *maker*, which for a two-sided strategy is likely most of them.

### Raw cache format

`data/l5/cache/*.jsonl` — one line per API response, each an envelope:

```json
{"endpoint": "...", "params": {...}, "fetched_at": 1788033600, "payload": [ ... ]}
```

The verbatim payload is kept, so any derived table can be rebuilt without re-fetching, and
a reviewer can check the parsing against the bytes that were actually served.
`fetch_manifest.json` alongside it records page counts, record counts, the observed
timestamp range, and any truncation with its reason.

---

## Methodology

### Window classification is validated, not read off the title

A market is in scope only if **both** hold:

1. **Asset** — exactly one of BTC/ETH matches the slug, question, event slug or event
   title on a word boundary, and no other asset token appears. `btc vs sol` is ambiguous
   and excluded; `bethesda` does not match `eth`.
2. **Window length** — the market's own `endDate − gameStartTime` (falling back through
   `eventStartTime`, `startDate`, `startTime` / `endTime`, `resolutionTime`, then the
   parent event's fields) lands in 300s ± 30s or 900s ± 60s.

The title is parsed too, but only as a **cross-check**. If timestamps say 5m and the title
says 15m, the timestamps win and `window_conflict` is set to `true` on every affected row.

If no usable start timestamp exists, the window length falls back to the title and the
start is reconstructed as `end − length`. Those rows carry
`window_source=text_fallback` and `bounds_reconstructed=true`, and both are counted in
the coverage table so a strict analysis can drop them.

Hourly and daily crypto Up/Down markets are excluded by this rule — they fail the
duration band. So are SOL/XRP/DOGE and every other asset.

### Grouping

Windows are keyed on **`conditionId`**, never on the title or the clock. Two consecutive
5-minute BTC windows with near-identical slugs stay separate.

### Deduplication

Two stages, because the two failure modes are different:

1. **Within a source** — byte-identical records collapse. These are the overlap the
   backwards time cursor deliberately creates (it steps back to the oldest timestamp seen
   rather than one second past it, because losing a fill is worse than a duplicate).
2. **Across sources** — fills are grouped by identity
   (`transactionHash`, token id, `conditionId`, side, timestamp, price, size), and for
   each identity we keep the rows from whichever endpoint reported it *most* times. So the
   same fill served by both `/activity` and `/trades` is counted once, while a genuinely
   repeated fill inside one transaction is not silently collapsed.

Both counts are reported in `normalize_stats`.

### Outcome resolution

`Up`/`Down` text wins. A `Yes`/`No` market is mapped only through that market's own
`outcomes` array and the row's `outcomeIndex`, or failing that by matching the row's token
id against `clobTokenIds`. If none of those resolve, the row is **dropped and counted** —
never guessed from the question wording.

### Inventory accounting

Weighted-average cost basis per side:

- **BUY** — adds shares and dollars to the side.
- **SELL** — removes shares at the basis prevailing *before* that sell, and books the cash
  separately. A sale therefore never moves the average price of what is still held, which
  is what a matched-pair acquisition cost needs.
- A sell exceeding the visible position sets `oversold=true` rather than being smoothed
  away. Public data cannot distinguish a genuine short from a fill we failed to see.

Derived per trade:

| quantity | definition |
| --- | --- |
| `matched_qty` | `min(up_qty, down_qty)` — the fully hedged part of the book |
| `matched_pair_cost_per_share` | `up_avg_price + down_avg_price`; a pair under $1.00 settles at $1.00 |
| `matched_pair_cost_total` | `matched_qty × matched_pair_cost_per_share` |
| `excess_qty` | `up_qty − down_qty`, signed; positive is long UP |
| `dominant_side` | sign of `excess_qty` (`UP` / `DOWN` / `BALANCED`) |

### Definitions used in the summary

- **Price bands** — `lt70` `[0, 0.70)`, `70to80` `[0.70, 0.80)`, `80to90` `[0.80, 0.90)`,
  `gte90` `[0.90, 1.0]`. Closed below, open above.
- **Balanced finish** — residual `|up − down|` at or under 1.0 share, or at or under 5% of
  the larger leg.
- **Realized P&L** — the sum of `realizedPnl` across both tokens of the condition, from
  Polymarket's own position rows. **Excludes liquidity rebates, maker incentives and gas.**
  Windows without it carry `pnl_source=unavailable` and are excluded from the P&L split,
  not treated as zero.

### Statistics

`src/l5/stats.py`, standard library only, all validated against known closed forms in
`tests/test_l5_stats.py`:

- Mann-Whitney U (tie- and continuity-corrected normal approximation) with rank-biserial
  effect size, for two-sample comparisons.
- Spearman rank correlation with a Student-t p-value via the regularized incomplete beta.
- Pooled two-proportion z-test, and a binomial sign test for rates against a null.
- Seeded percentile bootstrap for medians.

Verdict labels top out at "strong evidence" and read "insufficient data" below n=20
(`MIN_SAMPLE_FOR_CLAIM`). **No multiple-comparison correction is applied**, and the
analysis runs dozens of tests — treat p-values as a ranking signal.

---

## Output column dictionary

### `l5_all_trades.csv` — one row per deduplicated fill

| column | meaning |
| --- | --- |
| `timestamp`, `timestamp_iso` | fill time, unix seconds and UTC ISO-8601 |
| `asset` | `BTC` or `ETH` (the underlying — **not** the API's `asset` field) |
| `window_minutes` | `5` or `15`, validated against market timestamps |
| `condition_id` | canonical market/window ID |
| `market_id`, `market_slug`, `question` | gamma market identity |
| `event_slug`, `event_title` | parent event identity |
| `outcome` | `UP` or `DOWN` |
| `outcome_index` | raw index as served |
| `side` | `BUY` or `SELL` |
| `price` | executed price, 0–1 |
| `shares` | contracts |
| `dollars` | `usdcSize` when served, else `price × shares` |
| `dollars_derived` | `true` when `dollars` was computed rather than served |
| `token_id` | ERC-1155 token id (the API's own `asset` field) |
| `transaction_hash` | on-chain transaction |
| `window_start_ts`, `window_end_ts` | derived window bounds |
| `seconds_from_window_start` | `timestamp − window_start_ts` |
| `seconds_to_expiry` | `window_end_ts − timestamp` |
| `window_source` | `metadata` or `text_fallback` |
| `window_conflict` | timestamps and title disagreed on the window length |
| `bounds_reconstructed` | start was inferred as `end − length` |
| `source` | `activity` or `trades` — which endpoint the surviving row came from |

### `l5_window_sequences.csv` — one row per fill, with cumulative state *after* it

Carries the trade identity columns (`condition_id`, `asset`, `window_minutes`,
`market_slug`, `event_slug`, `trade_seq`, `timestamp`, `timestamp_iso`,
`seconds_from_window_start`, `seconds_to_expiry`, `outcome`, `side`, `price`, `shares`,
`dollars`, `transaction_hash`) plus:

| column | meaning |
| --- | --- |
| `up_qty`, `down_qty` | net shares held per side |
| `up_spend`, `down_spend` | cost basis of the shares still held |
| `up_avg_price`, `down_avg_price` | weighted average cost of shares held (blank when flat) |
| `up_buy_qty`, `up_buy_spend`, `down_buy_qty`, `down_buy_spend` | gross buying, ignoring sells |
| `up_sell_qty`, `down_sell_qty` | gross selling |
| `matched_qty` | `min(up_qty, down_qty)` |
| `matched_pair_cost_per_share` | `up_avg_price + down_avg_price` |
| `matched_pair_cost_total` | `matched_qty × pair cost per share` |
| `excess_qty`, `excess_abs`, `excess_side` | residual directional inventory, signed and absolute |
| `dominant_side` | current dominant side (same as `excess_side`) |
| `cum_trades` | fills so far in this window |
| `cum_dollars_bought`, `cum_dollars_sold`, `cum_net_cash_out` | running cash |
| `oversold` | a sell has exceeded visible inventory in this window |

### `l5_windows_summary.csv` — one row per market/window

Identity and geometry (`condition_id`, `market_id`, `asset`, `window_minutes`,
`market_slug`, `event_slug`, `event_title`, `question`, `window_start_ts/iso`,
`window_end_ts/iso`) plus:

| column | meaning |
| --- | --- |
| `first_side`, `first_price`, `first_action`, `first_trade_ts` | the opening fill |
| `first_seconds_from_window_start` | how far into the window he opened |
| `first_price_below_half` | opened below the 50c midpoint |
| `total_trades`, `total_buys`, `total_sells` | fill counts |
| `total_dollars_bought`, `total_dollars_sold`, `total_dollars_net` | cash |
| `total_shares_bought` | gross contracts bought |
| `max_price_bought`, `min_price_bought` | price extremes among buys |
| `bought_at_70c_plus`, `bought_at_80c_plus`, `bought_at_90c_plus` | escalation flags |
| `qty_lt70`, `qty_70to80`, `qty_80to90`, `qty_gte90` | shares bought per price band |
| `dollars_lt70`, `dollars_70to80`, `dollars_80to90`, `dollars_gte90` | dollars per band |
| `final_up_qty`, `final_down_qty` | closing position per side |
| `final_up_avg_price`, `final_down_avg_price` | closing cost basis per side |
| `final_up_gross_avg_buy_price`, `final_down_gross_avg_buy_price` | average of all buys, sells ignored |
| `final_up_spend`, `final_down_spend` | closing cost basis in dollars |
| `matched_qty`, `matched_pair_cost_per_share`, `matched_pair_cost_total` | the hedged book |
| `residual_qty`, `residual_side`, `residual_frac_of_larger_leg` | leftover directional exposure |
| `finished_balanced` | residual within tolerance |
| `traded_both_sides` | both Up and Down were traded |
| `seconds_to_opposite_side`, `trades_before_opposite_side` | how fast he crossed |
| `realized_pnl`, `pnl_source`, `pnl_rows` | Polymarket position accounting, or `unavailable` |
| `window_source`, `window_conflict`, `bounds_reconstructed`, `oversold` | data-quality flags |

### `l5_non_trade_activity.csv` — context, not fills

`SPLIT` / `MERGE` / `REDEEM` / `REWARD` / `CONVERSION` rows on in-scope markets. A MERGE is
how matched Up+Down shares get turned back into USDC, so an inventory reading of the fills
alone would miss it. These rows are **not** included in the three tables above.

### `l5_analysis.json`

Machine-readable output of every test in `src/l5/analysis.py`, including sample sizes and
verdict labels. `L5_STRATEGY_FINDINGS.md` is rendered from it.

---

## Tests

```bash
python -m pytest tests/test_l5_classify.py tests/test_l5_normalize.py \
                 tests/test_l5_inventory.py tests/test_l5_windows.py \
                 tests/test_l5_stats.py tests/test_l5_pipeline.py -q
```

164 tests covering:

- **parsing** — timestamp shapes, asset detection, outcome resolution, malformed rows
- **window classification** — duration bands, metadata-beats-title, text fallback, out-of-scope rejection
- **window grouping** — condition-ID keying, chronological ordering
- **cumulative inventory math** — every figure hand-computed in the test
- **deduplication** — within-source, cross-source, and genuine repeats surviving
- **statistics** — against known closed forms
- **end to end** — synthetic cache to CSVs to findings, offline

Note: `tests/test_spread_scanner.py` and `tests/test_orderbook.py` have 14 pre-existing
failures unrelated to this work.

### Reproducing the samples

```python
from tests.test_l5_pipeline import _build_cache
from src.l5 import pipeline
_build_cache("/tmp/l5cache")
pipeline.analyze("/tmp/l5cache", "/tmp/l5out", "/tmp/FINDINGS.md")
```

---

## Limitations — please read before drawing conclusions

1. **Only executed fills are public.** Resting orders, cancellations and modifications are
   not retrievable. A window showing one side traded may be a directional bet *or* a
   two-sided quote whose other leg never filled. Nothing here assumes them apart.
2. **No historical order book.** Polymarket does not serve historical book state and this
   code does not reconstruct or infer any. "He bought the cheaper side" is therefore
   measured two ways — against the 50c midpoint (which assumes binary complementarity),
   and against his *own* opposite-side fill within 30 seconds (which assumes nothing).
   Both are reported.
3. **Rebates and incentives are not in the P&L.** For a two-sided quoting strategy they
   may be most of the economics. A negative realized trading P&L is not evidence the
   strategy loses money.
4. **History depth is whatever the endpoints still serve.** Any truncation is recorded in
   `fetch_manifest.json` and surfaced in the findings document.
5. **These are exploratory comparisons on one trader.** Dozens of hypotheses, no
   multiple-comparison correction, no out-of-sample validation.
