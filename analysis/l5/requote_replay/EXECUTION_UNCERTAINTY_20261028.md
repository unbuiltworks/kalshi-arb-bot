# Traversal fills are POSSIBLE fills — and what that costs the conclusion

This supersedes the single figure in `FOUR_WINDOW_REPLAY_20261028.md`. The
replay's "+$0.09 on window 0015z" was reported as though the counterfactual
path were the one that would have happened. It is one of several, and the
spread between them is wider than the figure itself.

## Where the traversal inference is strong

Observing a best bid strictly **below** our resting own price, while our
order is live and un-cancelled, does imply consumption. A resting bid at
0.44 *is* the best bid at 0.44; the touch cannot read 0.42 while our 0.44
still rests. Partial fills do not weaken it either — an unfilled remainder
would still set the touch at 0.44.

So conditional on the order resting, traversal is good evidence. The
engine's own queue discipline makes the same distinction and is stricter
about the other direction: *"cross_price_depth is a PRICE DISTANCE and is
cleared by withdrawal, which trade volume cannot measure, so no fill may be
claimed from it either way."*

## Where it is weak — and these dominate

1. **The order may never rest at all.** The recorded `latency_s` on the
   0015z refusals runs **4.7 s to 32.2 s** between decision and send. The
   requote prices from a book ≤ 2 s old; the venue sees that order tens of
   seconds later. A post-only order whose price has since crossed is
   **rejected**, and the submission claim is consumed anyway. This is the
   single largest source of error in the replay and it is not modelled at
   all: every counterfactual order is assumed accepted.
2. **Sparse sampling cuts both ways.** 0015z has ~16 book snapshots across
   887 s. The first counterfactual order (own 0.44, placed +154 s, TTL to
   +274 s) is scored unfilled because the only snapshot inside its TTL shows
   bid 0.47. An unobserved dip below 0.44 would have filled it earlier and
   changed everything after. Fill *times* are therefore approximate and
   fill *counts* are a floor.
3. **Divergence.** After the first counterfactual fill, every recorded
   decision came from a different inventory, cash and round state. Side
   selection, sizing and whether to open at all would all differ. The replay
   derives later legs from the book, which is state-independent, but it
   cannot reproduce the engine's actual choices.
4. **Quantity.** Counterfactual legs reuse the recorded 1.87 / 1.92. Under a
   different cash and inventory state the engine would have sized
   differently.

## The honest range for 0015z, which is wide

The one counterfactual pair in the entire replay is **conditional on a
rebound we did not control**. The UP leg filled at own 0.44 *because* the
market fell 0.46 → 0.42 → 0.36 — we bought UP while UP was losing. It was
completed at own 0.49 only because the touch then recovered to 0.63.

| if the DOWN complement… | 0015z effect |
|---|---|
| fills (the observed rebound) | pair 0.4400 + 0.4900 = 0.9300, **+$0.1309 gross**, +$0.1109 net |
| does not fill | UP 1.87 @ 0.4400 strands; UP settled 0 ⇒ **−$0.8228** |

So the requote's effect on the one window where it demonstrably acts spans
roughly **+$0.11 to −$0.82**. The favourable end is the one the tape
happens to show; the unfavourable end is the same setup without the
rebound, and nothing in the mechanism prefers one.

**Corrected conclusion.** The replay does not establish that the requote
improves net economics. It establishes that the requote *acts* — it
converts stale-price refusals into real, non-crossing, correctly-priced
maker orders — and that on this tape the one resulting pair was profitable.
Whether that is positive in expectation is **not** answered here, and is
precisely what the next campaign has to measure.

## Residual-loss exposure

The requote does not raise any ceiling. What it changes is how often the
engine reaches them:

* one resting order at a time, so at most one unmatched leg exists at once;
* residual bounded by `min(k × clip, 3)` contracts — the runtime reports
  `effective_cap_contracts: 3`, `which_binds: ABSOLUTE`;
* worst case per window is therefore the approved
  `L5_MAX_WORST_CASE_DEFICIT_USD = 3.00`;
* across the campaign the $5 daily-loss halt binds before four windows of
  worst case could accumulate, and the $20 cumulative cap sits behind it.

So the bounded downside is unchanged by the requote. The *expected*
downside rises with fill frequency, because every additional opening fill is
another leg that may fail to complete inside the 0.96 ceiling — which is
exactly the dominant loss already measured, and which neither correction
addresses.

**Adverse selection is the mechanism to watch, not a hypothetical.** A
requote fills cheaply *because* the touch is moving against the side being
bought. The next campaign must account requoted fills separately from
unrequoted ones, with `ticks_lower` against each fill's eventual
settlement, or the result will not distinguish a throughput gain from a
cost-basis illusion.
