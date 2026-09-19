# Why we miss naturally occurring executable flow

**P0 research question.** BTC5m is thin and bursty, not dead. This is the
forensic on every print of 10 contracts or more, the TTE comparison against
L5's own history, and the three-arm overlay.

**Model A is unchanged.** Every decision replayed below comes from the frozen
`continuous_probe.plan`. This study chooses no price, size or action, sends
nothing, and every fill in it is **SHADOW/THEORETICAL**.

Code: `large_prints.py` (17 tests), `overlays.py` (33). Reproduce:
`python3 -m src.gemini_execution.large_print_report`,
`python3 -m src.gemini_execution.overlay_report`.

---

## 1. The print-size census — six prints are the market

| bucket | prints | contracts | $ notional | share |
|---|---|---|---|---|
| <10 | 82 | 208.21 | 101.58 | 6.6% |
| 10–25 | 35 | 358.78 | 186.48 | 12.1% |
| 25–100 | 2 | 122.00 | 56.60 | 3.7% |
| **100+** | **6** | **2,024.00** | **1,199.03** | **77.7%** |

**Six prints carry 77.7% of all notional across 19 windows.** Their TTEs:
**41.8, 56.5, 57.0, 61.9, 126.0, 127.0** — every one of them below TTE 130,
and four of the six below TTE 62.

## 2. The forensic on the 43 prints of ≥10 contracts

| bucket | prints | with a reconstructed book | native would reach us | improve-1 would reach us |
|---|---|---|---|---|
| 10–25 | 35 | 13 | **7** | 7 |
| 25–100 | 2 | 2 | **2** | 2 |
| 100+ | 6 | 5 | **3** | 3 |

**12 of the 20 reconstructable large prints would have reached our native
quote.** That is a very different picture from the cycle-level view, where
only 15 of 483 cycles saw any reaching print — because most cycles have no
print at all. **For the prints that happen, we are usually at a price that
would have been hit.**

**And native and improve-1-tick reach exactly the same large prints.** A
312-contract print clears any queue at that level. **Queue position is
irrelevant for precisely the prints that carry 77.7% of the notional.** It
matters only for small ones.

22 of the 35 prints in the 10–25 bucket have **no reconstructed book** — our
own cycles stop around TTE ~104 and those prints fall below it. They are
recorded and not evaluated, rather than guessed at.

## 3. The inventory path — and not one matched pair forms

Six hypothetical fills were replayed forward through the frozen policy.
Immediate mark movement is not the outcome; this is:

| seed | price | replayed cycles | policy actions | follow-on fills | final inventory | settled | residual gross |
|---|---|---|---|---|---|---|---|
| NO | 0.65 | 5 | COMPLETE, EXTEND | 1 | NO 0.02 | no | **+$0.0131** |
| NO | 0.64 | 6 | COMPLETE, EXTEND | 2 | NO 0.03 | no | **+$0.0167** |
| YES | 0.85 | 0 | — | 0 | YES 0.01 | no | −$0.0085 |
| YES | 0.80 | 0 | — | 0 | YES 0.01 | no | −$0.0080 |
| YES | 0.17 | 0 | — | 0 | YES 0.01 | no | −$0.0017 |
| NO | 0.42 | 13 | COMPLETE, EXTEND | 0 | NO 0.01 | no | **+$0.0058** |

**In all six paths, `matched_pair_economics` is `null`. Not one matched pair
ever forms.** The frozen policy issued COMPLETE on every path that had cycles
left, and **no COMPLETE ever filled**. The only follow-on fills were
**EXTEND** — same side, at 0.65 and then 0.04. We accumulate the side being
sold to us and never obtain the other.

That is the adapted-policy decomposition's `BROAD_LEAKAGE` reproduced
prospectively on a different venue, from real prints.

**The P&L signs here mean nothing.** All six contracts resolved **NO**. The
three NO seeds won and the three YES seeds lost; the arithmetic sum is
+$0.0174, and had the six coins landed the other way it would be −$0.0174.
**Six coin flips that all landed the same way is not evidence about a
strategy**, and three of the six paths had *zero* cycles to replay because the
print landed below where our cycles reach.

## 4. TTE — does our envelope exclude L5's habitat?

L5's own historical BTC 5-minute activity (Polymarket, **6,229 trades,
$129,040**) against Gemini's executed notional:

| TTE | L5 trades | L5 $ | **Gemini $** |
|---|---|---|---|
| >300 | 0.0% | 0.0% | **5.9%** |
| 280–300 | 6.5% | 7.8% | 2.0% |
| 240–280 (our entry) | 7.4% | 7.8% | 2.5% |
| 180–240 | 16.1% | 17.0% | 2.0% |
| 120–180 | 23.0% | 24.3% | **21.0%** |
| **60–120** | **29.9%** | **30.1%** | 15.9% |
| **30–60** | 13.0% | 10.2% | **50.7%** |
| 0–30 | 4.0% | 2.9% | 0.0% |

