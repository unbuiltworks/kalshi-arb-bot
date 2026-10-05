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

---

# SPRINT SYNTHESIS — all six lanes in

## The keystone: our behavioral baseline was built on 2% of the data, and L5's recent edge looks like a rebate

Lane 5 found a defect in the baseline itself, which I verified
independently: `data/l5/raw/activity_gap.jsonl.gz` holds **706,500
payload records, of which ZERO carry a `name` field** (verified: record
keys are conditionId/eventSlug/outcome/price/side/size/timestamp/
transactionHash/type/usdcSize — no `name`, no `proxyWallet`), all fetched
with `params.user` = L5's address. The prescribed filter
`x['name']=='l5Zn1bWoM8eTsK'` discards all of them.

Corpus after switching identity from name to **provenance**: 312 events /
9,901 fills → **17,855 events / 754,234 fills, 2026-03-19 → 2026-09-25**.

**Every L5 constant I have reported this session is a Jul–Aug 2026
snapshot, not L5's policy.** The mechanics hold in kind; the parameters
move a lot:

| period | events | 1st-acq med | % favored | % complemented | EXTENDs med | fills/event |
|---|---|---|---|---|---|---|
| 2026-03 | 88 | 0.495 | 45.5% | 83.0% | 1 | 21 |
| 2026-05 | 877 | 0.50 | 44.9% | **99.2%** | 2 | — |
| 2026-07 | 3,666 | 0.54 | 61.3% | 70.3% | 1 | — |
| 2026-08 | 10,794 | 0.55 | 62.6% | 73.0% | 1 | 6 |
| **2026-09** | 851 | **0.59** | **66.9%** | **43.5%** | **0** | **2** |

HOLDS: incremental same-side accumulation (EXTEND first-class in every
period), patient complementing, favored-side bias (strengthening
monotonically 42% → 66.9%), and **frequency-before-size** — events/month
rose 88 → 10,794 while median size stayed in a 10–20 band, with
**concurrency capped at ≤4 throughout**. That is direct external support
for `PATIENCE → FREQUENCY → SIZE → CONCURRENCY`.

CHANGED: completion collapsed 99% → 73% → **43.5%**; median EXTENDs → 0
(bimodal, not shifted); entry later (T+104s).

**AND THE LIKELY CAUSE: L5's maker rebates jumped ~12× in August**
(median `usdcSize` per rebate record 98 → 1,161) with `REWARD` records
appearing for the first time. The behavioral regime change coincides
exactly with the arrival of large incentive cashflows. **L5 is paid to
rest liquidity, so an uncompleted resting fill is not a loss to it. We are
not paid that.** This is the single strongest reason the recent L5
behaviour must not be cloned — and it is exactly what `CLAUDE.md` already
anticipated: *"Do not unlock mature incentive-era behavior merely because
L5 used it later."*

## Where Lane 3 and Lane 5 meet — the decisive number

Lane 3 measured **unrebated** maker liquidity supply on Gemini BTC5m at
**−$0.2380/contract, CI [−$0.2831, −$0.1935]**, with **zero non-toxic
states** out of every bin of every pre-fill feature.

So a maker rebate must exceed **$0.238/contract** merely to break even,
and **$0.283** to clear the pessimistic bound. For the $480/day target:

| size | net needed | implied rebate needed |
|---|---|---|
| q25 | +$0.0667 | **≥ $0.3047** |
| q50 | +$0.0333 | ≥ $0.2713 |
| q100 | +$0.0167 | ≥ $0.2547 |

**That is the whole program in one line: the L5 strategy in its profitable
regime appears to be a rebate-harvesting strategy, and the rebate is the
component we have never sourced for our own account.**

## Other newly discovered L5 mechanics

1. **L5 is buy-only** — 2 SELL fills in 754,234, both in March, none since.
   Inventory is never unwound by selling, only complemented or redeemed.
   The "side-switch" framing in our model has **no support in the tape**.
   This independently vindicates Lane 2 modelling zero SELLs, and it
   sharpens the blocking limitation: the obvious fix for our residual loss
   is an action L5 itself never takes, because it is paid to wait instead.
2. A hard **06:00–19:00 UTC session in Apr–Jun dissolving into flat 24h
   operation by August** — human supervision becoming automated.
3. **Descending size ladder within each event** (Sep medians by ordinal
   37.0 → 28.2 → 28.2 → 20.0 → 19.5 → 13.3). A single per-fill size number
   hides this.
4. Round-lot quantization plus ~10,691 Aug fills at size <0.05 — dust
   partials consistent with resting maker orders being nibbled.
5. **Category scope: crypto up/down only.** BTC from March, ETH added in
   August, 5m overtook 15m in July. Across 17,855 events there is **not
   one** sports, esports, politics, economics or weather market. This does
   not license narrowing our engine — the market-agnostic requirement
   stands — but the behavioural evidence base is crypto-up/down only, so
   transfer to any other category is extrapolation.
