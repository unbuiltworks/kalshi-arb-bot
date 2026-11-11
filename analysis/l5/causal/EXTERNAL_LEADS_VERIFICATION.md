# External data leads — verified, not taken on trust

OWNER 2026-09-17. An external model returned leads. **The leads are not the
authority; the sources' actual behaviour is.** Everything below was probed.
Nothing here changes a frozen study or a strategy.

## P0 — L5_ORDER_ID_FORENSICS_V1: can we get order ids for arbitrary L5 trades?

### At the API layer: **NO.** Measured, not read from documentation.

| probe | result |
|---|---|
| `data-api.polymarket.com/trades?user=<L5>` | **HTTP 200**, 19 fields, and **not one** of `trader_side`, `maker_orders`, `maker_address`, `order_id`, `matched_amount`, `taker_order_id`, `orderHash` |
| `clob.polymarket.com/trades` | **HTTP 401** `Unauthorized/Invalid api key` |
| `clob.polymarket.com/trades?maker_address=<L5>` | **HTTP 401** — the filter does not make it public |
| `clob.polymarket.com/data/trades` | **HTTP 401** |

The CLOB trades endpoint is authenticated and scoped to the calling key's own
account. **Documentation describing it is evidence about our own account and
nothing else**, which is exactly the inference the owner ruled out.

### At the chain layer: **YES.** Verified by decode.

Every public trade row carries a `transactionHash`. The CTF Exchange emits, per
fill, an event with four topics and seven data words:

```
topic1 orderHash   topic2 maker   topic3 taker
word0 makerAssetId  word1 takerAssetId  word2 makerAmountFilled
word3 takerAmountFilled  word4 fee
```

Identified **by structure** in a real L5 transaction rather than by a
remembered signature, and cross-checked against the public row: 5,000,000 units
for 125,000,000 units = **$0.04 × 125 shares**, matching `price 0.04, size 125`
exactly, with **fee 336,000 = 0.07 × 0.04 × 0.96 × 125** to the cent.

So `order_id`, the order owner's address, the counterparty order hashes,
matched amounts **and the per-fill fee** are all publicly retrievable for
arbitrary traders — from the chain, never from the API.

**Not claimed:** maker vs taker. The `fee > 0` split is the *fee-bearing /
fee-free* label. Deciding maker/taker needs the `OrdersMatched.takerOrderHash`
cross-check, which this run does not do, and asserting it from the fee would be
circular with the only label the BTC15m work said was recoverable.

### The cluster question, on a preregistered sample

`SAMPLE_SPEC` (seed 20260917, uniform over qualifying clusters) was fixed
**before any receipt was fetched** — a cluster sample chosen after seeing which
ones resolve is not a sample.

Population: **85,754 clusters** of ≥2 fills sharing (market, timestamp,
outcome, price); 487,929 singletons. Drawn: 30 clusters + 10 singleton
controls.

| | |
|---|---|
| UNRESOLVABLE | **15 / 30** |
| MULTIPLE_PARENT_ORDERS | 6 |
| SAME_PARENT_ORDER | 9 |
| **of resolved clusters, share with one parent** | **0.60** |
| **of resolved FILLS, share collapsing into one parent** | **0.5806** |

**The unresolvable half is a retrieval limit, not a decoding bug, and that was
measured rather than assumed:** both public RPCs return `null` for those
transaction hashes, and *every* unresolvable cluster had **all** of its
receipts missing. Public Polygon nodes prune old receipts; this tape runs
2026-03 → 2026-08.

**Read with its n.** Fifteen resolved clusters is a small sample and the figure
is an estimate, not a measurement of the population. What it does establish is
that **the collapse is real and common** — same-instant clusters are frequently
one parent order being swept in pieces, which is the direct evidence
`L5_CAUSAL_MECHANISM_V1` inferred from consequences (P1 clustering) and could
not see.

## P0 — HISTORICAL_L2_OVERLAP_V1: do the dataset leads exist?

### `wzsg/polymarket-orderfilled-v2` — **REAL, OPEN, and verified against our own decode**

| | |
|---|---|
| rows | **575,630,861** |
| size | 33.6 GB Parquet (ZSTD), 123 shards, partitioned by UTC month |
| coverage | **2026-04-28 11:04:48 → 2026-08-03 23:47:33 UTC** |
| schema | `order_hash, maker, taker, side, token_id, maker_amount_filled, taker_amount_filled, fee, block_number, transaction_hash, log_index, contract` |
| provenance | open-source collector, `github.com/wzsg/polymarket-analytics`, daily incremental |

Its schema is **exactly** the event I decoded by hand, which is the strongest
verification available: an independent source and our own chain read agree on
the field set.

**It is NOT historical L2.** It is the fill tape with order hashes — no
cancellations, no resting orders, no depth. It would remove the RPC-pruning
limit above for **2026-04-28 → 2026-08-03**, which overlaps our BTC15m sample
substantially, and it answers nothing about the book.

### `rocklabs-io/polymarket-dataset` — **REAL and it IS the L2 lead, but GATED**

The repository exists and contains a README and LICENSE **only — no data.**
The documented CLOB Orderbook Events product is what HISTORICAL_L2_OVERLAP_V1
needs:

* **31.6B+ rows, Jan 2026 – Present, 221K markets**, hourly `raw/YYYY-MM-DD/HHMM.jsonl.zst`
* genuine captured websocket `feed_message` records: `book` snapshots and
  `price_change` deltas
* `price_change` entries carry `price`, `size`, `side`, **`hash` (order hash)**,
  `best_bid`, `best_ask`, `sequence_id` — and **`size: 0` means the level is
  removed**, i.e. cancellations are present as genuine events
* separate on-chain fills product: 882M+ rows, Nov 2022 – Present

**Access is gated:** *"We provide free access to these datasets for students
and academic researchers. To request access, contact us."* There is no public
download path. **No data was retrieved and none of the coverage claims above
are independently verified** — they are the vendor's documentation, read
carefully and reported as documentation.

**I have not contacted them.** That is an outward-facing request and it is the
owner's call.

**One consequence worth flagging if access is ever obtained.** The `hash` field
on each `price_change` level is an order hash. Joined against the on-chain
`OrderFilled.orderHash`, that would **independently establish identity** — it
would let us say *"this resting size was L5's order"* rather than the
deliberately weaker *"liquidity at L5's eventual execution price"*. That is the
one route seen so far that could lift the caveat, and it depends entirely on
access we do not have.

### `PolyOrderbooks` and `Marketlens / marketlenstrade` — **NOT VERIFIED**

Neither could be checked. GitHub search is unavailable in this session
(*"sessions are bound to their configured repositories"*), so name-based
discovery is not possible here. **This is an inability to verify, not evidence
of absence**, and they are left open rather than dismissed. A specific
`owner/repo` would be checkable immediately.

## Status

Findings and verifications only. No frozen study touched, no strategy changed,
no gate created. `L5_BOOK_CONTEXT_V1`, the Gemini BTC05M surface shadow and the
frozen PMUS tape all ran undisturbed throughout.
