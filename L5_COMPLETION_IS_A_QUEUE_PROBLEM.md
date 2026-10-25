# Completion is a QUEUE problem, not a price-ceiling problem

**Venue-observed, authenticated feed, production `l5-pmus-live` at 2026-10-25T23:20-23:29Z.**
Single window (`cpc-btc-updown-15m-2026-10-25-2315z`), 501 `L5_SHADOW_RESIDUAL_BOOK_SAMPLE`
rows over 539 s. One window, so this is a **single-window observation, not a corpus
measurement** — but the magnitude makes the direction robust.

## The measurement

Each sample prices the complement we would need to finish a pair, against the real book.

| | |
|---|---|
| samples | 501 |
| samples with **zero bids on the wire** | **262 = 52.3%** |
| samples where a **taker cross is impossible** (`insufficient_displayed_depth`) | **262 = 52.3%** |
| samples where a taker cross is possible | 239 = 47.7% |

**Maker completion — contracts queued AHEAD of us, as a multiple of what we need:**

| p10 | median | p90 | min | max |
|---|---|---|---|---|
| 626x | **4,758x** | 40,865x | **14x** | 42,464x |

- queue ahead >= 1x our need: **501 / 501 = 100.0%**
- queue ahead >= 100x: 487 / 501 = 97.2%
- queue ahead >= 1000x: 412 / 501 = 82.2%

**There was not one sample in 501 where we were not queued behind more than we needed.**

## Why this reframes the whole program

The complement join price is **0.9900 in 262 of 501 samples** — the maker pair identity
`1 - spread` on a one-cent book, exactly the 0.99 at which L5 forms pairs on the full corpus.
So the price *is* there. What is not there is **queue position**: joining that rung puts us
behind a median 4,758x our need, and the engine's actual quote sits **two ticks behind the
touch at 0.9700**, which is cheaper in own price and therefore *less* likely to fill.

This is the mechanism behind the only venue-confirmed result we have. Across 7 BTC15m windows
we realized **-$2.1448 on $4.5790 of acquisition (-46.8%)**, and **2 of the 7 lost exactly 100%
of acquisition cost** — first leg filled, complement never arrived, residual settled worthless.
That is 69% of the loss from 2 of 7 windows. The books above say why.

## What follows

**Raising the 0.96 ceiling to 0.99 would not have completed those pairs.** The ceiling is a
permission; the queue is the constraint. A permission to pay 0.99 is worth nothing while 42,283
contracts sit ahead of a 1-to-3 contract need and half the time there is no bid to cross at all.

This retires the ceiling as the candidate fix, and it does so from venue books rather than from
a shadow P&L. It also explains, without any new hypothesis, why three deployed gates were found
non-binding: the configuration was never what stopped us.

**Honest limits of this evidence:**

- One window. The obvious extension is the same measurement across many windows, which needs
  only the log stream already running — no new service, capital, or authorization.
- `queue_ahead_bound` is explicitly an **upper** bound; the venue publishes no per-order queue
  position. The true queue may be smaller. At 42,283 contracts against a need of 1-3, the
  direction survives a large error in that bound.
- Our own first-leg price is absent in these rows (`why: "no_first_leg_price"`), because the
  shadow carries modelled inventory. The pair costs are therefore not computed here; the queue
  and bid-presence figures do not depend on them.

## Consequence for the target

It sharpens the capital verdict rather than softening it. At 188x less capital we need a **small**
fill; the queue ahead is measured in tens of thousands of contracts regardless of our size. Being
small does not help us jump a queue, and the one thing that reliably buys queue priority —
posting early and resting through the window, as L5 does across ~108 fills — is exactly what a
$5 book and a one-outstanding-order limit forbid.
