# ORDERFILLED_ARCHIVE_COVERAGE_V1 — why is L5 absent? Bounded, and closed.

`wzsg/polymarket-orderfilled-v2` does not contain L5. This is the bounded check
of whether his activity uses a different chain, contract, event or collateral
from the population the archive holds.

## Measured comparison

| axis | L5's transactions (our own decode) | the archive |
|---|---|---|
| chain | Polygon (`eth_getTransactionReceipt` on Polygon RPCs) | Polygon, chain 137 |
| exchange contract | `0xe111180000d2663c0091e4f400237545b87b996b` | control row's `taker` = **`0xE111180000d2663C0091e4f400237545B87B996B`** — the same address |
| contract label | — | `CTF_EXCHANGE_V2` |
| event | 4 topics (sig, orderHash, maker, taker) + 7 data words | `order_hash, maker, taker, side, token_id, maker_amount_filled, taker_amount_filled, fee` — the same fields |
| collateral | `makerAssetId == 0` on a buy | same convention (sample row `maker_amount_filled` 8,000,000 vs `taker_amount_filled` 10,000,000) |
| block range | L5 txs 2026-04-29 → 07-27 tested | archive covers 2026-04-28 → 08-03 |

**No measurable difference was found on any axis.** Same chain, same exchange
address, same event shape, same collateral convention, overlapping blocks.

## What was tested, and what could not be

* **6 L5 transaction hashes**, spread across the coverage window: **0 rows
  each**, against a control (`transaction_hash` and `maker` taken from the
  archive's own first rows) returning **3** and **823**. The index reports
  `partial: false` over all 575,630,861 rows.
* **Token-id route: not available.** L5's cached activity rows carry
  `conditionId` but **not** the CLOB `asset` / token id — 194,600 TRADE rows in
  the window, and the field is simply absent from that source. So "is the
  market in the archive at all?" could not be asked this way.
* **`wzsg/polymarket-orderfilled-v1`: untestable.** Its index returned
  *"loading"* or *"corrupted and being rebuilt"* on all 12 attempts.

## Verdict

**ARCHIVE OMISSION: UNEXPLAINED.** Every axis we can measure is identical, so
there is no supported hypothesis about *why* L5 is missing — and per the owner's
instruction, **P0 time on this stops here.**

One observation is recorded as an observation, not a theory: L5 settles in
**pUSD** (`0xC011a7E1…`, `name()` = "Polymarket USD", confirmed by `eth_call`
and seen in his receipts). Whether a pUSD-settled deployment writes to a
different contract set than the archive collects is **untested**, and it is in
tension with the exchange address matching exactly. It is not offered as the
explanation.

## Consequence for the program

The RPC receipt-pruning limit on the **frozen historical tape** stands and has
no public workaround in hand. Recent activity is unaffected — receipt
availability there is **1.0000** across 1,600 transactions — which is why the
parent-order results are built on recent activity and are labelled a different
population throughout.
