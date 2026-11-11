# Gemini trade-tape decoder — specification v0 (NOT YET VALIDATED)

**Status: DRAFT. No historical conclusion may rely on it until P10-E agreement
is measured on a real sample.** This is the specification the P10 capture
exists to validate or kill.

## 1. What the venue actually documents

From the authoritative REST reference for `/v1/trades/:symbol`
([List Trades](https://developer.gemini.com/trading/rest-api/market-data/list-trades)):

> **type** — "buy means that an ask was removed from the book by an incoming
> buy order. sell means that a bid was removed from the book by an incoming
> sell order."
>
> **tid** — "The trade ID number" · **price** — "The price the trade was
> executed at" · **amount** — "The amount that was traded" ·
> **timestamp / timestampms** — "Gemini strongly recommends using milliseconds"

Two things follow. `type` is the **aggressor side**, and `price` is a price
that was **resting on a book**. Neither statement says *which* book.

That matters because the prediction-markets order API
([spec](https://developer.gemini.com/prediction-markets-spec)) carries a
**second dimension the trades feed does not report**: an order has both a
`side` (buy/sell) and an `outcome` (yes/no). The tape has `type` and no
`outcome`.

And the two outcomes are exact mirrors of each other. From a live contract's
`prices` object:

```
buy:  {yes: 0.99, no: 0.05}     sell: {yes: 0.95, no: 0.01}
bestBid: 0.95   bestAsk: 0.99
```

`buy.yes + sell.no = 1.00` and `sell.yes + buy.no = 1.00`, and
`bestBid/bestAsk` are the YES-side touch. So a NO execution at q and a YES
execution at 1−q are the same point on one ladder, and **price alone cannot
say which outcome traded.**

## 2. The two hypotheses

| | reading | contract-space price | contract-space aggressor |
|---|---|---|---|
| **H1** | the printed price is in the contract's (YES) space | `p` | `type` as printed |
| **H2** | the printed price is the complement's | `1 − p` | the opposite of `type` |

H2 flips the side as well as the price: buying NO at p is selling YES at 1−p,
and an aggressor that lifted the NO ask did so by hitting the YES bid.

## 3. The decision rule

For a trade at venue time `tms`, take every book observation of the same
instrument within `max_stale_s` of it (before or after). Write the YES touch
as `[bid, ask]` and allow one tick of slack.

```
h1_fits  =  ∃ book :  bid − tol ≤ p     ≤ ask + tol
h2_fits  =  ∃ book :  bid − tol ≤ 1 − p ≤ ask + tol

h1_fits and not h2_fits   ->  H1
h2_fits and not h1_fits   ->  H2
both                      ->  AMBIGUOUS_BOTH
neither                   ->  AMBIGUOUS_NEITHER
no book within the window ->  STALE          (never folded into a class)
trade precedes our first
book for that instrument  ->  PRE_CAPTURE    (backfill, not a failure)
```

**No row is forced into a side.** AMBIGUOUS is a result.

## 4. What bounds coverage, structurally

`p` and `1 − p` are both inside a book of width `w` centred at `m` whenever
both lie within `w/2` of `m`. So a trade is decodable roughly when

```
|p − 0.5|  >  w / 2
```

which makes coverage a property of the **venue's spread**, not of our method:

| lane | median book width | decodable price region | prediction |
|---|---|---|---|
| BTC5m | 0.280 | p outside [0.36, 0.64] | poor coverage |
| BTC15m | 0.060 | p outside [0.47, 0.53] | good coverage |

This is a falsifiable claim about the decoder and the first thing the capture
should confirm or refute.

## 5. Validation gates, none of them met yet

1. **Coverage** — decoded share of observed (non-PRE_CAPTURE) trades, by
   family, with staleness reported alongside.
2. **Internal consistency** — the book before and the book after a trade must
   give the same verdict; disagreement is an error estimate.
3. **Independent agreement** — `p10e_audit_decode.py` decides by nearest-touch
   distance with a margin, shares no code with `p10b_decode.py`, and must
   agree on the rows both classify.
4. **Manual inspection** — a stratified random sample checked by hand against
   the captured books.
5. **Mirror forensics** — `p10c_mirror.py` on apparent (p, 1−p) pairs: tid,
   millisecond separation, quantity, aggressor labels, which reading fits the
   book, and how many distinct book states appear between the two rows.

**Until 1–5 are satisfied, every historical result that depends on a side
assignment stays PROVISIONAL, and the honest summary of the historical arms is
the P10-D bound, not any point estimate.**

## 6. Open questions the capture must answer

* Does H2 ever occur? Early captures decode **8 of 8 as H1** — too few to
  conclude anything, and decoded rows are by construction the ones furthest
  from 0.50, which is where mirroring is easiest to see and hardest to hide.
* If mirroring is real, are the two rows of a pair distinguishable by anything
  other than price — `amount`, `tid` adjacency, the number of book states
  between them?
* Does the venue ever report a prediction-market match as ONE row? A single
  row per match plus the documented `type` semantics would make H1 universal
  and this whole question closed.
