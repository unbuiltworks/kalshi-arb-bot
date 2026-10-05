# L5 PROFITABILITY SPRINT — SCOREBOARD

No live orders. No OOS unseal. Long recorder tape, frozen studies and the
six sealed OOS windows preserved; the sealed six are excluded **by name**
from every lane.

## The capacity arithmetic that frames everything (Lane 6)

Target $480/day = $5/window across 288 BTC5m windows/day. Required **net
EV per filled contract**:

| scenario | fill | q1 | q5 | q10 | q25 | q50 | q100 |
|---|---|---|---|---|---|---|---|
| every window (288/d) | 100% | $1.67 | $0.33 | $0.17 | $0.067 | $0.033 | $0.017 |
| every window | 50% | $3.33 | $0.67 | $0.33 | $0.133 | $0.067 | $0.033 |
| underdog reach (132/d) | 100% | $3.63 | $0.73 | $0.36 | $0.145 | $0.073 | $0.036 |

**A contract pays at most $1**, and the measured gain per completed lock is
+$0.125 to +$0.313. So **$480/day is arithmetically impossible at q1 and
q5** — the requirement exceeds the payout bound — and first becomes
reachable around q10–q25. Probe-size economics were never capable of this
number at any edge quality.

## The decisive Gemini result: even a PERFECT hedge falls 11x short

Lane 2 measured the matched book and the directional residual separately
over 62 independent windows. Matched-inventory economics are
**+$0.006/window, CI [−$0.023, +$0.030]** — approximately zero. Scaling
*only* that component, i.e. assuming a perfect hedge that never carries
residual:

| size | $/day | 95% CI |
|---|---|---|
| q1 | $1.73 | [−7, 9] |
| q10 | $17.28 | [−66, 86] |
| q25 | **$43.20** | [−166, 216] |
| q100 | $172.80 | [−662, 864] |

Matched-book-only at q25 needs EV/window ≥ $0.0667 against $0.006
measured: an **11.1x shortfall whose CI includes zero**. $480/day is not
reached at any size the book supports — both sides are executable at q25
only 61.7% of the time. **This is an arithmetic ceiling on Gemini BTC5m
that is independent of prediction quality.**

## Scoreboard

| hypothesis | evidence | ind. windows | causal? | executable? | net EV | capacity | $/day | status | next gate |
|---|---|---|---|---|---|---|---|---|---|
| underdog + patient complement | measured | 61 | yes | yes | −$0.0285/win | q1=q25 reach | −$8.21 | **KILLED** | hurdle 50.4% vs 45.9% reach |
| favored first leg | measured | 61 | yes | yes | −$0.3108/win | q1–q25 | −$89.51 | **KILLED** | hurdle 85.9% vs 50.8% |
| first-leg state selection | measured | 10 disc | yes | yes | negative in every population | — | — | **KILLED** | margin sign flips with weighting |
| L5 dynamic inventory replay | measured | **62** | yes | modelled | **−$0.437/win** CI [−0.631,−0.245] | — | **−$125.86** | **KILLED** | CI excludes zero |
| └ spring 0.96 arm | measured | 62 | yes | modelled | −$0.387/win | — | −$111.46 | KILLED | — |
| └ NO_EXTEND ablation | measured | 62 | yes | modelled | −$0.225/win | — | −$64.80 | KILLED | loss linear in unhedged shares |
| └ matched book alone | measured | 62 | yes | modelled | +$0.006/win CI [−0.023,+0.030] | q25 61.7% | $43/day @q25 | **ceiling, ~zero** | 11.1x short, CI spans zero |
| external→book lead (Phase A) | collecting | 0 | — | — | UNMEASURED | — | — | **dispatched** | ≥2 admissible venues |
| maker toxicity separable | running | — | — | — | UNMEASURED | — | — | running | kill-or-keep |
| venue/category selector | running | — | — | — | UNMEASURED | — | — | running | ranked table |
| L5 fresh evidence | running | — | — | — | n/a | — | — | running | pull succeeded? |

