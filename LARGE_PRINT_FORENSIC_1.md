# Why we miss naturally occurring executable flow

**P0 research question.** BTC5m is thin and bursty, not dead. This is the
forensic on every print of 10 contracts or more, the TTE comparison against
L5's own history, and the three-arm overlay.

**Sample: 26 windows, 154 prints, 3,686.14 contracts, $2,093.71.** Soak #4
added 7 windows after the first pass; **§7 records how much the attribution
moved when it did**, because it moved enough to matter.

**Model A is unchanged.** Every decision replayed below comes from the frozen
`continuous_probe.plan`. This study chooses no price, size or action, sends
nothing, and every fill in it is **SHADOW/THEORETICAL**.

Code: `large_prints.py` (17 tests), `overlays.py` (33). Reproduce:
`python3 -m src.gemini_execution.large_print_report`,
`python3 -m src.gemini_execution.overlay_report`.

---

## 1. The print-size census — six prints are the market

| bucket | prints | $ notional | share |
|---|---|---|---|
| <10 | 94 | 129.73 | 6.2% |
| 10–25 | 49 | 258.98 | 12.4% |
| 25–100 | 2 | 56.60 | 2.7% |
| **100+** | **9** | **1,648.40** | **78.7%** |

**Nine prints carry 78.7% of all notional across 26 windows.** The six from
the first pass sat at TTE 41.8, 56.5, 57.0, 61.9, 126.0 and 127.0 — every one
below TTE 130, four below TTE 62. The concentration held when the sample grew
from 19 windows to 26 (77.7% → 78.7%).

## 2. The forensic on the 43 prints of ≥10 contracts

| bucket | prints | with a reconstructed book | native would reach us | improve-1 would reach us |
|---|---|---|---|---|
| 10–25 | 49 | 24 | **10** | 10 |
| 25–100 | 2 | 2 | **2** | 2 |
| 100+ | 9 | 8 | **5** | **6** |

**17 of the 34 reconstructable large prints would have reached our native
quote.** That is a very different picture from the cycle-level view, where
only 15 of 483 cycles saw any reaching print — because most cycles have no
print at all. **For the prints that happen, we are usually at a price that
would have been hit.**

**And native and improve-1-tick reach almost exactly the same large prints** —
10 vs 10 in the 10–25 bucket, 2 vs 2 in 25–100, and 5 vs 6 in 100+. A
312-contract print clears any queue at that level. **Queue position is nearly
irrelevant for precisely the prints that carry 78.7% of the notional.** It
matters for small ones.

25 of the 49 prints in the 10–25 bucket have **no reconstructed book** — our
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
| >300 | 0.0% | 0.0% | **4.4%** |
| 280–300 | 6.5% | 7.8% | 1.5% |
| 240–280 (our entry) | 7.4% | 7.8% | 1.9% |
| 180–240 | 16.1% | 17.0% | 2.5% |
| 120–180 | 23.0% | 24.3% | **30.8%** |
| **60–120** | **29.9%** | **30.1%** | 18.9% |
| **30–60** | 13.0% | 10.2% | **38.8%** |
| 0–30 | 4.0% | 2.9% | 1.3% |

**The answer is no.** L5's single busiest bucket is **TTE 60–120 (30.1% of
dollars)** — which our envelope *does* cover for OPEN and EXTEND, since
`no_new_open_extend_tte_s` is 60. Our boundaries exclude **13.1%** of L5's
historical dollars (below TTE 60) and **2.9%** (below TTE 30). They do not cut
off L5's habitat.

**What they do cut off is Gemini's.** 38.8% of Gemini's notional is in TTE
30–60, where L5 historically did 10.2% and where we may only COMPLETE.
Gemini's TTE 30–180 carries **88.5%** of its notional against L5's **64.6%**.

The `>300` row needs a caveat I got wrong once: prints above TTE 300 precede
the contract's own `effectiveDate`, and they are **not** a reliable feature.
19 of 26 windows open with a burst at TTE ~377; the 7 captured 11:35–12:05Z
have no print before TTE 268.7. L5 never traded above TTE 300 because
Polymarket's 5-minute windows are exactly 300 s. And L5
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

Over 742 cycles:

| arm | distinct prints reached | quote-instances filled | mean outbid share of rest |
|---|---|---|---|
| **NATIVE** (reprices every ~8 s) | **5** | 2 | — |
| **IMPROVE_1_TICK** (reprices every ~8 s) | **10** | 7 | — |
| IMPROVE + 15 s minimum rest | 11 | 10 | 34.4% |
| IMPROVE + 30 s minimum rest | 15 | 18 | 40.1% |
| IMPROVE + 45 s minimum rest | 19 | 24 | 42.6% |
| **IMPROVE + 60 s minimum rest** | **22** | 34 | 44.0% |

