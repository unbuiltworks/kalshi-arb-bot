# P10 — data-semantics validation. Status: DECODER UNVALIDATED.

**Date:** 2026-09-22 · **No live orders. Nothing armed. No thresholds tuned.**

---

## The result that governs everything else

Running the P9-F and adaptive arms over the historical corpus **under each
reading of the tape** gives:

| lane | split | arm | ALL_H1 (what we published) | ALL_H2 (the other reading) | **bound** |
|---|---|---|---|---|---|
| 5m | dev | S | −14.17 | **+75.45** | [−14.17, +75.45] |
| 5m | dev | A3 | −13.43 | +32.88 | [−13.43, +32.88] |
| 5m | val | S | −20.86 | +42.10 | [−20.86, +42.10] |
| 15m | dev | S | −46.07 | **+47.03** | [−46.07, +47.03] |
| 15m | dev | A3 | −25.03 | +1.40 | [−25.03, +1.40] |
| 15m | val | S | −32.61 | +25.92 | [−32.61, +25.92] |

Three mixed readings (each row independently H2 with probability ½, three
seeds) land inside the interval without collapsing toward either end.

**The sign of every historical arm is set by the decoding assumption, not by
the data.** The decoder's coverage on that corpus is **0%** — it carries no
books — so a "decoded-only" figure for it would be the empty set, and this
bound is the honest summary. `analysis/gemini/p10_out/p10d_bounds.txt` has the
full table.

Everything that depends on assigning a trade to a side is now marked
**PROVISIONAL** in P6, P7, P8, P8AB, P8_AUDIT, P9 and P9D. The earlier claim
that a decoding error could only flatter a positive result is **withdrawn**,
and **P9-F is no longer described as proven conservative.**

---

## P10-A — capture, with the clock on both records

`p10a_capture.py` records, per poll, the request time, the response time and
the round trip; per trade, the venue's `tid`, `timestampms`, `type`, `price`
and `amount` verbatim, de-duplicated by `tid`; per book, `bestBid`, `bestAsk`,
`lastTradePrice` and the four `buy/sell × yes/no` quotes.

Measured so far: **book staleness before a trade, median 1.7–1.8 s**
(p90 2.1–5.0 s); **trades-poll round trip, median 0.22–0.27 s**. A cycle over
4 symbols is ≈ 2 s at 4.5 req/s, so staleness is dominated by cycle length, not
by network.

**A collection defect, found and fixed in flight.** The first two captures
drew all four slots from Crypto because discovery ranked instead of
round-robining — so they did not sample the families whose tapes show the
strongest mirror signature. Discovery now round-robins across Crypto, Weather,
Politics and Economics and **records the per-category candidate count beside
the slate**, so a silently empty category cannot repeat this quietly. A
recurring read-only capture routine now runs hourly.

---

## P10-B — decode, or refuse

The rule and its structural limit are in `P10_DECODER_SPEC.md`. A trade is
classified only when exactly one of H1 (`p` is contract-space) and H2 (`1−p`
is) is compatible with a book inside the staleness window; everything else is
`AMBIGUOUS_BOTH`, `AMBIGUOUS_NEITHER`, `STALE` or `PRE_CAPTURE`, and none of
those is folded into a class.

Coverage on the first BTC-only captures (small, and the first thing the
hourly routine will grow):

| capture | observed trades | H1 | H2 | AMB both | AMB neither | decoded |
|---|---|---|---|---|---|---|
| A · Bitcoin | 10 | 3 | **0** | 6 | 1 | 30% |
| B · Bitcoin | 11 | 6 | **0** | 0 | 4 | 55% |
| B · Ether | 1 | 1 | **0** | 0 | 0 | 100% |

The structural limit predicted by the spec is visible: `AMBIGUOUS_BOTH`
dominates exactly where the book is wide (BTC5m, width ≈ 0.28), because a wide
book contains both `p` and `1−p`.

---

## P10-C — the mirror, on the evidence available

On the tid-bearing pull of 4,761 contracts, **51,344** apparent (p, 1−p) pairs
within 2 s, excluding prices near 0.50:

| | |
|---|---|
| identical `tid` | **0 / 51,344** |
| identical amount | 30,636 (59.7%) |
| identical aggressor `type` | 47,855 (93.2%) — 46,921 of them `buy`/`buy` |
| millisecond separation | median **0**, p25 −48, p75 0 |
| \|tid gap\| | median 4, **mode 1** (15,270 pairs) |

Same millisecond, adjacent ids, same aggressor label, a majority sharing a
quantity. That is **consistent with** two records emitted by one matching
event — and also with two separate executions at complementary prices. The
venue documents `type` as the aggressor side, and one match has one aggressor,
which argues against two same-`type` rows being one match *unless* the two rows
live in two different books. **It is not settled, and it is not being promoted
to fact.** The book test separates the two and needs the capture.

