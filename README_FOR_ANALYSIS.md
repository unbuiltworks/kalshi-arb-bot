# README_FOR_ANALYSIS — Polymarket L5 window-trader study

Read-only reconstruction of the trading behaviour of Polymarket user
`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68` (profile `l5zn1bwom8etsk`) in
**BTC and ETH 5-minute and 15-minute Up/Down markets**.

Everything here is an exporter and an analyzer. It authenticates with nothing,
signs nothing, places nothing, and cancels nothing. It is independent of the
Kalshi arbitrage bot and of the SOL executor; it shares no configuration or
credentials with them.

---

## Current status: NO DATA RETRIEVED

The environment this study was built in blocks every Polymarket host at the
network-egress layer. All of `data-api.polymarket.com`, `gamma-api.polymarket.com`,
`clob.polymarket.com`, `polymarket.com` and `docs.polymarket.com` answer
`403 Forbidden` to the proxy `CONNECT`, so **no trader data could be fetched**.

The pipeline was still run end to end against the live endpoints. The result is
recorded, not hidden:

* `data/l5/raw/*.jsonl` — one line per attempt, each carrying the exact URL,
  parameters and the `403` transport error.
* `analysis/l5/l5_manifest.json` — `"fetch_status": "failed"` with the reason.
* `analysis/l5/l5_all_trades.csv`, `l5_window_sequences.csv`,
  `l5_windows_summary.csv` — **header rows only, zero data rows**.
* `L5_STRATEGY_FINDINGS.md` — generated with a `STATUS: NO DATA` banner and
  no conclusions.

To produce the real study, run the pipeline from a host that can reach
Polymarket's public APIs:

```bash
python -m src.polymarket_l5.cli all
```

Every artifact above regenerates in place, with real numbers and without any
code change. To see the shape of the outputs right now, without network access,
look in `analysis/l5/sample_synthetic/` — those files are generated from
invented test fixtures and are labelled as such.

---

## Quick start

```bash
python -m src.polymarket_l5.cli fetch     # network: harvest public APIs -> data/l5/raw/*.jsonl
python -m src.polymarket_l5.cli build     # offline: raw cache -> the three CSVs
python -m src.polymarket_l5.cli analyze   # offline: CSVs -> analysis/l5/l5_analysis_stats.json
python -m src.polymarket_l5.cli report    # offline: stats -> L5_STRATEGY_FINDINGS.md
python -m src.polymarket_l5.cli all       # all four, in order

python -m pytest tests/test_l5_*.py -q                 # 42 tests, no network
python scripts/l5_generate_synthetic_sample.py         # refresh the synthetic sample
```

Useful flags: `--address`, `--raw-dir`, `--output-dir`, `--findings-path`,
`--append` (keep the existing raw cache instead of starting a fresh harvest).

Python 3.11+, standard library only. No third-party dependency is used by this
package — `urllib` honours `HTTPS_PROXY` and `SSL_CERT_FILE` from the
environment.

---

## Data sources (public, GET only)

| Endpoint | Used for | Cache tag |
|---|---|---|
| `GET data-api.polymarket.com/activity?user=…` | executed fills; also REDEEM/SPLIT/MERGE rows | `activity` |
| `GET data-api.polymarket.com/trades?user=…` | second fill source, used only to find gaps in `/activity` | `trades` |
| `GET data-api.polymarket.com/positions?user=…` | position rows incl. Polymarket's own realized P&L | `positions_all`, `positions_redeemable`, `positions_closed` |
| `GET gamma-api.polymarket.com/markets?condition_ids=…` | market start/end, outcomes, resolution | `markets` |
| `GET gamma-api.polymarket.com/events?slug=…` | event grouping and titles | `events` |
| `GET clob.polymarket.com/markets/{condition_id}` | fallback market metadata | `markets_clob` |

`src/polymarket_l5/http_client.py` enforces the envelope in code: HTTPS only,
hostname on an allowlist, GET only, and no authorization header exists anywhere
in the package. A call to any other host raises `DisallowedRequest`.

