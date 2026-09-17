# L5_HABITAT_SCORE — can a passive quote surface actually live here?

`analysis/l5/habitat/score.py` · `habitat_v1.json` · real observations only ·
**no composite score**

> **CORRECTED 2026-09-17.** The first version of this file reported the
> **median market** and called it the venue. On a venue with 325 markets of
> which most never trade, the median market is idle, so it reported
> *"Gemini 0.0000 at every horizon, PMUS 0.0086–0.0595"* — which **understated
> both venues and got their ranking wrong**. Two defects are fixed and both
> statistics are now reported side by side:
>
> * **POOLED** sums each market's own counts. An observation is only ever
>   completed by a trade **in the same market** — an intermediate version
>   merged the event streams and let market X be completed by market Y, which
>   inflated PMUS to 0.83. That artifact is gone.
> * **Polymarket BTC15m was `UNMEASURABLE`**, not zero: the recorder sees L5's
>   fills and the venue publishes no price on that row. The market's own public
>   prints were joined afterwards, read-only, without touching the running
>   recorder.

The primary diagnostic is **post-fill continuation opportunity**: after
acquiring one side, does a credible **opposite-side** interaction actually
arrive, and how soon? `MATCHED_SHARE_LINEAGE_V1` found that the complement has
to arrive within tens of seconds to be worth anything, so a venue with plenty
of volume and no timely opposite interaction is one where L5's mechanism cannot
run — however busy it looks.

**Identical definitions everywhere, different economic mappings.** An
*interaction* is a trade that actually printed: prints where the venue
publishes them, a rise in cumulative traded quantity priced at the venue's own
`last` where it does not. **Book movement is never counted as interaction** —
that conflation already cost us a 97%-vs-46% error once. What a *pair* means is
not flattened:

| venue | mapping |
|---|---|
| PMUS / Polymarket | **two tokens, both bought**: cost = `b_yes + b_no` |
| Gemini | **one contract** (the `-DOWN` book does not exist): `BUY b + SELL s`, the sale a **synthetic** complement worth `1 − s` |

## The primary diagnostic

P(opposite interaction within Δ) after a hypothetical first-side fill.
`harder_side` is the minimum of the two directions — a surface needs whichever
side it did **not** just fill.

### POOLED — per-market counts summed, never cross-market

| venue | ≤5 s | ≤30 s | ≤60 s | ≤120 s | n two-sided obs |
|---|---|---|---|---|---|
| Gemini BTC05M | 0.0042 | 0.0205 | 0.0368 | 0.0713 | 3,803 |
| **PMUS sports** | **0.0292** | **0.0592** | **0.0969** | **0.1307** | 20,404 |
| Polymarket BTC15m (L5 native) | 0.0244 | 0.0297 | 0.0393 | 0.0626 | 942 |

### Every other component, reported separately — MEDIAN MARKET

| component | Gemini BTC05M | PMUS sports | **Polymarket BTC15m** |
|---|---|---|---|
| markets | 18 | 325 | 8 |
| two-sided uptime | 0.7612 | **0.9712** | 0.8258 |
| reversal frequency / min | 5.835 | **0.000** | 3.491 |
| **spread (median ticks)** | 22.0 | 5.0 | **1.0** |
| touch depth, harder side | 10.0 | **2,600** | 149.5 |
| **capacity** (median touch notional) | **$4.40** | **$1,650** | **$156** |
| interaction frequency / min | 0.500 | 0.000 | 7.165 *(pooled; median market 0)* |
| maker/post-only validity | 1.0000 | 1.0000 | 1.0000 |
| known fees | **venue_declared_unknown** — not estimated | 0.07 taker-only, tick 0.01, min size 5 | same as PMUS |
| known rewards, kept separate | unknown | declared eligibility only: rewards min size 50, max spread 4.5 — **nothing credited, nothing estimated** | same |

**`composite: null`, deliberately.** A composite lets a failed component hide
behind a passing one.

## Reading

**The venue ranking is the opposite of what the median reported, and the
corrected picture is more interesting.**

**PMUS sports has the best post-fill continuation** — roughly **2×** Gemini at
every horizon and **~2×** Polymarket BTC15m at 60–120 s — plus **$1,650** at
the touch and 0.97 two-sided uptime. Its weakness is that it **never
oscillates**: 0.000 reversals/min. Pre-event sports books sit still, and
`MATCHED_SHARE_LINEAGE_V1` found that *reversals*, not drift, are what generate
matched pairs.

**Gemini BTC05M has an excellent price and almost no counterparty.** A 22-tick
spread implies a pair at the touch near 0.71–0.80 against ~0.98 on PMUS — and
it is barely reachable: **$4.40** at the touch, 67 interactions across 18
markets, and pooled harder-side continuation of **0.0042 → 0.0713**. The
prospective surface shadow says the same thing far more directly: **17
evidenced fills, zero complements at any mark.**

**L5's own venue is not the best on continuation — it is the tightest and the
choppiest.** Polymarket BTC15m runs a **1.0-tick spread** with **3.49
reversals/min**, 0.826 two-sided uptime and $156 at the touch. A one-tick
spread means the pair at the touch is around **1.01**: there is no spread to
capture. What that venue offers is **constant two-sided flow at a price where
being the resting side, repeatedly, is the entire edge** — which is exactly the
mechanism `L5_CAUSAL_MECHANISM_V1` inferred and the parent-order work has now
confirmed on chain.

### Why L5's directly-measured recycling is far higher than this number

The prospective residual clock measured L5's *actual* episodes recycling at
**0.40 / 0.60 / 1.00** by 60 / 120 / 300 s. This table says a random two-sided
moment on his venue sees the harder side within 120 s only **6.3%** of the
time. Both are correct and they measure different things:

* this metric asks *"if you were filled at an arbitrary moment, would the other
  side trade?"*
* L5's episodes are the moments he **was** filled — a selected subset — and he
  is already resting on **both** sides, so his complement can come from his own
  standing quote rather than from a new opportunity.

**A continuation probability measured from a random moment understates what an
already-resting two-sided surface achieves.** That gap is itself the argument
for the surface: presence beats reaction.

## Caveats, stated rather than buried

* Gemini rests on **67 interactions**; per-market medians are small-n.
* The PMUS book tape is **still collecting** (closes ~17:15Z). These are
  partial figures. The frozen preregistered sweep verdict is a separate
  artifact and is untouched.
* `expected_credible_interactions_remaining` is unavailable for PMUS sports:
  those markets have no uniform window length, and inventing one would be
  worse than the gap.
* Polymarket BTC15m had **no measurable habitat** until 16:04Z because the
  recorder only wrote where L5 traded — exactly the selection bias a habitat
  measure must not have. It now emits a periodic public book summary; the
  recorder was restarted once for this and the gap is disclosed here.
* L5's own fills are the only interaction stream visible on the Polymarket
  recorder. They are a **lower bound** — everyone else's trades are invisible
  to it — and the row carries no price, so they cannot feed the continuation
  test. Counted and named, never silently used.