**Native reaches 5 distinct prints across 26 windows. Improving by one tick
and simply not repricing for 60 seconds reaches 22 — 4.4×.** The gain is
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

Attributing all **$2,093.71** of executed notional across 26 windows to the
reason we did not interact with it:

| cause | prints | $ notional | share |
|---|---|---|---|
| **C — timing: TTE <60, where we cannot OPEN** | 19 | **839.54** | **40.1%** |
| **C — timing: TTE >280, before our band opens** | 60 | 122.71 | **5.9%** |
| **A — price: our quote would not be reached** | 33 | 258.72 | **12.4%** |
| **B — queue: consumed ahead of us** | 7 | 19.04 | **0.9%** |
| no evidence (our cycles do not cover it) | 24 | 548.11 | 26.2% |
| **reachable — we would have been there** | 11 | **305.60** | **14.6%** |

**(C) TIMING/TTE — 46.0%, and it is still the largest cause by a factor of
almost four.** Almost all of it is one region: TTE <60, where our envelope is
in COMPLETE_ONLY and then withdrawn.

**(A) PRICE — 12.4%.** Real, and larger than the first pass suggested (4.6%
at 19 windows). We are at the best *bid* 100% of the time, so this is not
"we quote behind the touch" — it is that the touch itself is often nowhere
near where the print happens.

**(B) QUEUE — 0.9% by notional**, though it is the difference between 5 and 10
distinct prints reached, because the prints it costs us are the small ones.
Queue position matters for print *count* and barely at all for *notional*.

**(D) GENUINELY ABSENT FLOW — real but not the largest cause.** 88.8% of
adjacent-cycle intervals carry no print, and only 3.1% of our 483 quoting
cycles saw any reaching print. But **$305.01 (19.8%) of notional was
reachable at our own price with our own queue position** — the flow was there
and we were not trading.

**Ranking: C ≫ A > D ≈ B by notional.** By *count* of prints reached the order
differs — queue position doubles the prints we touch — which is why both
measures are given.

One caution on (C): the reason the envelope leaves TTE <60 is that the
outcome is nearly determined there — that region contains the 714-contract
print at **0.79**, 42 seconds before a contract that settled YES. Being the
maker on the other side of that is exactly the adverse selection this
programme has been measuring. **The 40.1% is not free money and I am not
proposing to go and get it.**

## 7. How much these shares moved when the sample grew

The first pass ran on 19 windows; soak #4 took it to 26 and soak #5 to 30.

**The §6 table was derived by hand in prose and could not be reproduced.** It
is now computed by `LP.attribution()` and stored in
`analysis/gemini/large_prints.json`, pinned by 7 tests. At 30 windows, 168
prints, $2,449.83:

| cause | 19 win | 26 win | **30 win (computed)** |
|---|---|---|---|
| C — timing, TTE <60 | — | 40.1% | **40.4%** |
| C — timing, TTE >280 | — | 5.9% | **5.9%** |
| **C — timing (total)** | 58.6% | 46.0% | **46.3%** |
| A — price | 4.6% | 12.4% | **6.5%** |
| B — queue | 1.2% | 0.9% | **0.9%** |
| reachable | 19.8% | 14.6% | **39.9%** |
| no evidence | 15.8% | 26.2% | **6.5%** |
| 100+ share of notional | 77.7% | 78.7% | 78.7% |

**C has stabilised. Coverage, not the venue, moved everything else.** C held
at 46.0% → 46.3% and its two regions barely moved; B has been negligible
throughout. But **"no evidence" collapsed from 26.2% to 6.5%** because soak #5
added cycle coverage over windows that previously had no reconstructed book —
and almost all of that notional landed in **reachable**, which more than
doubled to 39.9%. A fell back to 6.5%, roughly where the 19-window pass had
it, which confirms the 12.4% reading was itself a coverage artefact.

**Read this as: C ≈ 46% is the one share worth relying on.** A, reachable and
no-evidence are still moving with how much of the tape our cycles cover, and
the honest summary is that **we now know we could have been there for 39.9% of
the notional and were not** — a larger claim than the first pass could make,
and one that only became visible when coverage improved.

The two figures that have never moved — the concentration of notional in nine
prints, and its concentration in TTE 30–180 — remain the ones worth relying on.
