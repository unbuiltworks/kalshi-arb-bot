# L5 observability: canonical tape, venue limits, vendors, prospective recorder

P0 (data), 2026-09-21. Read-only throughout. **Nothing was purchased.**

## Phase 1 — canonical fill tape, and the wall it hits

Rebuilt independently of the existing derived datasets, joined on a portable
identity and never overwriting anything:

    key = (transactionHash, conditionId, outcomeIndex, side, price, size, timestamp)

`asset` (token id) was **not** usable as a join key: it is present on every
`activity.jsonl` TRADE item and absent from all **692,461** `activity_gap`
items. `conditionId` + `outcomeIndex` identify the same token and are present in
both, so that is the key. Provenance is recorded per source.

| source | TRADE items | first-seen unique | note |
|---|---|---|---|
| `data/l5/raw/activity.jsonl` | 75,864 | 71,984 | has `asset`; also 4 MAKER_REBATE, 3 TAKER_REBATE, 3 REWARD |
| `data/l5/raw/activity_gap.jsonl.gz` | 692,461 | 688,833 | no `asset` |
| **deduplicated universe** | | **760,817** | 17,046 markets, 128 active days, 2026-03-19 → 2026-08-31 |

**The universe cannot be labelled maker/taker.** Verified, not assumed:

- `/activity` items carry **no** maker/taker field at all.
- `/trades` carries it only through the `takerOnly` parameter.
- Max usable offset on `/trades` is exactly **10,000** — binary-searched,
  10,001 returns HTTP 400.
- `startTs`, `endTs`, `before`, `after`, `from`, `min_timestamp` are **all
  silently ignored**; each returned the newest trades unchanged. There is no
  time filter, so history cannot be walked.

So the reachable labelled evidence is two complete windows, and the taker set
reaches back further than the full set because takers are the minority:

| fetch | unique rows | span |
|---|---|---|
| `takerOnly=true` | 10,500 | **13.54 days** |
| `takerOnly=false` | 10,488 | 1.02 days |

Reconciliation against the two existing derived datasets:

- **Venue label vs chain label.** `takerOnly` gives maker share **80.9%**; the
  chain-derived `l5_role` in `l5_backfill.json` gives **88.3%** over a different
  (Zenodo-overlap) window. Two independent sources, same direction, different
  windows — not merged, and neither overwritten.
- 0.40% of taker rows were absent from the full set (pagination edge), recorded
  rather than discarded.

## Phase 2 — the decisive questions

Answered in full in **`analysis/l5/TAKER_AUTOPSY_20260921.md`** (items 1–8 and
the transition matrix, TRAIN/OOS, with shuffled and state-matched nulls,
effect sizes and cluster-bootstrap intervals).

**H1 — L5 normally makes/extends and disproportionately uses aggressive fills to
acquire the opposite outcome after inventory accumulates: FALSIFIED.** Taker use
falls monotonically with run length (23%→5% TRAIN, 8%→1% OOS) inside every
time-to-settlement band; `MAKER EXTEND → TAKER OPPOSITE` is the rarest of the
four transitions (1.8% / 0.4%); the observed pooled effect is reproduced almost
exactly by a within-market label shuffle (+0.0562 vs +0.0536 observed) and is
zero and sign-reversed out of sample. Taker is an **OPEN** instrument
(P(taker|OPEN) = 87%/77%) and an **urgency** instrument (5%→52% as TTE runs out).

**H2 — a substantial portion of apparently exceptional L5 maker execution is
explained by MINT matching rather than same-outcome FIFO queue advancement:
SUPPORTED AS COUNTERPARTY COMPOSITION, NOT AS A SEPARATE QUEUE.** Directly on
chain, L5's fills sit in a transaction carrying `PositionSplit` in **73.5–85.5%**
of single-match transactions against a **58.6%** baseline for non-L5 fills in the
same blocks (block-clustered CIs exclude 0 for three of four classes). But per
`2d84a002` the two outcome tokens are **one book in two conventions** (99.9%
price-level, 99.1% size agreement), so this is not a second fillable pool. It
says L5's counterparties are disproportionately buyers of the other outcome
rather than sellers of the same one — who fills him, not where he queues. The
mint-*feasibility* book test remains withdrawn as an identity.

Items 9–10, match-mechanism distribution by maker/taker, are **not yet computed
per label**: it needs the on-chain `PositionSplit` join applied to the newly
labelled windows. The machinery exists (`mint_onchain.py`); the join is the
remaining work.

## Phase 3 — historical L2 vendors. RECOMMENDATION: DO NOT BUY

Verified against the vendors' own pages, not against the claims as relayed.

| vendor | coverage from | resolution | deltas? | our product family | cost |
|---|---|---|---|---|---|
| **Marketlens** | **2026-03-01** | tick / millisecond | **yes** — "every level, every tick, nothing interpolated" | BTC/ETH/SOL/XRP/DOGE up-down windows | **free tier: 1M rows/day**, Pro $39/mo, Scale $199/mo |
| PolymarketData.co | market creation (Aug 2025 examples) | **1 minute**, stated as the maximum | **no**, snapshots only | not specified for 15m up/down | free tier exists, pricing not on that page |
| PolyOrderbooks | — | 1 second | snapshots | crypto only | REST / SDK / S3 |

