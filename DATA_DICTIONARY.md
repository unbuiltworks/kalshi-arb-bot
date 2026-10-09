# Data dictionary — L5 public-fill forensic package

Every field in every shipped file, what produced it, and where it can
mislead you. Fields are grouped as **observed** (came out of the venue's
public API verbatim), **derived-mechanical** (arithmetic on observed
fields, no modelling choice), or **derived-with-a-choice** (a convention
was picked; the convention is stated).

Subject: a single Polymarket trading account, public profile slug
`l5zn1bwom8etsk`, on-chain address `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`.
It is **not** our account. Everything here is that account's public
activity, retrieved from Polymarket's public data API.

---

## 1. Corpus scale and provenance

| quantity | value |
|---|---|
| source envelopes | 1,570 (157 + 1,413) |
| raw records in envelopes | 784,500 (78,000 + 706,500) |
| admitted after the identity rule | 784,500 |
| unique after dedupe | 776,749 |
| exact duplicates dropped | 7,751 |
| **TRADE fills (the dataset)** | **760,817** |
| REDEEM | 15,729 |
| MAKER_REBATE / TAKER_REBATE / REWARD | 110 / 73 / 20 |
| distinct windows (markets) with fills | 17,046 |
| first fill | 2026-03-19T10:09:37Z |
| last fill | 2026-08-31T23:17:16Z |
| total spend across all fills | 7,551,796.78 USDC |
| matched pairs formed across all windows | 6,815,895.88 |

**Identity rule.** A record is admitted iff the API envelope that returned
it was requested with `params.user` = L5's address, **and** the record
carries no *contradicting* `name`. The `name` field is never used as a
positive filter: 706,500 of the 784,500 records have no `name` block at
all, so filtering on it would silently discard 90% of the corpus.
Contradicting names found: **0**. Provenance mismatches: **0**.

**Dedupe key.** `(transactionHash, conditionId, outcomeIndex, timestamp,
price, size, type)`. Shorter keys that look natural here — notably
`(transactionHash, asset, timestamp)` — collapse ~33,000 records whose
price/size/side actually differ, which understates redemption cashflow in
particular. This key has zero such collisions.

**Corroboration, and its limit.** The 78,000 named records overlap the
706,500 nameless ones in time, so the two can be intersected on
`transactionHash` to check identity against an independent signal. They
share **3,175 transactions** — about **0.5%** of the nameless set. On that
0.5% identity is independently confirmed. For the other 99.5%,
**provenance is the only evidence.** This is a real limitation, not a
formality.

---

## 2. `dataset/fills/l5_fills_enriched_partNN_*.csv.gz` — one row per fill

14 parts, chronological, ≤ 55,000 rows each. **A window is never split
across parts**, so each part is self-contained for sequential analysis.

### Identity and provenance

| column | kind | meaning |
|---|---|---|
| `event_slug` | observed | the window, e.g. `btc-updown-15m-1787903100`. The trailing integer is the window's start time as a unix epoch. |
| `asset` | derived-mechanical | `BTC` / `ETH`, parsed from the slug prefix. |
| `duration_minutes` | derived-mechanical | `5` or `15`, parsed from the slug. |
| `trade_seq` | derived-mechanical | 1-based index of this fill **within its window**, ordered by `(timestamp, tx_hash)`. |
| `timestamp` | observed | unix seconds. **Whole seconds only** — the API exposes no sub-second precision. |
| `datetime_utc` | derived-mechanical | `timestamp` rendered ISO-8601 UTC. |
| `condition_id` | observed | on-chain condition id of the market. |
| `tx_hash` | observed | settlement transaction hash. Many fills share one hash. |
| `source_file` | derived-mechanical | which raw file the record came from; lets you separate the named 78,000 from the nameless 706,500. |

### Window clock

| column | kind | meaning |
|---|---|---|
| `window_start_epoch` | derived-mechanical | from the slug. |
| `window_end_epoch` | derived-mechanical | `window_start_epoch + duration_minutes × 60`. |
| `seconds_from_window_start` | derived-mechanical | can exceed the window length: fills land after nominal expiry. |
| `seconds_to_settlement` | derived-mechanical | `window_end_epoch − timestamp`. **Negative means the fill is after nominal expiry.** |

### The fill

| column | kind | meaning |
|---|---|---|
| `leg` | derived-with-a-choice | `UP` or `DOWN`, taken from the outcome **label**. |
| `outcome_raw` | observed | the venue's outcome string. |
| `outcome_index` | observed | the venue's index. **Do not key on this.** 3,196 fills carry a polarity reversed relative to the majority (1,600 rows have index 1 = "Up", 1,596 have index 0 = "Down"), because the market's `outcomes` array is not ordered consistently. Keying on the index silently swaps legs in those windows. |
| `side` | observed | `BUY` on 760,815 fills; `SELL` on **2**. |
| `price` | observed | probability price in dollars, 0–1. 0 out of range. |
| `size` | observed | contracts. 0 non-positive. |
| `usdc_size` | observed | the venue's own notional. Not always exactly `price × size`; it is the venue's figure, carried unaltered. |

### Reconstructed inventory **after** this fill

All of these are state *after* applying the fill, so a row answers "what
did L5 hold the instant this fill landed".

The two outcomes of a window are complements: one contract of each is a
**matched pair** that pays exactly $1.00 whichever way the window
resolves. That is the whole basis of the reconstruction.