Endpoint shapes drift. Every call degrades gracefully: a `4xx` is recorded in
the raw cache and in the manifest, and the affected analysis reports
`insufficient-data` rather than substituting a guess.

### Pagination and coverage limits

Offset pagination runs until a short page, an empty page, an HTTP error, or a
repeated first row (which is how an endpoint that silently ignores `offset`
announces itself). `MAX_PAGES` caps a runaway loop. If the API caps deep offsets,
the harvest is truncated rather than wrong: `l5_manifest.json` records
`pages`, `items` and `stopped_because` so the coverage limit is visible.

---

## Reproducibility

`fetch` is the only stage that touches the network. It writes one JSONL line per
HTTP response to `data/l5/raw/<tag>.jsonl`:

```json
{"fetched_at":"…","tag":"activity","url":"…","params":{…},"status":200,"error":"","payload":[…]}
```

`build`, `analyze` and `report` are pure functions of that cache. Re-running them
reproduces every CSV and every number in the findings byte-for-byte (modulo the
generation timestamp). Normalized intermediates land in
`data/l5/normalized/*.jsonl` beside the raw cache.

`analysis/l5/l5_manifest.json` ties a run together: address, fetch report per
endpoint, dedup counts, row counts, coverage, and the git SHA of the code that
produced it.

---

## Method

### 1. Deduplication

Overlapping offset pages return the same fill twice, but a single transaction can
also legitimately contain two identical fills (same outcome, side, price, size,
second). Collapsing on a naive set would delete the second kind.

So: fingerprint each fill on `(tx_hash, timestamp, condition_id, token_id, side,
price, shares)`, count occurrences **within each response batch**, and keep the
maximum count seen across batches. A repeated page contributes nothing; a genuine
duplicate inside one response survives. `/activity` is authoritative; fills that
appear only in `/trades` are added and counted separately in the manifest
(`supplemented_from_trades`).

### 2. Window classification (validated, not read off the title)

Per the brief, titles are not trusted on their own:

1. **Primary — duration.** `endDate - startDate` from market metadata, snapped
   to 5 or 15 minutes only if it lands within `WINDOW_TOLERANCE_S` (45s) of the
   bucket. An hourly market measures 3600s and is rejected outright.
2. **Secondary — text.** Slug/title/event regex. Used only to corroborate.
3. **Disagreement** → duration wins, and the market is flagged
   `window_text_conflict:text=N`.
4. **Duration metadata missing** → classification is `text_only`, the market is
   flagged and **excluded from the primary analysis**.
5. **No market metadata at all** → excluded; never guessed into scope.

Asset classification uses word-boundary regexes over slug/title/event
(`btc|xbt|bitcoin`, `eth|ether|ethereum`) so `"method"` does not read as ETH. A
text that matches both is flagged `asset_ambiguous` and excluded.

Every excluded fill is written to `analysis/l5/l5_excluded_markets.csv` with its
reason. Nothing disappears silently; `l5_windows_summary.csv` also carries the
`window_source` column so the provenance of each classification travels with the
row.

### 3. Inventory accounting

* **Average cost basis.** A BUY adds `price × shares` to that side's basis. A
  SELL removes `wavg_price × shares`, and `proceeds − removed_basis` is booked as
  realized trading P&L. A sell larger than the held quantity is clamped and
  flagged `sell_exceeds_held_qty`.
* **`matched_qty = min(up_qty, down_qty)`** — the number of Up/Down pairs held.
* **`matched_pair_unit_cost = wavg_up + wavg_down`** — the cost of acquiring one
  pair. A pair pays exactly $1.00 at resolution whichever way the market
  settles, so this number *is* the trade: below $1.00 is locked profit before
  fees and incentives, above $1.00 is a locked loss.
* **`excess_qty = up_qty − down_qty`** — signed directional inventory. Positive
  is long Up. `dominant_side` is `Up`, `Down` or `BALANCED`.

