# Gemini 5m: first closed windows (D) and the continuous-inventory counterfactual (E)

Observation only. No Gemini order was placed. No PMUS, risk, cap, band,
admission, sizing or concurrency change. Nothing here is realized.

## 0. What was blocking this, and it was mine

Sections D and E were not blocked by coverage. They were blocked by a
**read-path gap in the collector I built**: the canonical trajectory rows are
written to the Railway volume `/state/gemini-5m`, which only the collector
container can read. A window could complete with 91.9% in-window coverage
and be invisible to every analysis running anywhere else.

Fixed in `32cb80aa` (window-close digest) and `6a551dc9` (print rows, because
STRICT/SUPPORTED/OPTIMISTIC cannot be separated without the prints). Both are
additive and read-only; the volume write stays the system of record.

## D. First closed windows

Source: `gemini-5m-collector` deployment `9f11d488` serving `6a551dc9`.
All three chunk-intact (`COMPLETE_CHUNKS`). Numbers recomputed from the
series by the consumer, not copied from the collector.

| market | obs | obs/min | in-window span | max gap | two-sided % | one-sided obs | prints | spread p50 | depth p50 | complete |
|---|---|---|---|---|---|---|---|---|---|---|
| GEMI-BTC05M**2609141455**-UP | 237 | 50.18 | **283.40 s** | **31.01 s** | 83.45 | 47 | 9 | **0.18** | 10 | NO |
| GEMI-ETH05M**2609141455**-UP | 139 | 57.08 | **146.11 s** | 1.10 s | 49.12 | 144 | 0 | **0.30** | 283 | NO |
| GEMI-BTC05M2609141450-UP | 79 | 54.36 | 87.20 s | 4.30 s | 57.25 | 59 | 1 | 0.07 | 10 | NO |

**NO WINDOW IS COMPLETE YET** under the >= 290 s criterion, and the threshold
was not relaxed to manufacture one. The two 14:50 windows opened before the
collector was up, so only the 14:55 pair is a fair test of coverage:

* **BTC 14:55 missed by 6.6 s** (283.40 of 290) and carries a **31.01 s
  interior hole** at tte 49.8 -> 18.79. Span alone would have hidden that
  hole, which is why `max_gap_s` is now reported beside it.
* **ETH 14:55 is the real coverage story and it is not a collector fault.**
  Its max gap is 1.10 s -- the 1 Hz sampling never faltered. It stops at
  tte 149.78 because **the book stopped being two-sided**: 144 of its 283
  in-window samples were one-sided, so only 49.12% were states at all. A
  one-sided book is recorded raw but is correctly not a canonical state.

Classification of the ETH shortfall: **TRUE_NO_QUOTE**, not
SOURCE_COLLECTION_GAP and not EXTRACT_ARTIFACT. The venue was not quoting
two-sided, and that is a fact about Gemini ETH 5m, not about our pipeline.

Collector health at the same moment: `subscribed 6 == expected_active 6`,
`bbo_rows 1191`, `reconnects 1`, `errors 0`, `alarm null`, disk 48.87 GB.

## E. Continuous-inventory counterfactual

Canonical policy **unchanged** (`pmus_l5_persistence_v2`, model
`bbb55bf90638d2bd`, via `l5_venue_adapter.proposal_v2`), residual cap at the
live rung's 6, post-only and the [0.20, 0.80] band binding exactly as live.
Research action ceiling 400, deliberately not the live `MAX_ACTIONS=6`.
Run on the three windows above and labelled NOT-COMPLETE.

### E1. The persistence change works

Per window, median: **L5_BEHAVIORAL_ACTIONS 82, VENUE_ADMISSIBLE_ACTIONS 30,
requotes 25, intentions 8.** The action ceiling never bound.

The two counters are far apart, which is the whole point. BTC 14:55 produced
**8 intentions but 62 venue orders and 54 requotes**, and **one single
intention survived 190.44 s and 52 requotes**, walking its price from 0.21 to
0.80 as the touch moved. Under the live shell, which reads a touch move as
termination, that one intention would have been 53 separate terminated
episodes -- and the live `MAX_ACTIONS=6` would have ended the session inside
the first few seconds.

**So the shell is not what stops the engine from acting on Gemini 5m.**

### E2. But there are ZERO fills, in every tier including OPTIMISTIC

| tier | matched | residual | no_fill | fills |
|---|---|---|---|---|
| STRICT | 0 | 0 | 3 | 0 |
| SUPPORTED | 0 | 0 | 3 | 0 |
| OPTIMISTIC | 0 | 0 | 3 | 0 |

OPTIMISTIC is a front-of-queue upper bound. Its being zero means **this is
not a queue-position question at all** -- there was no trade on our side of
the book to be at the front of. Two causes, both measured:

**1. The flow was entirely one-directional, and the canonical open-side rule
put us on the other side.** All 9 BTC 14:55 prints were taker SELLS
(`buyer_is_maker` true). `open_side_yes` is the shared cross-venue
convention -- *"open the cheaper touch, ties to YES"* -- and the cheaper
touch was DOWN every single time, which rests as a **YES ask** and can only
be reached by a taker BUY. **There were no taker buys.** Meanwhile **6 of
those 9 sells landed at or through the YES bid** -- they would have filled an
UP-side maker.

**2. The spreads are wide enough that trades happen between the touches.**
BTC spread p50 0.18, ETH 0.30. **3 of the 9 BTC prints landed INSIDE the
spread**, unreachable by a maker at the touch on *either* side.

### E3. What this says about the Gemini 5m thesis

Gemini 5m scores habitat 0.4061 against PMUS's 0.108 and the unchanged engine
still captures almost nothing. Sections D and E locate that gap precisely:

* it is **not** the rest/cancel shell -- persistence produced 8 intentions and
  190 s of continuous working inventory in a single window;
* it is **not** queue position -- the OPTIMISTIC upper bound is also zero;
* it **is** that the habitat score rewards price movement and two-sided
  quoting, and **neither implies reachable two-sided FLOW**. A market can
  oscillate beautifully, quote both sides, and still trade only inside a
  wide spread against one-directional takers.

This is a **missing feature in the Habitat Score**, stated as a finding and
not acted on: the score has no term for *directional balance of taker flow*
or for *what fraction of prints are reachable by a maker at the touch*. The
score gates nothing today and still gates nothing.

Sample is 3 windows, one of them not usable for flow. This is a mechanism
finding, not a venue verdict. The >= 30 matched campaigns / >= 30 complement
fills floor is untouched and nothing is promoted.