**A tension the capture must resolve.** The same-second mirror rate is 20–56%,
yet **0 of 11 decoded rows are H2**. Both rows of a genuine mirror pair are
equally decodable — if `p` is far from 0.50 then so is `1−p` — so the decodable
region is *not* biased against detecting H2. Either mirroring is rarer than the
same-second statistic suggests, or the decoder is wrong. Eleven rows cannot
tell us which.

---

## P10-E — the second decoder

`p10e_audit_decode.py` imports nothing from `p10b_decode.py` and decides by
nearest-touch distance with a 2¢ margin rather than interval containment.

* rows both classified as H1/H2: **11**
* **agreement: 11 / 11 = 100%**
* the independent rule has higher coverage (70% vs 30% on capture A) because a
  price just outside a moved book is still nearest its own touch

The cross-tab shows where they differ and why: `AMBIGUOUS_NEITHER → H1` (5
rows) is the book having moved between snapshots, and `AMBIGUOUS_BOTH → H1` (3
rows) is a wide book that containment cannot split but distance can. A
stratified sample is printed for manual inspection; the rows checked so far are
consistent with the labels, but **11 agreeing rows is not the validation gate**.

---

## P10-F — the holdout is untouched

`p10f_forward_aug.py` writes to `analysis/gemini/forward_aug/` and a guard
refuses any path that resolves inside `analysis/gemini/forward/`. Three tests
assert that the augmented collector can neither read nor write the sealed
directory, and that no module in the P10 capture path opens it. The original
collector still writes exactly what it wrote before.

---

## What would change this status

The five gates in `P10_DECODER_SPEC.md`: coverage, internal consistency,
independent agreement, manual inspection, and mirror forensics on captured
(p, 1−p) pairs with books attached. None is met on a sample worth the name.

Until they are, the historical Gemini arms have a **bound, not a result**, and
no family ranking, no forward shadow test and no strategy conclusion follows
from them.

## Diagnostic: G2/G5 by BTC duration family, 2026-09-22T23:20Z

**Not a gate score.** `p10i_subpop.py`, run because BTC15m's book is about a
quarter as wide as BTC5m's and the spec predicts that should matter. The
pre-registered gate is an aggregate and stays one.

| family | obs | decoded | H2 | G2 ambiguous | G5 structural | median width |
|---|---|---|---|---|---|---|
| BTC5m | 96 | 55 | 0 | 27.1% | 81.5% | 0.240 |
| BTC15m | 47 | 25 | **1** | **14.9%** | 78.1% | 0.080 |
| BTC hourly ladder | 15 | 8 | 0 | 46.7% | 53.3% | 0.070 |

Splitting ambiguity by KIND is what makes this legible, because the two kinds
have different causes and only one of them is a property of the venue:

| family | AMBIGUOUS_BOTH | AMBIGUOUS_NEITHER | median book age |
|---|---|---|---|
| BTC5m | **24** | 2 | 1.49s |
| BTC15m | 1 | **6** | 1.32s |
| BTC hourly | 2 | 5 | **3.24s** |

* **BTC5m's ambiguity is structural and irreducible.** 24 of 26 ambiguous rows
  are `AMBIGUOUS_BOTH`: the 0.240-wide book genuinely contains both `p` and
  `1 − p`, exactly as `|p − 0.5| ≤ w/2` predicts. No amount of polling fixes
  this; only a narrower book does.
* **BTC15m's ambiguity is a freshness problem, not a structural one.** Only 1
  row is `AMBIGUOUS_BOTH` — the narrow book almost never admits both readings —
  while 6 are `AMBIGUOUS_NEITHER`, meaning the book moved between the poll and
  the print. Its 14.9% ambiguity is the best of the three and well inside G2.
* **So BTC15m IS the reliably decodable subpopulation the narrow spread
  predicted**, and its lower G5 (78.1%) does not contradict that: G5 scores
  "predicted decodable == actually decoded", so a `NEITHER` row counts as a
  structural failure when its real cause is a stale book. G5 conflates the
  structural limit with the recorder's freshness. **This is an observation
  about what G5 measures, not an argument for changing it** — the threshold was
  pre-registered and stands as written.
* **The hourly ladder is the worst performer and its 3.24s median book age is
  why.** It is a recorder started at 23:09Z watching 4 symbols, not a venue
  property.

### The conflict of interest this creates, stated rather than resolved

The hourly-ladder recorder was launched **after** the gate was pre-registered,
by me, to raise throughput. It is dragging G2 and G5 down. Removing it now
would raise both toward passing, which is precisely why it stays: dropping a
BTC recorder after seeing that its rows hurt is discarding inconvenient data.
Its symbol count is also left alone for the same reason — tuning the capture
after seeing which family costs the most is not a neutral act, whatever it is
called. The gate scores every BTC row collected.