### 4. P&L

Two independent sources, never blended silently — `pnl_source` says which was
used:

* `polymarket_positions` — `realizedPnl` as Polymarket reports it, aggregated by
  condition id. Preferred when present.
* `derived_resolution` — computed from our own fills and the market's resolved
  outcome:
  `realized_trading_pnl + (winning_side_qty × $1.00) − remaining_cost_basis`.
* `unavailable` — the market is unresolved and positions data is absent. Those
  windows are excluded from Q8, not defaulted to zero.

**Both are trading P&L only.** Gas, maker rebates and liquidity-incentive
payouts do not appear in fill or position data and are **not** assumed to be
included. A window that looks flat here may be profitable after incentives, and
a marginally negative pair cost may be an entirely rational incentive-farming
trade. No claim in this study depends on knowing which.

---

## Output files

All under `analysis/l5/`.

### `l5_all_trades.csv` — one row per deduplicated in-scope fill

`timestamp`, `datetime_utc`, `asset`, `window_minutes`, `window_id`, `market_id`,
`market_slug`, `event_slug`, `event_title`, `title`, `outcome` (Up/Down), `side`
(BUY/SELL), `price`, `shares`, `dollars`, `condition_id`, `transaction_hash`,
`seconds_from_window_start`, `seconds_to_expiry`, `window_start_utc`,
`window_end_utc`, `window_source`, `token_id`, `outcome_raw`, `outcome_mapping`,
`source`, `market_flags`.

`window_id` is the market's condition id — one window is one market.

### `l5_window_sequences.csv` — one row per fill, cumulative state after it

Identity (`window_id`, `asset`, `window_minutes`, `trade_seq`), the fill itself,
timing (`seconds_from_window_start`, `seconds_to_expiry`,
`seconds_since_prev_trade`), and the running state required by the brief:

`cum_up_qty`, `cum_up_spend`, `cum_down_qty`, `cum_down_spend`,
`wavg_up_price`, `wavg_down_price`, `matched_qty`, `matched_pair_unit_cost`,
`matched_pair_cost`, `excess_qty`, `excess_abs_qty`, `excess_cost`,
`dominant_side`, `cum_gross_dollars_bought`, `cum_realized_pnl_trading`,
`cum_trade_count`, `anomalies`.

### `l5_windows_summary.csv` — one row per window

Identity and timing; `first_side`, `first_price`, `first_action`,
`first_trade_seconds_from_start`; `total_trades`, `total_buy_trades`,
`total_sell_trades`, `total_dollars_bought`, `total_dollars_sold`;
`max_price_bought` and `max_price_bought_side`; `any_buy_ge_70c`,
`any_buy_ge_80c`, `any_buy_ge_90c`; `qty_band_*` and `dollars_band_*` for the
bands `lt50 / 50_59 / 60_69 / 70_79 / 80_89 / 90_plus`; `final_up_qty`,
`final_down_qty`, `final_up_avg_price`, `final_down_avg_price` and the
buy-only equivalents; `matched_qty`, `matched_pair_unit_cost`,
`matched_pair_cost`; `residual_qty_signed`, `residual_qty_abs`, `residual_side`,
`residual_cost`, `balanced_at_end`; `seconds_first_to_opposite_side`,
`opposite_side_ever_bought`; `cheaper_side_first`, `first_side_price_vs_other`;
`winning_outcome`, `resolution_source`, `pnl_derived_resolution`,
`pnl_polymarket_positions`, `pnl_source`,
`realized_trading_pnl_within_window`, `anomalies`.

### Also written

* `l5_excluded_markets.csv` — every out-of-scope fill with its exclusion reason.
* `l5_analysis_stats.json` — full machine-readable statistics.
* `l5_manifest.json` — provenance for the run.
* `sample_synthetic/` — SYNTHETIC demonstration outputs; not real data.

---

## Pre-registered analysis