Marketlens' coverage start of 2026-03-01 would span our L5 corpus
(2026-03-19 → 2026-08-31) essentially in full, and it is the only one claiming
delta-level reconstruction.

**Why we should still not buy, and why the free tier is the only thing worth
touching.** None of these vendors can say **which resting orders are L5's**.
Anonymous depth is anonymous at every one of them. The question we would be
buying data to answer — his queue position — is not answerable from any L2
archive, at any resolution, at any price. What an archive can improve is *book
reconstruction*, not *order attribution*.

We also already hold something better than the paid 1-minute tier for the
product family that matters: the Zenodo record 22084977 set is **1-second full
depth, both sides**, for BTC/ETH 15m, at zero cost.

**Recommendation.** Evaluate Marketlens on its **free** tier only, scoped to a
single question: does delta-level reconstruction add anything the 1-second
Zenodo snapshots do not, on our exact condition IDs? Spend nothing. Escalate for
a purchase decision only if that free evaluation shows a concrete gain, which on
the attribution argument above it probably cannot.

## Phase 4 — the prospective recorder is ~3 changes from sufficient

Target chain: **PRE-FILL BOOK → L5 ACTION → MATCH TYPE → POST-FILL BOOK.**

What `src/l5_live_habitat_service.py` does today, read from the code:

| link | status | why |
|---|---|---|
| PRE/POST-FILL BOOK | **fetched then discarded** | `intl_books_loop` calls `clob.polymarket.com/book?token_id=…`, which returns the **full ladder** — verified live: **43 bid levels, 42 ask levels**, plus `last_trade_price`, `tick_size`, `hash`, `timestamp` — and `best()` collapses it to one bid and one ask before `write()` |
| L5 ACTION | **absent** | nothing in the service polls L5's address; there is no `user=` query anywhere in it |
| MATCH TYPE | **absent** | needs the `PositionSplit` join by transaction hash |

**The smallest addition that closes all three:**

1. **Persist the ladder we already fetch.** Stop collapsing `bk["bids"]` /
   `bk["asks"]` in `intl_books_loop`. The bytes are already in hand; this is the
   single highest-value change and it costs one line plus storage.
2. **Add an L5 action poller.** `/trades?user=0xb945…&takerOnly=true` and
   `…=false` on a short cadence. The 10,000-offset cap is irrelevant to a live
   poller, which only ever needs the newest page — the cap only blocks walking
   *backwards*, which is exactly why history cannot be labelled.
3. **Join match type post hoc** by transaction hash via the existing
   `analysis/l5/historical_l2/mint_onchain.py`. No new collection.

Observation only. Polymarket International remains non-executable, and none of
this places an order.

**This is worth doing because L5 is still active** — `offset=0` on the taker
endpoint returned trades from today. Prospective capture with items 1–3 in place
would make the pre-fill book, the action, and the match type all *directly
observed* for every future fill, instead of reconstructed.

## WHAT WE NOW KNOW DIRECTLY

- **Taker is an OPEN and urgency instrument, not an inventory-completion one.**
  P(taker|OPEN) 87%/77%; P(taker) falls 23%→5% with run length inside every TTE
  band; rises 5%→52% as settlement approaches; `MAKER EXTEND → TAKER OPPOSITE`
  is the rarest transition at 1.8%/0.4%. Both eras.
- **L5 never sells** — 100% BUY across 20,988 labelled fills.
- **The maker/taker split itself**, from the venue: 80.9% maker (venue label),
  88.3% (chain label, different window).
- **L5's counterparties skew to complement buyers**: PositionSplit in 73.5–85.5%
  of his single-match transactions vs 58.6% for non-L5 fills in the same blocks.
- **The venue API's exact limits**: offset ≤ 10,000; no working time filter;
  `/activity` carries no maker/taker field.
- **The CLOB book endpoint serves full depth** and we are throwing it away.

## WHAT REMAINS INFERRED

- Maker/taker for the other **~740,000** corpus fills. Unreachable from this API.
- **Queue position, for anyone.** Not recoverable from anonymous depth at any
  vendor or resolution.
- Ordering within a second for **53.4%** of fills — recovered only up to a rule.
- Why the taker rate fell from 19.1% to 7.3% between the two eras.
- Whether the counterparty-composition effect has any cause we can act on.
- Match mechanism split **by maker/taker label** (Phase 2 items 9–10).

## WHAT PROSPECTIVE RECORDING CAN RESOLVE

With the three Phase-4 changes, and only these:

- Pre-fill and post-fill **full ladders** around every L5 action — directly
  observed, ending the reconstruction of book state.
- **Maker/taker at capture time**, with no offset cap, ending the history wall
  for everything recorded from now on.
- **Match type per fill**, by transaction hash.
- Whether the touch really falls toward his resting order, measured rather than
  inferred from lead-dependent snapshots.

It still cannot resolve **queue position** or **order ownership**: those need
orders we placed ourselves, which is a different experiment and is not
authorized here.
