# GEMINI_COMPLETION_MODEL_B — recycling, not fill count

**The problem is redefined.** Not "how do we get filled more" but "does an
initial fill become matched inventory without unacceptable residual". Every
number below is denominated in **dollars** as well as units, because 0.01
contracts of leakage and 0.01 contracts of matched inventory are not the same
object at different prices.

**Sample:** 18 initial fills (4 ACTUAL venue fills, 14 HYPOTHETICAL from the
large-print forensic) over 13 BTC5m contracts, replayed to settlement under
**31 arms**. Model A is frozen and untouched; an arm changes only a COMPLETE
price, a quote's persistence, whether an EXTEND is acted on, or whether a
COMPLETE-only quote holds late. **Nothing was sent. Every fill is
SHADOW/THEORETICAL.**

Code: `completion.py` (36 tests), `bursts.py`, `hazard_report.py`,
`l5_postfill_crossvenue.py` (12 tests). Reproduce:
`python3 -m src.gemini_execution.completion_report`,
`... .burst_report`, `... .hazard_report`.

---

## 0. The three results that matter

1. **The EXTEND veto is a free Pareto win on this sample.** It suppresses all
   7 EXTEND fills, avoids **$0.0331 of residual (25.7%)**, and gives up
   **$0.0000 of matched inventory** — every veto arm dominates its own twin.
2. **Queue preservation does nothing.** The priority experiment's premise
   holds (a complement was already resting in 15 of 18 cases) and preserving
   it changes **nothing**: 0 pairs, 0 complement fills, at every duration.
3. **The cross-venue difference is activity, and it is a gradient inside L5's
   own book.** 122 fills/window → 96.0% complement; 6 → 83.1%; 2 → 42.1%;
   ours → 0%. L5's own recycling degrades exactly the way ours does when its
   market thins.

---

## 1. The decision criterion, and a trap in it

Ranked by **matched dollars / gross dollars filled**, residual and pair
economics beside it as instructed:

| arm | M/G at par | M/G raw | pairs | matched $ | residual $ | comp | ext | basis |
|---|---|---|---|---|---|---|---|---|
| **IMPROVE_1+REPRICE+EXTEND_VETO** | **0.0807** | 0.0807 | 1/18 | 0.0084 | **0.0957** | 1 | **0** | **0.84** |
| NATIVE+REPRICE+LATE_60_30 | 0.0723 | 0.0723 | 1/18 | 0.0100 | 0.1284 | 0 | 7 | 1.00 |
| IMPROVE_2+STICKY_15 | 0.0640 | 0.0640 | 1/18 | 0.0088 | 0.1288 | 1 | 7 | 0.88 |
| IMPROVE_1+STICKY_15 | 0.0633 | 0.0633 | 1/18 | 0.0087 | 0.1288 | 1 | 7 | 0.87 |
| NATIVE+REPRICE (baseline) | 0.0000 | 0.0000 | **0/18** | 0 | 0.1315 | 0 | 7 | — |

**The trap: matched/gross is gameable by overpaying.** `BEST_PERMISSIBLE_MAKER`
posts the highest *raw* ratio in the study (0.0885) purely because it pays
**1.25** for the same single pair that `IMPROVE_1` buys at **0.84**. More
dollars land in the "matched" column and the pair is worse. So the ranking is
run on the **par-constrained** ratio — matched dollars from pairs at or below
$1.00 — with the raw ratio shown beside it. Every arm whose basis exceeds par
scores 0.0000 at par, which is the correct answer: a pair bought above $1.00
is a matched loss.

## 2. The 4 × 5 COMPLETE execution matrix

OPEN and EXTEND stayed Model-A native in all 20 cells. **Every cell produced
exactly one matched pair out of 18 seeds.** The entire spread between cells is
what was paid for that one pair:

| price policy | REPRICE | STICKY 15 | STICKY 30 | STICKY 45 | STICKY 60 |
|---|---|---|---|---|---|
| NATIVE | — | 0.86 | 1.01 | 1.01 | 1.01 |
| IMPROVE_1 | 0.84 | 0.87 | 1.02 | 1.02 | 1.02 |
| IMPROVE_2 | 0.85 | 0.88 | 1.03 | 1.03 | 1.03 |
| BEST_PERMISSIBLE_MAKER | 1.16 | 1.16 | 1.25 | 1.25 | 1.25 |