6. Maker/taker is **not resolvable** from this corpus; a cross-reference
   gives a **16.9% taker-share lower bound only**, reported as a floor.

## Consequence for Lane 2 that must be recorded

Lane 2 derived its action-family triggers from the L5 corpus **using the
defective `name` filter**, so its band `[0.20, 0.82]` and compress trigger
`0.991` describe the **Jul–Aug regime**, not L5's policy. Its structural
conclusion — matched book ≈ zero, entire loss is unhedged directional
residual, loss linear in unhedged favored shares — does not depend on
those constants and stands. **Its parameters need re-deriving on the
754,234-fill corpus before any replay number from it is quoted again.**

## Final scoreboard

| hypothesis | evidence | ind. windows | causal? | executable? | net EV | capacity | $/day | status | next gate |
|---|---|---|---|---|---|---|---|---|---|
| Gemini underdog + complement | measured | 61 | yes | yes | −$0.0285/win | q1=q25 reach | −$8.21 | **KILLED** | — |
| Gemini favored first leg | measured | 61 | yes | yes | −$0.3108/win | q1–q25 | −$89.51 | **KILLED** | — |
| Gemini first-leg state selection | measured | 10 disc | yes | yes | negative everywhere | — | — | **KILLED** | — |
| Gemini L5 inventory replay | measured | 62 | yes | modelled | −$0.437/win CI excl. 0 | — | −$125.86 | **KILLED** | params need re-derivation |
| └ matched book alone | measured | 62 | yes | modelled | +$0.006/win CI spans 0 | q25 61.7% | $43 @q25 | **ceiling ≈ zero** | 11.1× short |
| Gemini maker supply (unrebated) | measured | 46 | yes | 42% fill | −$0.2380/fill | 10,698 fills | negative | **KILLED** | zero non-toxic states |
| **Kalshi 15M structural fit** | measured | 72 tickers | — | 91.9% @q5 | UNMEASURED | pair 1.02 | UNMEASURED | **best candidate** | **adverse selection; rebate ≥$0.238?** |
| Polymarket sports | measured | 71 events | — | — | UNMEASURED | pair 1.01 | UNMEASURED | gated | confirm 1000bps fee is/isn't effective |
| PMUS sports | **artifact** | 2 markets/12min | — | ask_qty null 232/232 | — | — | — | **gated out** | needs a real census |
| External→book lead (Phase A) | collecting | 0 | — | — | UNMEASURED | — | — | **dispatched** | ≥2 admissible venues |
| L5 rebate-harvesting hypothesis | **new** | 17,855 events | plausible | — | UNMEASURED | — | — | **highest value** | source our own rebate terms |

## Recommendation

**Gemini BTC5m is finished as a deployment candidate.** It failed on four
independent mechanisms — prediction, inventory management, liquidity
supply, and capacity arithmetic — and the capacity ceiling alone (11.1×
short under a *perfect* hedge) is independent of prediction quality.

**The fastest defensible route is now Kalshi 15-minute series**, for one
reason that is not about Kalshi's spread: it is the **only** environment
where **maker-reward program terms are actually sourced** (183,329
programs scanned; 83.1% of leg-tape rows under an active program), and the
rebate is the component Lane 5 identifies as the likely source of L5's
profitable-regime economics. Kalshi also matches L5's 900s window exactly
and prices pairs 22¢ tighter than Gemini.

Two things must be measured before any of that is trusted, and neither is
a strategy experiment:
1. **Our own rebate terms** — does any available program credit
   ≥$0.238/contract? Sourced and credited, never assumed. This single
   number decides whether the maker lane is dead or the main event.
2. **Adverse selection, which is UNAVAILABLE for every environment** — no
   corpus aligns own fills with book state and subsequent mark. It is the
   dimension most likely to reverse Kalshi's apparent edge.

Kalshi's honest blocker: **prints are anonymous**, so per-actor
OPEN/EXTEND/COMPLETE sequences and complement lag are UNAVAILABLE, and its
leg tape is **83 minutes**. It is demonstrated *structurally permissive*,
not *behaviourally L5-like*.

---

## CORRECTION — I misdiagnosed the remedy for Lanes 2 and 4

I wrote that Lane 2 "derived its action-family triggers from the L5 corpus
**using the defective `name` filter**". **That is wrong.** Verified:

* `data/l5/raw/trades.jsonl` — 10,500 payload records, **10,500 match
  `name == 'l5Zn1bWoM8eTsK'` (100.0%)**. The `/trades` feed *does* send the
  profile block, so the filter works perfectly there.
* The defect bites **only** on `data/l5/raw/activity_gap.jsonl.gz` (the
  `/activity` feed, 706,500 records, zero with a `name`), and **neither
  Lane 2 nor Lane 4 reads that file.**

So the diagnosis changes, and the real finding is sharper and worse:

> **`trades.jsonl` spans 2026-08-30 07:49 → 20:52 UTC — 13.1 hours of a
> single day.**