**The answer is no.** L5's single busiest bucket is **TTE 60–120 (30.1% of
dollars)** — which our envelope *does* cover for OPEN and EXTEND, since
`no_new_open_extend_tte_s` is 60. Our boundaries exclude **13.1%** of L5's
historical dollars (below TTE 60) and **2.9%** (below TTE 30). They do not cut
off L5's habitat.

**What they do cut off is Gemini's.** 50.7% of Gemini's notional is in TTE
30–60, where L5 historically did 10.2% and where we may only COMPLETE. And L5
never traded above TTE 300 at all — its 5-minute windows are exactly 300 s —
while Gemini's contracts trade from TTE ~377, a pre-window region L5's
behaviour has no analogue for.

**So the bounds are well matched to L5's habitat and badly matched to
Gemini's. Gemini's liquidity profile is roughly 60 seconds later than the
venue L5 was learned on.** That is a transfer problem, not a bounds error, and
I am not proposing to move either bound.

## 5. The three arms

JOIN_BEST is dropped: `ticks_native_behind_best` is **0 in all 476 cycles**
the policy quoted, so NATIVE and JOIN are the same order.

| arm | distinct prints reached | quote-instances filled | mean outbid share of rest |
|---|---|---|---|
| **NATIVE** (reprices every ~8 s) | **2** | 1 | — |
| **IMPROVE_1_TICK** (reprices every ~8 s) | **6** | 5 | — |
| IMPROVE + 15 s minimum rest | 7 | 7 | 37.8% |
| IMPROVE + 30 s minimum rest | 11 | 13 | 43.5% |
| IMPROVE + 45 s minimum rest | 15 | 17 | 45.9% |
| **IMPROVE + 60 s minimum rest** | **17** | 24 | 47.1% |

**Native reaches 2 distinct prints across 19 windows. Improving by one tick
and simply not repricing for 60 seconds reaches 17 — 8.5×.** The gain is
monotone in rest duration and the cost is mild: even at 60 s the quote is
outbid for only 47% of the rest.

Quote-instances exceed distinct prints because overlapping rest windows mean
one print can satisfy several hypothetical quotes; the distinct-print column
is the honest one.

**No rest duration is chosen.** The owner asked for buckets, and a test scans
the module for any constant a future reader could mistake for a choice.

IMPROVE_1_TICK was maker-only in all 483 cycles and is refused wherever it
would not be.

## 6. The answer: A, B, C or D?

Attributing all **$1,543.69** of executed notional across 19 windows to the
reason we did not interact with it:

| cause | prints | $ notional | share |
|---|---|---|---|
| **C — timing: TTE 30–60, where we cannot OPEN** | 7 | **782.54** | **50.7%** |
| **C — timing: TTE >280, before our band opens** | 60 | 122.71 | **7.9%** |
| **A — price: our quote would not be reached** | 21 | 70.84 | **4.6%** |
| **B — queue: consumed ahead of us** | 6 | 18.84 | **1.2%** |
| no evidence (our cycles do not cover it) | 21 | 243.75 | 15.8% |
| **reachable — we would have been there** | 10 | **305.01** | **19.8%** |

**(C) TIMING/TTE — 58.6%, and it dominates everything else by an order of
magnitude.** Almost all of it is one bucket: TTE 30–60 carries half of all
notional and our envelope is in COMPLETE_ONLY there and withdrawn 30 seconds
later.

**(A) PRICE — 4.6%.** Small, and consistent with §5: we are at the best bid
100% of the time. Price is very nearly not the problem.

**(B) QUEUE — 1.2% by notional**, though it is the difference between 2 and 6
distinct prints reached, because the prints it costs us are the small ones.
Queue position matters for print *count* and barely at all for *notional*.

**(D) GENUINELY ABSENT FLOW — real but not the largest cause.** 88.8% of
adjacent-cycle intervals carry no print, and only 3.1% of our 483 quoting
cycles saw any reaching print. But **$305.01 (19.8%) of notional was
reachable at our own price with our own queue position** — the flow was there
and we were not trading.

**Ranking: C ≫ D > A > B.**

One caution on (C): the reason the envelope leaves TTE 30–60 is that the
outcome is nearly determined there — the median print price in that bucket is
0.44 but it contains the 714-contract print at **0.79**, 42 seconds before a
contract that settled YES. Being the maker on the other side of that is
exactly the adverse selection this programme has been measuring. **The 50.7%
is not free money and I am not proposing to go and get it.**