*(cells are the median effective pair basis; — is no pair)*

**Stickiness beyond 15s crosses par in every column.** On a 5-minute contract
a held quote is not patient, it is stale: the market moved 19 cents inside the
30s window and the same 263-contract seller took our stale 0.75 bid that
native would have priced at 0.56. **Sticky-30/45/60 paid 18 cents more for the
identical fill.**

**No arm ever crossed.** 74 of 74 improvements were provably maker-only; the
price rule refuses rather than guessing when the ask is unreadable.

**Reachability is unchanged by price.** All 77 native complement quotes already
sat **at the touch**; improving moved reach from 0/77 to 1/74. Price was not
the obstacle.

## 3. Preserving the resting complement — the priority experiment

**The premise holds.** The frozen policy quotes both legs on flat inventory,
so a complement was already live when the fill landed in **15 of 18 seeds**
(the other 3 had no cycle recorded before the fill). Preservation did engage:
9 of 77 complement quotes were the preserved one at 15s, rising to **50 of 77
at 60s**.

**The result is nil.** Every duration:

| arm | pairs | complement fills | matched $ | residual $ |
|---|---|---|---|---|
| cancel/reprice (baseline) | 0/18 | 0 | 0 | 0.1315 |
| preserve 15s | 0/18 | 0 | 0 | 0.1315 |
| preserve 30s | 0/18 | 0 | 0 | 0.1315 |
| preserve 45s | 0/18 | 0 | 0 | 0.1315 |
| preserve 60s | 0/18 | 0 | 0 | 0.1315 |

**Queue preservation on the complementary leg does not materially increase
matched inventory here.** Not because the quote was not preserved — it was,
for up to 50 of 77 quotes — but because no external print reached it either
way. Keeping your place in a queue is worthless when nothing arrives to work
through the queue.

*(The aged-queue model is stated as a model: a preserved quote has intervening
reaching volume decremented from the queue in front of it, a requote joins the
back of its level. Neither is observable from a snapshot ladder.)*

## 4. The EXTEND veto — the one unambiguous win

Model A kept requesting EXTEND throughout; Model B declined to act and logged
what it declined. **Each veto arm is compared against its own non-veto twin**,
so the only difference on either axis is the veto.

| arm | matched retained | matched $ given up | residual $ avoided | avoided % | EXTENDs suppressed | dominates twin |
|---|---|---|---|---|---|---|
| NATIVE+REPRICE | — (twin had none) | **0** | **0.0331** | 25.2% | 7 | **yes** |
| IMPROVE_1+REPRICE | **100%** | **0.0000** | **0.0331** | 25.7% | 7 | **yes** |
| BEST_MAKER+REPRICE | **100%** | **0.0000** | **0.0331** | 25.7% | 7 | **yes** |
| BEST_MAKER+STICKY_30 | **100%** | **0.0000** | **0.0331** | 25.7% | 7 | **yes** |

**The Pareto curve is degenerate in the best possible way: there is no
trade-off.** All four points sit at 100% matched retained and ~25.7% residual
avoided. On this sample the EXTEND fills contributed **nothing** to matched
inventory — they were pure leakage, and removing them cost nothing.