Lane 2's band `[0.20, 0.82]`, its compress trigger `0.991`, and Lane 4's
entire L5 reference row (the 100.0-similarity anchor the whole venue
ranking is scaled against) are derived from **one 13-hour session**, not
from the "Jul–Aug regime" and not from 754,234 fills.

**The remedy is therefore not "fix the filter" — it is "point the
derivation at the 754,234-fill provenance-identified corpus."** Lane 5's
own audit is the only analysis in this sprint built on that corpus.

This strengthens rather than weakens the sprint's conclusions, because the
L5 *reference* was the optimistic side of every comparison: Lane 4 ranked
Gemini 28.4 against an L5 anchor measured on a single good day. It also
means **no L5-derived parameter in this repository should be quoted until
re-derived**, and that the one-day provenance must be stated wherever the
312-event / 9,901-fill figures appear.

---

## RETRACTION — my Kalshi recommendation rested on an artifact

Two turns ago I recommended Kalshi 15-minute series as "the fastest
defensible route", and gave as the central justification that it is "the
**only** environment where maker-reward program terms are actually
sourced (183,329 programs scanned; **83.1%** of leg-tape rows under an
active program)".

**The 83.1% is arithmetically correct and substantively misleading, and I
verified the decomposition myself:**

| series | leg-tape rows | under a reward program |
|---|---|---|
| KXCRYPTOLEAD15M | 935 | 935 |
| KXSILVER15M | 197 | 197 |
| KXWTI15M | 196 | 196 |
| KXGOLD15M | 195 | 195 |
| KXCOPPER15M | 185 | 185 |
| KXNATGAS15M | 178 | 178 |
| **KXBTC15M** | **191** | **0** |
| **KXETH15M** | **192** | **0** |

**Every one of the 1,886 program rows is in a series with no fill
evidence, and 0 of the 383 BTC/ETH rows — the only series where flow is
measurable — is under any program.** The reward programs exist precisely
where there is no flow and are absent precisely where flow exists. The
83.1% was an artifact of which tickers the collector happened to poll.

### The gate verdict, split

**Class A — liquid (BTC/ETH/SOL/XRP/DOGE 15M; 40,791 deduped prints, 51
windows, ~6.9h): expected compensation $0.0000, maximum $0.0000,
confirmed credited $0.00.** Against the $0.2380 break-even that is a
deficit of **≥$0.2380/contract**, and it is robust to every unsourced
parameter, because zero compensation minus a non-negative fee is at most
zero. **Incentive-replication is KILLED at Kalshi wherever fills are
measurable.**

**Class B — subsidised (CRYPTOLEAD + commodity 15Ms; fill evidence NONE):**
sourced pool **$20.00 per 15-minute period** = $1,920/day/market, which is
*not* below the target, so **not killed on arithmetic**. But eligibility
mechanics and the pool-scoring rule are **UNSOURCED** — and unknowable
from this container. **$1,920/day of advertised pool in a market with
$177/window of flow is a term, not a business.**

**`CONFIRMED_CREDITED` is $0.00 for every Kalshi program. No credit has
ever been observed.**

### Fees are not the constraint

Source-confirmed shape `quadratic`, multiplier 1, `tapered_deci_cent`;
coefficient UNSOURCED. Sensitivity at c = 0.035/0.07/0.10 gives
$0.0088/$0.0175/$0.0250 per contract at P=0.50 — **0–10.5% of the
hurdle**. The missing coefficient cannot flip the verdict either way.

### And Kalshi has the same pair-cost floor as Gemini

Verified independently: **`pair_cost < 1.00` in 0 of 2,269 snapshots**
(min 1.0010, median 1.0200), and **zero sub-$1 snapshots in every one of
the eight series**. So the `≤0.96` spring target is never satisfiable on
this sample either. Kalshi is tighter than Gemini (median 1.02 vs 1.24)
but **not qualitatively different** — the simultaneous pair never pays.

### Two caveats that cut against my earlier framing

1. **The −$0.2380 hurdle is a GEMINI BTC5m measurement, not a Kalshi
   one.** Transferring it assumes comparable maker toxicity, which is
   **unmeasured** on Kalshi. A Kalshi-native adverse-selection
   measurement is therefore blocking, not optional.
2. The leg tape is **2,269 snapshots over 83.3 minutes of one day**, with
   **no fills, no maker P&L and no credited rewards**. Program presence on
   one 83-minute slice is also not evidence of persistence.

### Corrected status

**Kalshi is NOT the demonstrated best candidate.** It is an environment
whose liquid markets carry **no** incentive and whose incentivised markets
carry **no flow**, on sourced-but-stale terms, with its maker toxicity
unmeasured. The single observation that would reopen it is **whether a
liquidity program ever appears over a liquid series** — and the existing
collector already logs `KALSHI_INCENTIVE_STATE`, so that needs no new
code.
