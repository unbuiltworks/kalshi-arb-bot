# PMUS vs Polymarket BTC15m: it is the venue, and not for the reason I said

**Verdict: PMUS BTC15m does not have the executable flow to reproduce L5's
behaviour. The BTC15m NO-GO is reinstated, on different and firmer evidence
than it originally rested on.**

I have now been wrong in both directions on this question, so the basis
matters more than the conclusion. What follows is the evidence and what would
overturn it.

Script: `analysis/pmus/reconcile/cross_venue.py`.

---

## What changed my mind back

Your clarification was the reason I found it. Treating PMUS and Polymarket as
one market hid the finding; separating them exposed it.

**80.3% of PMUS BTC15m observations show `bid_sz == ask_sz == 500.00`
exactly** (18,661 of 23,318 rows), and **65 of 136 windows never change touch
size at all.** 199 distinct sizes do occur, so 500 is not a display cap or a
rounding artefact — it is a quote.

PMUS BTC15m is a **market-maker-quoted market**, not an order-flow market. A
symmetric 500×500 at a 2-tick spread, refreshed rather than consumed.

## The two venues side by side, at their own cadences

| | PMUS | Polymarket Intl |
|---|---|---|
| windows / observations | 68 / 18,742 | 55 / 31,611 |
| mean sampling interval | 12.3 s | 2.3 s |
| **queue at own-side touch, p50** | **500** | **62** |
| queue p10 / p90 | 250 / 500 | 10 / 288 |
| share of observations under 100 shares | **4.3%** | **62.0%** |
| median spread | **2 ticks** | **1 tick** |
| best-bid changes per window, p50 | **4.6** | **196.5** |
| own-level size decreases per window | **1.7** | **~175** |

Coverage: PMUS 2026-08-30 → 09-02; Polymarket 2026-08-21 → 24. **There is no
contemporaneous overlap in local data** — these are seven days apart, so this
is a same-instrument, different-week comparison, not a simultaneous one.

**The queue figure is the one to trust.** It is a level read off the book, not
an event count, so it is unaffected by sampling rate. 500 versus 62 is a real
structural difference. The event counts (touch moves, size decreases) are
partly confounded by the 12.3 s versus 2.3 s cadence and should be read as
indicative only — though correcting PMUS upward even fourfold leaves ~18 moves
per window against Polymarket's 196.

## Why high volume and no fills are consistent

The PMUS 2-second capture showed 123,914 shares traded in 635 seconds — real,
substantial volume. It is not in tension with the above once the mechanism is
stated:

Trades hit the market maker's 500, and the market maker **replenishes** rather
than being depleted. Own-level size decreases run **1.7 per window** across
18,742 observations. Under FIFO, an order resting behind that 500 never
advances, however much volume prints. The 51.8% of volume printing at or below
the bid is sells hitting the maker's bid — not flow reaching our queue
position.

So the earlier observation that "64,132 shares printed at or below the bid"
was true and I drew the wrong inference from it. Volume at our price is not
the same as volume *through* our queue position.

## The four explanations, quantified

| # | explanation | weight | evidence |
|---|---|---|---|
| 1 | **Different venue liquidity and opposing flow** | **dominant** | queue p50 500 vs 62; 4.3% vs 62.0% of observations under 100 shares; 1.7 vs ~175 own-level decreases per window; 2-tick vs 1-tick spread |
| 4 | **L5-specific mechanisms unavailable on PMUS** | **large, ~40% of his acquisition** | 24.1% mint-feasible + 16.4% confirmed atomic par-pair, both CTF complete-set; PMUS spec has no equivalent (combos need distinct contracts, are conjunctions, not enabled) |
| 3 | **Missing trade data / replay limits** | **real, smaller than I claimed** | on PMUS rows, 27 s sampling sees 27.3% of touch moves and 50.4% of own-level decreases — not the 0.6% I reported, which was a **Polymarket** figure I wrongly let stand for PMUS |
| 2 | **Placement, timing, queue modelling** | **modest** | direction inference scored against real exchange buy/sell on 94 PMUS prints: **72.9% accurate where it commits, 25.5% dropped as unattributable**; one-shot entry ceiling (already fixed) |

Explanation 1 is primary and 4 compounds it: the Polymarket flow L5 is reached
by is partly *created* by the CTF mint primitive, which gives every
opposite-side buyer a way to match him without an offer on his book. PMUS has
neither the crowd nor the primitive.

## Correcting my last two messages

- **Reinstated:** PMUS BTC15m lacks sufficient executable flow. Now grounded
  in queue structure, which is sampling-independent, rather than in modelled
  fill counts, which were not.
- **Withdrawn:** my claim that 99.4% of queue-depletion events are invisible
  at our cadence *on PMUS*. That 0.6% figure came from Polymarket data. On
  PMUS the comparable figure is 50.4% visible. The sampling defect is real but
  it is not what is costing us fills here, because PMUS has few such events to
  miss.
- **Still withdrawn:** `PLACEMENT_VERDICT.md`'s pair costs and completion
  rates as precise quantities. They were computed on the 27 s tape. Their
  *direction* is now corroborated by independent structural evidence, but the
  numbers themselves remain imprecise.

## What would overturn this

1. **A contemporaneous capture of both venues**, same hours, same cadence.
   None exists locally; this is the single biggest gap.
2. **PMUS touch size falling below ~100 with any regularity** in a category we
   have not yet measured. The 500×500 pattern is a BTC15m finding and must not
   be generalised to PMUS as a whole — sports market types were never measured
   this way, and the running sports study's numbers come off the same 27 s tape
   and so inherit the imprecision above.
3. **Depth beyond the touch.** The PMUS BBO tape carries only touch size. If
   the second and third levels are thin, a quote resting one tick behind the
   maker could sit in a much shorter queue than 500 — this is the one placement
   idea the data does not yet rule out, and it needs a laddered PMUS capture
   to test.

## What I did not do

No contemporaneous cross-venue comparison, because the data does not exist
locally. No PMUS ladder analysis beyond the touch, for the same reason. I
deployed nothing and armed nothing; everything here is local read-only
analysis of existing captures.

## Labels

Queue sizes are public touch sizes, attributed to no trader. No fill is
claimed. `UNKNOWN_DECREASE` remains ambiguous between trade and cancellation.
Nothing here is realized profit.
