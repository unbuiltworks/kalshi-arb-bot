# What public fills cannot reveal

Read this before drawing any conclusion from the dataset. Several of the
most natural-sounding inferences are **not available** from this data at
all, and a few are available only in a weakened form. Being specific
about which is which is the point of this document.

The dataset is a record of **executions only** — trades that happened. It
is not a record of intent, and it is not a record of the market.

---

## 1. Orders are invisible. Only fills survive.

Not in the data, at all:

* **Orders that were placed and never filled.** If L5 quoted both legs and
  only one got hit, you see the one that got hit. The other leg leaves no
  trace whatsoever.
* **Cancellations.** Quote, cancel, requote, cancel — all invisible. A
  pattern you read as "waited 9 seconds, then bought" is equally
  consistent with "quoted continuously for 9 seconds and got filled at
  the end", and the data cannot separate those.
* **Order type.** Limit vs market, post-only, GTC vs IOC vs FOK, any
  time-in-force at all.
* **Order size as submitted.** `size` is the size that *filled*. A 200-lot
  that filled 5 at a time and a sequence of 5-lots are identical here.
  **This is a load-bearing ambiguity**: much of the apparent "incremental
  sizing" in the fill sequences could be partial fills of larger resting
  orders.
* **Queue position, and whether L5 was maker or taker.** This corpus
  carries **no maker/taker flag**. (A separate, much smaller `takerOnly`
  feed covering ~4 days exists in the repository but is not part of this
  package, precisely because asserting "maker" outside its coverage would
  be a guess.) So "L5 is a maker" is a hypothesis to be argued, not a
  fact you can read off these files.
* **Self-trades, and whether the counterparty was related.**

## 2. The order book is invisible.

The dataset contains L5's own prices, and nothing else about the market:

* No bid, no ask, no spread, no depth, at any moment.
* No way to know whether L5's price was aggressive or passive.
* No other participants' trades, so no measure of market volume, of L5's
  share of it, or of who was on the other side.
* `min_price` / `max_price` in the window summary are over **L5's own
  fills**, not the market's range. Do not read them as a price path.
* **No underlying price.** These are BTC/ETH up-down windows, and the
  spot path that decided each window is not here. You cannot tell whether
  a fill was informed by a spot move.

A corollary worth stating plainly: you **cannot** compute adverse
selection, slippage, effective spread capture, or fill-quality-versus-BBO
from this package. Every one of those needs a book.

## 3. Timing resolution is one second, and it is the venue's clock.

* Timestamps are **whole seconds**. Two fills in the same second are
  unordered; `trade_seq` breaks the tie by transaction hash, which is
  arbitrary, not chronological.
* It is the **settlement/indexing** timestamp, not the moment of L5's
  decision. Latency between decision, submission, match and indexing is
  unknown and not constant.
* Therefore: **no latency claim, and no sub-second reaction claim, is
  supportable.** An apparent "reacted within 1 second" is at the
  resolution floor.

## 4. Inventory is reconstructed, not observed.

Per-fill inventory in this package is arithmetic on the fill sequence. It
is exact *given the fills*, but:

* It is **per window**. Cross-window and cross-asset position, and total
  capital deployed at any instant, are computable only by summing these
  windows — and that sum is correct only if the corpus is complete.
* **Completeness is assumed, not proven.** A missing fill shifts every
  subsequent inventory figure in that window. The corpus is believed
  complete over its span (both files reconcile and dedupe cleanly) but
  there is no independent audit against an on-chain reconstruction.
* FIFO lot-matching is a **convention**. It is the defensible one and it
  is stated, but L5's own accounting may differ, so `effective_pair_cost`
  is our cost basis, not necessarily L5's.

## 5. Settlement coverage is biased toward L5 winning.

Settlement is recovered from REDEEM records — and a REDEEM exists only
when L5 held something in the money and redeemed it. Windows where L5
ended with nothing worth redeeming leave **no settlement evidence**.

So the labelled subset is **not** a fair sample of all windows for any
question that is itself about winning. 17,042 of 17,046 windows carry a
label here, so the practical exposure is small — but the mechanism is
real and matters if you subset further.

## 6. Economics are partial.

`window_pnl` omits, by necessity:

* **Rebates and rewards** — 203 records, all with empty slug, so
  unattributable to any window (see the data dictionary §5).
* **Gas and transaction costs.**
* **Transfers in and out**, so capital base and return-on-capital are
  unknown.
* **Any activity outside Polymarket.** If this strategy is one leg of a
  cross-venue position, the hedge is elsewhere and invisible here. **A
  window that looks like a directional bet may be a hedged position whose
  other leg is on another venue.** Nothing in this data can rule that out.

Consequently: the sum of `window_pnl` is **not** L5's realized P&L, and
no statement about L5's profitability, Sharpe, or return on capital is
fully supportable from this package.

## 7. Identity rests mostly on provenance.

Independent identity corroboration covers **3,175 transactions (~0.5%)**
of the nameless 706,500. For the remaining 99.5%, the evidence that these
are L5's trades is that the API returned them for a request naming L5's
address. That is good evidence. It is not the same as a signed
attestation, and if the venue's API ever mis-attributed a page, this
corpus would carry it.

## 8. Intent, identity and infrastructure are all unobservable.

* Whether this is one human, a team, or a bot; whether one strategy or
  several multiplexed through one account.
* Whether a pattern is a rule, a parameter, or an emergent consequence of
  something else entirely.
* Whether a change across the span is a strategy change, a code change, a
  venue change, an incentive-programme change, or a change in who was at
  the keyboard.
* Any API keys, rate limits, co-location, or infrastructure.

## 9. Survivorship and span.

* The corpus starts 2026-03-19 and ends 2026-08-31. **Earlier activity, if
  any, is not here**, and the API caps historical offsets (which is why
  the corpus needed a gap backfill at all). The first fill in the file is
  not provably L5's first fill ever.
* Nothing after 2026-08-31 is in this package. Do not extend any
  conclusion past that date.
* 10,794 of 17,046 windows are in August alone. **The corpus is heavily
  weighted to its last two weeks.** Any corpus-wide average is
  substantially an August average. Always check whether a finding survives
  when the early months are analysed separately — the package is sharded
  chronologically to make that easy.

---

## The honest summary

This data can support strong claims about **what L5 executed, in what
order, at what prices, with what resulting inventory, and how that
inventory settled**.

It cannot by itself support claims about **what L5 intended, what L5 saw,
what L5 cancelled, what the book looked like, whether L5 was maker or
taker, how fast L5 reacted, or what L5 actually earned.**

Claims of the second kind can still be argued — from structure,
from economics, from what is consistent across thousands of windows — but
they are inferences, and anything proposed from this package should say
which kind it is.