| column | kind | meaning |
|---|---|---|
| `cum_up_qty`, `cum_up_spend`, `up_vwap` | derived-mechanical | cumulative contracts bought on `UP`, USDC spent, and spend/qty. |
| `cum_down_qty`, `cum_down_spend`, `down_vwap` | derived-mechanical | the same for `DOWN`. |
| `matched_qty` | derived-mechanical | matched pairs held = running `min(up, down)`. |
| `matched_spend_fifo` | derived-with-a-choice | USDC that bought those pairs, costed **FIFO against the actual lots that paired** — not against a blended average. A blended average is wrong whenever the two legs were bought at different times at different prices, which is the normal case here. |
| `effective_pair_cost` | derived-mechanical | `matched_spend_fifo / matched_qty`. Below 1.0 the matched book is locked profitable; above 1.0 it is locked loss-making, regardless of how the window resolves. Empty until the first pair exists. |
| `locked_pair_profit` | derived-mechanical | `matched_qty − matched_spend_fifo`, i.e. settlement-independent P&L already banked on the matched book. |
| `residual_side` | derived-mechanical | which leg is held unpaired: `Up`, `Down`, or empty when flat. |
| `residual_qty`, `residual_spend`, `residual_vwap` | derived-mechanical | the unpaired holding, FIFO-costed. The unpaired leg is the only part of the window's position whose value depends on the outcome. |
| `total_spend` | derived-mechanical | cumulative USDC spent in this window. |
| `open_directional_usd` | derived-mechanical | = `residual_spend`; USDC at risk on the outcome. |

### Sequence context

| column | kind | meaning |
|---|---|---|
| `fills_so_far` | derived-mechanical | `trade_seq − 1`. |
| `seconds_since_prev_fill` | derived-mechanical | gap to the previous fill **in the same window**. Empty on the first. Whole seconds; a 0 means "same second", not "simultaneous". |
| `leg_switched_from_prev` | derived-mechanical | 1 if this fill is on the other leg from the previous one. |

### Settlement

| column | kind | meaning |
|---|---|---|
| `settled_outcome` | derived-with-a-choice | the outcome that paid. See §4. |
| `settlement_evidence` | derived-mechanical | how it was established: `redeem_only`, `gamma_only`, `redeem+gamma_agree`, or `none`. |

---

## 3. `dataset/l5_window_summary.csv.gz` — one row per window

17,046 rows. Final state plus window economics. Columns largely mirror
§2 with a `final_` prefix, plus:

| column | kind | meaning |
|---|---|---|
| `n_fills`, `n_up_fills`, `n_down_fills`, `n_leg_switches` | derived-mechanical | counts over the window. |
| `first_fill_sec_from_start`, `last_fill_sec_from_start` | derived-mechanical | when L5 entered and stopped, relative to window open. |
| `min_price`, `max_price`, `mean_price` | derived-mechanical | over L5's own fills only — **not** the market's price range. |
| `settlement_proceeds` | derived-mechanical | `final_matched_qty` + `final_residual_qty` if the residual side won, else + 0. |
| `window_pnl` | derived-mechanical | `settlement_proceeds − total_spend`. **Excludes** rebates and rewards, which are unattributable (§5), and excludes gas. |
| `window_pnl_per_matched_pair` | derived-mechanical | `window_pnl / final_matched_qty`. |
| `redeem_usdc`, `n_redeems` | observed | REDEEM records carrying this slug. |

---

## 4. `dataset/settlement.csv` — how the outcome was established

Two independent sources, and they never disagree:

* **REDEEM records (15,519 windows).** On this venue only the *winning*
  token redeems; the loser is worthless. So a REDEEM record's `outcome`
  **is** the settled outcome. This recovers settlement from public
  activity alone, with no price feed and no oracle call.
* **`markets.jsonl` resolved `outcomePrices` (3,525 windows).** Available
  only for the 3,528 markets covered by an earlier, narrower harvest.

Where both exist (**2,002 windows**) they agree on **2,002** and disagree
on **0**. Windows with conflicting REDEEM outcomes: **0**. Windows with
fills but no settlement label: **4**.

**The caveat that matters:** REDEEM evidence exists only if L5 *held a
winning position and redeemed it*. A window where L5 ended with nothing
in the money leaves no REDEEM, so its settlement is unobserved here. This
makes settlement coverage **non-random with respect to L5's outcome** —
do not treat the labelled subset as a fair sample of all windows when the
quantity you are estimating is itself about winning.

---

## 5. `cashflow.csv.gz` — non-fill money, and why most of it cannot be used

| type | records | USDC total | attributable to a window? |
|---|---|---|---|
| `REDEEM` | 15,729 | see file | **yes** — carries a slug |
| `MAKER_REBATE` | 110 | see file | **no** — slug and conditionId are empty |
| `TAKER_REBATE` | 73 | see file | **no** — same |
| `REWARD` | 20 | see file | **no** — same |

The 203 rebate/reward records are **account-level**: the venue returns
them with no market attached. They therefore cannot be allocated to any
window, and `window_pnl` excludes them. Two consequences:

1. Any per-window or per-contract incentive conclusion drawn from 203
   records is **thin evidence** and must say so.
2. `window_pnl` summed over all windows is **not** L5's realized P&L. It
   omits rebates, rewards, gas, and any transfer in or out.

---

## 6. `CORPUS_PROFILE.json`

Distributions and cross-tabs only — price, size, inter-fill gap, pair
cost, position-in-window, per-month, per-hour, window P&L histogram. It
is deliberately descriptive: no hypothesis, no causal claim, no
conclusion of this package's authors appears in it.

---

## 7. `case_studies/`

29 complete windows, each rendered with **every** fill in order and the
reconstructed inventory after each. Selected by explicit strata (regime,
profitability, one-sided accumulation, pair-cost compression,
side-switching, cadence, asset, residual outcome) documented in
`CASE_STUDY_INDEX.md`. One requested stratum — an early-regime ETH window
— has **no** instance: ETH fills begin in August. That is recorded rather
than substituted.

These files state what happened. They contain no interpretation.
