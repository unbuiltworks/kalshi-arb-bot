# Maker fees: RESOLVED by the venue's own ledger, per row

**Date:** 2026-09-22 · Evidence: `analysis/gemini/maker_fee_cash_*.json`,
produced by the read-only gate from `/v1/mytrades`, `/v1/transfers` and settled
positions.

## The established fact, which nothing below may overwrite

**The eighteen EP016 fills carry $0.00 in fees, in both independent sources.**

* `/v1/mytrades` — the account's own execution ledger — returns **18 of 18**
  rows for the four EP016 contracts, matched by symbol, and every one has
  `fee_amount: 0`. Total **$0.00** against the published schedule's implied
  **$0.18**.
* the WebSocket fill events carried `n` explicitly as `0.0000` on all eighteen,
  with `fees_missing: 0` — a stated zero, not an omitted field.

Two independent venue records agree, per row. This is not an inference from a
formula and it is not an aggregate.

## What the other 199 rows say, and why it does not touch the above

Across the account's whole returned trade history:

| label | rows | meaning |
|---|---|---|
| `ZERO` | **97** | no fee charged |
| `MAKER_FORMULA` | **60** | fee equals `0.0175 × C × P × (1−P)` rounded up |
| `TAKER_FORMULA` | 13 | fee equals `0.07 × C × P × (1−P)` rounded up |
| `EITHER` | 23 | both formulas give the same cent |
| `NEITHER` | 6 | ten-lot rows at $0.03 where maker implies $0.04–0.05 |

So **maker fees are real on this account and have been charged** — 60 rows match
the maker formula exactly. Every maker-formula row surfaced is dated
**2026-09-01 or 2026-09-02**; the EP016 fills are 2026-09-22 and are zero.

**That is a change over time, not a contradiction.** The published rate exists,
has been applied to this account, and was not applied to our most recent maker
fills. Whether that is a waiver, a product change or a promotion is not
established, and is not being guessed at.

## What this means for a restart decision

* **The fee to assume for a NEW maker fill is $0.00**, on the most recent and
  most directly comparable evidence — eighteen fills, both sources, per row.
* **A maker fee of 0.0175 × C × P × (1−P), rounded UP, may resume without
  notice.** It has been charged on this account within the last month. Any
  bounded probe must survive it: at C=1 and P≈0.45 that is **$0.01 per fill**,
  which on eighteen fills is $0.18 against $0.05 of rebate.
* `/v1/transfers` showed **10 rows, zero debit-like, zero fee-named** — fees are
  netted at execution, not swept, so the ledger is the correct source and
  transfers were never going to show them.

## Status

**RESOLVED for the 18 fills: $0.00 charged.** The fee line for those fills is
confirmed, not assumed. The forward-looking rate is **UNRESOLVED** and must be
treated as a risk that can return to $0.01 per fill.