These eight questions and their tests are fixed in `src/polymarket_l5/analyze.py`
before any data was seen. Each result carries its own `n` and a conservative
confidence label (`insufficient-data` below n=8; `suggestive-small-n` below
n=20; `weak`/`moderate`/`strong` above, gated on p).

| # | Question | Test |
|---|---|---|
| Q1 | Does size increase at 70c / 80c / 90c? | Spearman(price, shares) and (price, dollars) over buy fills; Mann-Whitney of shares at/above each threshold vs below; per-band medians |
| Q2 | 5m vs 15m | Mann-Whitney on nine per-window metrics; two-proportion tests on five rates |
| Q3 | BTC vs ETH | same battery |
| Q4 | Does he buy the cheaper side? | one-proportion test vs 0.5 on the first buy landing below $0.50, and on all buy fills |
| Q5 | Latency to the opposite side | distribution of `seconds_first_to_opposite_side`, split by length and asset, plus latency as a fraction of the window; bootstrap CI on the median |
| Q6 | A combined pair-cost target? | distribution of the final `matched_pair_unit_cost`: median, IQR width, share within $0.01/$0.02 of the median, share below $1.00, cumulative share by threshold |
| Q7 | Balanced vs directional finishes | share balanced (residual ≤ 1 share or ≤ 1% of the larger leg), one-proportion test, split by asset and length |
| Q8 | Profitable vs losing windows | split by `pnl_source`-preferred P&L, then the Q2/Q3 comparison battery |

Statistics are implemented in `src/polymarket_l5/stats.py` (stdlib only):
tie-corrected Mann-Whitney with a normal approximation, two- and one-proportion
z-tests with Wilson intervals, Spearman rank correlation, and a seeded bootstrap.

---

## Constraints honoured

* **Fills only.** Public activity shows executed trades, not resting or
  cancelled orders. A maker strategy appears as a sequence of fills with no
  visible intent. Nothing infers order placement.
* **No order-book reconstruction.** Historical book state at fill time is not
  available and is not synthesised. "Cheaper side" is derived from the fill price
  against the $1.00 binary complement, not from a book snapshot — the CSV states
  this in `first_side_price_vs_other`.
* **No rebate assumptions.** See the P&L section.
* **Dedup is conservative in both directions** — see above.
* **Window classification is validated against market duration**, and every
  exclusion is written down.
* **Read-only.** No credentials are read, no keys are loaded, no mutating
  endpoint exists in the code.
* **Independence.** This package imports nothing from the Kalshi bot and touches
  no other service.

## Statistical caveats

Consecutive windows in one session share market regime, so treating windows as
independent samples makes p-values optimistic. With a few dozen windows,
effect sizes and counterexample counts are more informative than significance,
which is why `L5_STRATEGY_FINDINGS.md` prints support rates, Wilson intervals and
explicit counterexamples for every candidate rule.

## Layout

```
src/polymarket_l5/
  config.py       constants: hosts, address, buckets, price bands, paths
  http_client.py  GET-only, allowlisted, no-auth HTTP with retries
  cache.py        raw + normalized JSONL caches
  fetch.py        harvest orchestration and pagination
  normalize.py    raw rows -> fills, outcome mapping, deduplication
  windows.py      market index, asset + window classification and validation
  inventory.py    cumulative Up/Down inventory, matching, pair cost, residual
  pnl.py          reported vs derived window P&L
  export.py       the three CSVs + exclusion audit
  stats.py        stdlib statistics kit
  analyze.py      the eight pre-registered questions
  findings.py     renders L5_STRATEGY_FINDINGS.md from the statistics
  cli.py          fetch / build / analyze / report / all
tests/
  test_l5_normalize.py   parsing, outcome mapping, dedup
  test_l5_windows.py     classification, validation, scope filtering
  test_l5_inventory.py   cumulative inventory math
  test_l5_pipeline.py    end-to-end build -> analyze -> report, HTTP guards
  fixtures/l5_synthetic.py
scripts/l5_generate_synthetic_sample.py
```