## What Lane 2 established about WHERE the money goes

The matched book is ~zero; **the entire loss is the directional residual**
(−$0.443/window). The residual is the favored side, carried at median
basis **$0.75**, which settled favourably **8/29 = 27.6%**. Terminal
states: 29 MATCHED (+$1.44 total), 29 holding RESIDUAL (−$28.52), 4 never
acted. Fees were $2.28 — 8% of the deficit, not the story.

**CAUTION on that 27.6%**: it is a settlement base rate over 29 windows,
not a measured edge — structurally the same artifact as the 7/9 UP
observation that made the early-window analyzer look positive. If it were
real signal the underdog would win 72.4%, which the first-leg study found
no evidence for. Lane 2 labelled it descriptive and declined to invert the
side choice on it, which is correct: doing so would fit a side to these
outcomes.

**The tolerance test mattered.** 58/58 acted windows had pair cost above
$1 after leg 1 and **zero below** — a policy requiring `pair_cost < 1`
after leg 1 would have taken no action at all on this tape, so allowing
temporary pair cost >$1 was load-bearing, not cosmetic. Of those 58: 21
later compressed below $1 (final 0.875–0.99), 13 matched but stayed ≥$1,
24 never matched. L5's own final matched pair cost is median 0.977 /
p90 1.256 — it ends above $1 routinely too.

## The blocking limitation, stated plainly

The one obvious fix for the binding loss is **selling the unhedged favored
side before settlement**, and that is exactly the action we cannot price:
the sell-side fee is UNRECONCILED (we have never sold on this venue), and
L5's own corpus is 9,901 fills of which **all 9,901 are BUY**, so the
behavioural corpus cannot justify it either. No result above depends on a
sell. Establishing sell-side economics must not be done by submitting an
order to discover semantics.

## Hard-kill compliance

Nothing in this sprint created a D V3, another fixed underdog/favorite
rule, a martingale, simultaneous two-sided taker buying, or another
threshold sweep of the failed maker policy. Increasing size, frequency or
concurrency on any arm above would multiply a loss whose CI excludes zero.

---

## CORRECTION — the Gemini spread, measured three ways

I reported "median spread $0.24" and separately called the pair proxy
"algebraically the true UP bid-ask". The algebra is right (synthesized
DOWN ask = 1 − UP bid, so pair − 1 = UP ask − UP bid) but the **population
was not stated**, and that matters:

| measurement | population | n | median | mean |
|---|---|---|---|---|
| q1 pair cost − 1 | 61 **complete** windows (strike + settlement) | 24,520 | **0.24** | 0.275 |
| top-of-book UP ask − bid | **all** non-excluded windows with a book | 47,922 | **0.14** | 0.228 |
| single-leg quoted spread | **Gemini BTC15M, Aug 2026 historical** | 23,318 | 0.02 | 0.036 |

All three are correct on their own population; none is "the" Gemini
spread. **The "extraordinary ~29¢ spread" framing was overstated**: on
direct top-of-book measurement the median is **$0.14**, and **18.2% of
observations sit at a 1–2¢ spread** while 41.4% are at or above 20¢. The
book is bimodal, not uniformly wide. Top-of-book size is median 25 on
both sides.

This does **not** change Lane 3's verdict, which measured adverse
selection directly on 10,698 evidenced fills rather than inferring it from
spread width. It does mean the maker opportunity was never as large as the
headline implied.

A second correction, applied to `VENUE_SCORECARD.md`: it claimed in one
place that `policy_d_port_ext` independently reproduces Gemini's
pair-cost floor and in another that it cannot. The latter is right — that
corpus carries the **UP leg only, zero DOWN-leg rows** — so the claim of
independent reproduction is removed. **Gemini's pair-cost floor rests on a
single census and is not independently confirmed.** That is a caveat
against the Gemini conclusion, not for it.
