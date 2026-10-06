# LADDER V1 — the structure reproduces, the selection does not. KILL.

Spec fingerprint fixed before evaluation:
**`LADDER_V1_SYMMETRIC_TWO_SIDED_AT_TOUCH`**. Historical replay on
third-party public data; no order sent; **nothing here is realized profit
for our account.** No live orders. No V3, no threshold sweep.

## Power, stated first

27 markets carry L5 fills inside the L2 overlap; 64 markets have books.
The independent unit is the **market**. Grouped leave-one-market-out only
— **no out-of-sample claim is made.**

## 1. L5's observed fill process

637 bursts (fills ≤5s apart). **30.9% of all bursts, and 71.6% of bursts
with ≥3 fills, are filled on BOTH outcomes.**

| fill class | share |
|---|---|
| RESTING_BID_CONSISTENT | **62.0%** |
| MINT / at-or-above-ask | **29.0%** |
| INSIDE_SPREAD | 9.0% |

Leg qty median **23.61** (p90 80, max 320); leg price p10 0.08, median
0.50, p90 0.88 — he quotes across the whole range.

## 1b. The MINIMUM implied resting set

Orders per burst: median **2**, p90 **5**, max **32**. Levels per outcome
within a burst: 508 bursts at 1 level, 165 at 2, 73 at 3, and a long tail
to 17.

**This is a strict LOWER BOUND.** Orders that never filled are
unobservable, so the true resting set is at least this and may be far
larger. No claim is made about L5's actual orders — ownership is
unobserved.

## 2–3. Conservative replay, with the circularity removed

The fill rule is **independent of L5**: a resting bid at price `p` is
filled only when the observed best bid falls **strictly below** `p` — the
book walked through the level. No touch=fill, no invented cancellations,
and L5's own fills are never used as execution evidence for the candidate.

Resting bids are **maker**, and Polymarket's `feeSchedule` is
`takerOnly` (rate 0.07, exponent 1), so the candidate pays **zero fees by
the venue's own schedule** — the most favourable fee treatment available,
and a sourced term rather than an assumption.

| | candidate | L5 observed |
|---|---|---|
| fills, 27 overlap markets | **439** | **1,917** |
| ratio | **0.229** | — |
| fills/market median (max) | 16 (**34**) | 13 (**208**) |
| median fill price | **0.420** | **0.590** |
| median inter-fill gap | **11.0s** | **12.2s** |
| filled both sides | **27 of 27** | — |
| matched fraction | **0.600** | **0.7034** |
| residual per market | **saturates at 94.4 shares** | unbounded |
| **pair cost p25/median/p75** | **0.806 / 0.868 / 0.906** | 0.9195 / **0.9679** / 1.0136 |

### What reproduces

**The two-sided structure does.** A single symmetric resting bid on both
outcomes, replenished after each fill, fills **both sides in 27 of 27
markets** and lands an inter-fill cadence within 1.2s of L5's. That is the
hypothesis confirmed: both-outcome bursts need no switch decision, only
simultaneous resting interest.

**Residual control works, and it is the structural answer to item 4.**
Suppressing the heavier side at an 80-share gap caps residual at **94.4
shares in every market** — against L5's unbounded residual that cost
**−$30,654**. The control variable is indeed *what remains available to be
filled*, not an active switch.

### What does not reproduce — and the pair cost is the tell

The candidate's pair cost is **median 0.868 against L5's 0.968**. That is
not a success; **it is too good, and that is diagnostic.** Our fill rule
only triggers when the book declines through a resting level, so the
replay harvests a favourable subset of fills and misses everything L5 got
in rising or stable books. The same artifact explains the **0.229 fill
ratio** and the missed tail: candidate max 34 fills/market against L5's
**208**.

## Economics — not defensibly positive

| scope | markets | total cash | per market | **median** | win rate |
|---|---|---|---|---|---|
| all candidate markets | 64 | +$19.83 | +$0.31 | **−$11.33** | **38%** |
| only where L5 traded | 27 | +$134.58 | +$4.98 | **−$13.46** | 41% |
| **where L5 did NOT trade** | 37 | **−$114.74** | −$3.10 | −$10.39 | 35% |

Mean marginally positive, **median clearly negative, win rate 38–41%**,
and at zero modelled fees. A result whose mean is positive while its
median is −$11 is carried by a few markets, which is the same fragility
the corpus-level analysis found (net is a 6.9% residue of gross).

**And the candidate trades where L5 chose not to**: 703 fills across 37
markets L5 never touched, losing $114.74. **Market selection is a
behaviour the ladder spec does not contain**, and it is where L5's edge
partly lives.

## Advancement gate — NOT MET

| condition | verdict |
|---|---|
| two-sided burst behaviour | **PASS** — both sides in 27/27, cadence within 1.2s |
| bounded residual | **PASS** — saturates at 94.4 shares by construction |
| matched-inventory accumulation | **PARTIAL** — 0.600 vs 0.7034 |
| fill quality | **FAIL** — 22.9% of fill volume, misses the heavy tail entirely |
| pair-cost distribution | **FAIL** — 0.868 vs 0.968; too favourable, a selection artifact |
| positive economics after fees | **FAIL** — median −$11.33 at 38% win rate, with zero fees |

**KILL Ladder V1 as a tradable candidate.** No shadow architecture is
built; that step was explicitly conditional on this gate.

## 5. The mint, quantified correctly

**29.0% of L5's fills are mint/at-or-above-ask; 62.0% are ordinary resting
fills.** So completion depends on mint-compatible execution for under a
third of its volume. The mint is an **execution mechanism**, not the
switch signal — consistent with V2, where the mint sum carried 93.4% of
its mass in one 2¢ bin at exactly the base rate.

## What Ladder V1 did establish

1. **Both-outcome fills need no switch decision.** The structural
   hypothesis is confirmed on independent fill evidence.
2. **Residual is controllable by quote suppression**, and that is a
   mechanism we can implement without predicting anything.
3. **The missing variable is market and timing selection**, not ladder
   geometry. L5's 37 untouched markets lost us $114.74.
4. **A naive ladder cannot be replayed honestly from book-walk evidence
   alone** — the rule's own selectivity biases pair cost favourably, so a
   positive replay under this rule proves little.

The next genuinely informative object remains **prospective**, where order
ownership, placement, cancellation and queue position are observable. No
archive supplies them.

## One correction to my own earlier figure

The regime work could not reproduce my "Oct-5 median spread $0.1400,
n=47,922, 41.4% ≥$0.20". Pooled it gives n=61,033 / median 0.15 / 44.0%;
BTC05M-only n=46,173 / median-of-window-medians 0.225 / 61.6%;
dedup by `(market,lastUpdateId)` n=18,112 / median 0.18. **My exact subset
is not reproducible from those directories**, so the robust statement is
the range, not my figure: **~$0.02 in late August became $0.15–$0.225 in
October, a 7–11× widening.** The conclusion is unchanged; the precise
number should not be quoted again.

Two further corrections from that work, both of which revise claims I
relayed: **both** `gemini_bbo_btc05m` and `btc15m` are UP-leg only because
**the venue lists only the UP instrument** — a derivation gap
(`DOWN ask = 1 − UP bid`), not a lost feed, so those files *can* speak to
pair cost. And on Oct-5, **BTC15M's median single-leg spread was $0.08
with 0% of window medians ≥$0.20 and 110 shares at best bid**, against
BTC05M's $0.225 / 61.6% / 25 shares — the 15-minute book was materially
the better habitat that day, on 28 windows (a hypothesis, not a regime
finding).