**No threshold is proposed.** This is the unconditional veto measured
observationally; the conditional version ("veto when complement reachability
is poor") needs a reachability signal, and §5 says that signal does not yet
have the sample to exist.

**The caveat that matters:** 7 EXTEND fills across 18 seeds is a small base,
and a veto that looks free here could cost matched inventory in a regime where
EXTENDs do eventually complete — which is precisely what L5 does on Polymarket
(§7), where extend and complement fills run at a **1.06 ratio**. This result
says the veto is free *on Gemini as observed*, not that same-side accumulation
is worthless in general.

## 5. The completion-hazard dataset — and why no threshold follows

**2,315 residual-state rows across 6 symbols**, every feature asked for: TTE,
held side/qty/average price, desired COMPLETE price and its distance from the
touch, queue ahead, opposite-side prints over 15/30/60/120s, aggressor
imbalance, seconds since opposite flow, price velocity, spread, rest duration,
and the eventual outcome columns.

**The sample cannot support a split, and the first count said otherwise.**
Pooled across arms the positive cell looked like 72 states. But every arm
replays the *same* moments — the pseudo-replication factor is **24×**. De-duplicated
to independent (symbol, moment, seed) units:

| | independent states | rows before de-duplication |
|---|---|---|
| complement eventually reached | **3** | 72 |
| never reached | 44 | 2,243 |

**Three positive cases.** The floor for a split is 30 per cell.
`sufficient_for_a_split: false`. No threshold is proposed, no model is fitted,
and nothing in the feature medians should be read as a signal.

## 6. Large-flow burst precursors

**Updated at 11 bursts / 154 controls (2026-09-19 14:2xZ).** The first pass ran
at 9 bursts and two of its "separated" features did not survive two more.

| feature | burst median | control median | coverage | separates at n=11 |
|---|---|---|---|---|
| **TTE (s)** | **90.7** | **267.0** | **11/11** | **yes** |
| contracts in prior 120s | 20 | 4 | 11/11 | yes |
| book imbalance (top/total) | 0.919 | 0.946 | 10/11 | yes |
| **depth depletion (120s)** | 0.341 | 0.000 | 10/11 | **no — was yes at n=9** |
| **max print in prior 60s** | 263 | 10 | 7/11 | **no — was yes at n=9** |
| levels disappeared | 3 | 2 | 10/11 | no |
| spread | 0.31 | 0.30 | 10/11 | no |
| touch depth | 227.0 | 234.0 | 10/11 | no |

**Depth depletion was the second-strongest precursor at 9 bursts (0.655 vs
0.000) and stopped separating at 11.** So did the max-print features. That is
the instability the n=9 verdict warned about, arriving on schedule — and it is
the reason the verdict refused to call anything predictable.

**Only TTE survives**, and it survives cleanly: bursts land at a median TTE of
**90.7s** against **267.0s** for matched controls, at full coverage.

> **Verdict unchanged: not predictable enough to rest liquidity in front of.**
> Eleven bursts cannot establish predictability, and two of the four apparent
> precursors have already evaporated. The TTE finding converges with §8 from an
> independent direction: the action on this venue is late.

## 7. Cross-venue — what L5 does that we do not

**The observability is not symmetric and every row says so.** Polymarket shows
L5's own fills and never its book, quotes, queue or cancels; Gemini shows the
book and the whole tape but our fills are hypothetical. The comparison is
restricted to what both genuinely expose.

| candidate | L5 (Polymarket BTC5m) | us (Gemini BTC5m) | separable |
|---|---|---|---|
| complement reached at all | **83.1%** (795/957) | **0%** (0/18 native) | yes |
| time to first complement | **66 s** | never | yes |
| EXTEND per COMPLETE | **1.06** | **∞** (7, 0) | yes |
| TTE at the first fill | **243 s** | **127 s** | yes |
| matched $ / gross $ | **0.6363** | **0.0000** | yes |
| effective pair basis | **0.958** | — | yes |
| TTE at the complement | 153 s | — | no — no complement ever filled |
| fills per window | 6 | — | no — our fills are hypothetical and envelope-capped |
| quote persistence / queue | — | 0.0 reach | no — Polymarket never exposes L5's resting quotes |
| flow direction after the fill | — | — | no — the L5 extract has no counterparty tape |

**And the control that settles it.** The same trader, the same venue, three
different market slices:

| slice | windows | fills/window | inter-fill | **complement rate** | ext:comp | matched $/gross $ | basis |
|---|---|---|---|---|---|---|---|
| L5 BTC **15m** | 527 | 122 | 2.0 s | **96.0%** | 1.01 | 0.9368 | 0.962 |
| L5 BTC **5m** | 957 | 6 | 15.0 s | **83.1%** | 1.06 | 0.6363 | 0.958 |
| L5 ETH **5m** | 703 | 2 | 13.0 s | **42.1%** | **1.40** | 0.3660 | **1.007** |
| **us, Gemini BTC 5m** | 18 seeds | — | — | **0%** | **∞** | 0.0000 | — |

**L5's own recycling degrades exactly the way ours does when its market
thins.** On ETH 5m the complement rate falls to 42%, the extend:complement
ratio rises to 1.40, and the pair basis crosses par to 1.007 — the same
symptoms we have, one notch less severe. Gemini BTC5m is the continuation of
that gradient, not a different phenomenon.

So the answer to "what distinguishes L5's fill→complement from our
fill→EXTEND-only leakage" is **two measurable things, neither exotic**:

1. **Activity.** Complement rate tracks fill density monotonically across
   three independent slices. Our venue sits below the thinnest of them.
2. **Runway.** L5's first fill lands at median **TTE 243** and its complement
   at **TTE 153** — 90 seconds later, with 153 seconds still to spare. Our
   first fills land at median **TTE 127**, and our recorded coverage usually
   ends between TTE 81 and 36. L5 is not completing faster than us; it is
   starting far earlier in the window.

Quote persistence and queue behaviour **cannot** be separated on this evidence
— Polymarket does not expose them — and are not claimed either way.

## 8. TTE 60→30, COMPLETE-only

**This is not a new bound.** The frozen policy is *already* COMPLETE_ONLY
below TTE 60 and WITHDRAWN below TTE 30; the test pins
`LATE_PHASE_HI_S == NO_NEW_OPEN_EXTEND_TTE_S` and
`LATE_PHASE_LO_S == WITHDRAW_ALL_TTE_S`. What the phase lacks is
**observation** — our recorded cycles usually stop before TTE 60. **No live
bound was moved.**

The phase ran on **11 of 18 seeds** with residual to recycle. Across them the
TTE 60–30 band held **$573.16** of notional — and essentially all of it sat in
**one contract**:

| seed | band prints | band notional | reached us | recycled |
|---|---|---|---|---|
| 0140-UP | 2 | **$568.46** | 2 | **100% of residual** |
| 1205-UP | 1 | $4.70 | 0 | 0% |
| the other 9 | 0 | $0 | 0 | 0% |

**One of eleven.** It recycled its entire residual at a pair basis of **1.00** —
at par, not below it. The late phase raises matched pairs from 1/18 to 2/18 in
the IMPROVE arms, which is real and is also two observations.

Two caveats are carried in the JSON rather than buried: no ladder is recorded
that late, so the queue is **unknown and not assumed to be zero** — the fill is
an **upper bound**; and where no book exists at or before TTE 60 the quote is
priced from inside the band, which is information a quote placed at TTE 60
could not have had. *(The first run reported a staleness of −25.1s, which is
not a thing; the pricing cycle is now chosen at or before the band and the
defect is pinned by a test.)*

## 9. What this licenses

**Licensed as findings:** the EXTEND veto is a Pareto improvement on observed
Gemini data; queue preservation is not; stickiness beyond 15s is harmful;
matched/gross must be read at par; the cross-venue difference is activity and
runway; bursts land late and after depth depletion.

**Not licensed:** any threshold, any selector, any admission rule, any live
change. The hazard dataset has **3 independent positive cases**; the burst
study has **9 bursts**; the whole completion matrix rests on **one matched
pair**. Pre-entry persistence stands at **12 of the 20-pair floor** (the three
soaks that ran before the wiring landed carry no snapshots) and collection
continues.

**Nothing live. No new authorization. Model A, the TTE bounds and the live
envelope are unchanged.**

---

**Accounting labels.** Everything above is **SHADOW / THEORETICAL**. No order
existed, nothing was submitted, filled, settled or realized. "Matched
inventory", "pair basis", "residual" and "capital at risk" are reconstructions
of hypothetical inventory paths, not positions and not a cost basis. Matched
and residual are never summed. No rebate is assumed anywhere. L5's Polymarket
P&L is not reconstructed and not claimed — only the sequence, timing, side,
price and TTE of its own fills.
