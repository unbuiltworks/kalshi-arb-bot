# P3 — The participation gap is a VENUE LIQUIDITY limit, not a policy or surface limit

**Status: ANALYSIS / REPLAY ONLY. Nothing armed.**

**Headline.** The hypothesis was that L5's ≥$0.65 fills come from denser
two-sided participation that our one-rung surface cannot reproduce. The
participation gap is real and larger than expected. **But it is not caused by
our surface, our action budget, our position caps, our retention rule or our
rung count — every one of those was varied and none of them binds.** It is
caused by the venue:

```
per BTC15m window          Gemini, ENTIRE venue tape      Polymarket, L5 ALONE
trades printed                                    7                       122
contracts                                       243                     2,307
notional                                       $162                    $1,087
```

**During COHORT-2 we were 33-100% of the entire Gemini BTC15m tape**, and in
one window (1400) every single print on the venue was ours. L5-like
participation is not something we are failing to reach; on this market it
does not exist to be reached.

---

## A. The quantified gap

| metric | L5 BTC15m | COHORT-2 | ratio |
|---|---|---|---|
| windows | 527 | 11 | |
| buy fills per window | 122 | 5.1 | **24x** |
| fills per active minute | 10.72 | 0.68 | **16x** |
| median inter-fill gap, any | 2s | 66s | 33x |
| median gap, same side | 3s | 72s | |
| median gap, opposite side | 9s | 176s | |
| median fill quantity | 10.5 | 1 | |
| median fill price | 0.40 | 0.35 | |
| distinct same-side levels filled per 5s | 6 | 1 | |
| **fills priced >= 0.65** | **26%** | **5%** | |

Price-band distribution of fills:

```
band          L5 BTC15m   COHORT-2
<0.35               45%        48%
0.35-0.50           14%        30%
0.50-0.65           15%        16%
0.65-0.80           13%         5%
>0.80               13%         0%
```

Validation on other cohorts: L5 BTC5m 6.5 fills/window, 2.69/active-minute,
median gap 15s, 43% of fills >= 0.65. L5 ETH 3.0 fills/window, 2.37/min, 47%
>= 0.65. **BTC15m is his densest market; the others are 4x thinner and still
4-5x denser than ours.**

**Timestamp caveat, stated because it bounds every gap figure above.** The L5
sequence file is second-granular (`seconds_to_expiry` and `timestamp` are both
integers). A median gap of 2-3s is therefore an *upper* bound on density —
the true gaps may be sub-second. 95% of fills carry their own
`transaction_hash` (65,111 unique of 68,547), so the same-second multi-level
clusters are separate executions, not one sweep being reported in parts.

---

## B. Which constraint binds — four candidates, three falsified

### B.1 Not the action budget

```
1 rung, reprice 0t, 240s floor       places  fills  net
budget  12                               92     53   -3.07
budget  24                               93     53   -3.07
budget  48                               93     53   -3.07
budget  96                               93     53   -3.07
budget 200                               93     53   -3.07
```

**Identical from 12 slots to 200.** The ledger saturates at ~126 mutations
and stops binding. P2's finding that repricing "exhausts the ledger" was true
and is not the limiting factor.

### B.2 Not the position cap

```
max_residual  4   ->  4.8 fills/window, peak inventory 6, spend $20.07
max_residual  8   ->  4.8 fills/window, peak inventory 6, spend $20.07
max_residual 16   ->  4.8 fills/window, peak inventory 6, spend $20.07
max_residual 32   ->  4.8 fills/window, peak inventory 6, spend $20.07
```

Raising the oscillator's residual cap eightfold changes **nothing**. We never
reach the cap, so it never binds.

### B.3 Not the rung count, and not the quoting price

This was the surprise. **The bid our policy would quote at is already
distributed like L5's fills:**

```
band          our quotable bid    L5 fills
<0.35                      35%         45%
0.35-0.50                  20%         14%
0.50-0.65                  18%         15%
0.65-0.80                  15%         13%
>0.80                      12%         13%
```

The frozen surface has **no price ceiling** (`tick_offsets [0]`,
`rung_count 1`, no max price). It would happily bid 0.85. The book offers a
>= $0.65 bid on **27% of cycle-legs**, and 33% of COHORT-2's own tape prints
made a >= $0.65 buy reachable on one leg or the other.

We placed only 4% of orders there (16% after the lifecycle fix and 3 rungs),
and those orders filled at 22-100%. **The prices were available, our policy
would have quoted them, and our orders fill when we place them. We simply do
not place many orders.**

### B.4 It is the venue

```
window   tape prints   contracts   notional   our fills   OUR SHARE OF THE TAPE
1200               9         665       $210           5                     56%
1215              10         397       $193           6                     60%
1230              15       1,316       $417           6                     40%
1245              12       1,192       $504           4                     33%
1300              13       1,239       $576           6                     46%
1315               5         210       $162           4                     80%
1330               6         243        $89           5                     83%
1345               6         117       $102           5                     83%
1400               5           5         $3           5                    100%
1415               7          13         $4           6                     86%
1430               5          23         $6           4                     80%
MEDIAN             7         243       $162
```

You cannot take 122 fills out of a 7-trade tape. Independent corroboration
from the earlier flow survey (`data/policy_d_port_hot/gemini_flow_result.json`):
Gemini BTC05M runs **0.79 trades/min in hot hours and 0.28 overnight**, for
the whole venue, against L5's 10.72 own-fills/min.

**Placements are bounded by fills plus cancels** (an order only goes on when
a slot frees), fills are bounded by the tape, and the tape is 7 prints. The
architecture is not the ceiling; the market is.

*Bound on this claim:* the tape is POLLED (`/v1/trades`, sampled once per
cycle) and could undercount a burst between reads. It cannot undercount by
the order of magnitude the comparison needs, and all 56 of our own confirmed
fills appear in it.

---

## C. Richer passive surfaces — measured, and they do not converge

```
surface                              fills  f/min  mQty  matched  residual    net   >=.65
BASELINE (defect, 1 rung)               58   0.69    15     3.32     -7.10  -3.78      5%
fix only: reprice 0t, 240s floor        53   0.89    15     2.10     -5.17  -3.07      9%
fix, complement only, 240s              58   0.71    18     2.57     -4.19  -1.62      9%
2 rungs/side                            66   0.79    20     3.32     -7.39  -4.07      8%
3 rungs/side                            66   0.96    16     2.17     -3.01  -0.84      9%
2 rungs/side + reprice 240s             44   1.49    11     1.20     +0.99  +2.19      9%
3 rungs/side + reprice 240s             40   2.22     6     0.67     +3.15  +3.82     10%
3 rungs, reprice 240s, budget 24        40   2.22     6     0.67     +3.15  +3.82     10%
3 rungs, reprice 60s, budget 24         16   7.61     2     0.18     +1.76  +1.95      6%
```

**Every surface lands at 5-10% of fills >= $0.65 and 0% above $0.80.** L5 is
at 26% and 13%. Richer participation does not move our fill-price
distribution toward his, because the extra rungs are necessarily *deeper*
(behind the touch) and deeper is cheaper — the wrong direction for the band
we are missing.

The committed-dollar cap (`max_committed_usd $4.00`) was enforced throughout
so that concurrency could not silently become exposure; it blocked nothing,
because the surface never got that wide.

**The same reading discipline as P2 applies and is not suspended here.** The
arms showing `+2.19` and `+3.82` have `domWon` at 5/9 and 5/10 against the
baseline's 1/11, with matched collapsed from 15 to 6 — the coin landed
differently on a smaller book. They are not improvements.

---

## D. Retention — and replacement is destructive on this venue, whatever triggers it

Fills / matched qty / net, across the grid. Observed baseline is 58 / 15.

```
floor          >=0t         >=1t         >=3t         >=8t   persistent
  30s    18/5/-0.33   18/5/-0.33   20/5/-0.46   33/8/+2.73  58/15/-3.78
  60s    32/8/+0.53   32/8/+0.53   33/8/+0.27  38/10/+2.03  58/15/-3.78
 120s   44/11/-1.02  44/11/-1.02  44/11/-0.96  45/11/-1.23  58/15/-3.78
 240s   53/15/-3.07  53/15/-3.07  53/15/-3.16  55/16/-3.00  58/15/-3.78
 480s   58/15/-4.28  58/15/-4.28  58/15/-4.28  58/15/-4.28  58/15/-3.78
```

**Retention time dominates; the divergence threshold barely matters.** At any
floor, the 0/1/3-tick thresholds are nearly identical; only >= 8 ticks
differs, and it differs by approaching "never replace".

The reason is the measured fill-time distribution: median **64s**, max 737s,
only 30% within 30 seconds, and our resting orders fill at **74-83%**
eventually. **An order that looks stale still fills.** So "price became
stale" is not a useful replacement trigger here: on this venue, replacing a
resting order mostly means cancelling one that was going to fill.

**Do not ship 30s** — it cancels 70% of the orders that filled. But the
conclusion is stronger than "pick 240s": at this fill latency, *time-based
replacement is the wrong control altogether*, and the P2 finding that a 240s
floor helps is better read as "240s is close enough to not-replacing".

---

## E. Mutation architecture

The 5-second decision loop is not the binding constraint either, because
placements are bounded by fills and cancels rather than by cycles: across 11
windows and ~750 cycles, only 93 placements occurred. Separating maintenance
mutations from acquisition mutations would be sound hygiene, **but it would
buy nothing measurable here** — B.1 shows the shared budget stops binding at
~126 mutations and the economics are identical from 12 slots to 200.

Recommendation: do not restructure the budget on this evidence. Revisit only
if a venue with real flow is selected, where the budget would bind again.

---

## F. Economics

No surface produced the transformation being looked for. The sequence sought
was: more representative two-sided fills -> more matched inventory + a
healthier residual-price distribution -> positive economics. What happened
instead: fills rose modestly (58 -> 66), the residual-price distribution did
not move (5% -> 9-10% above $0.65, 0% above $0.80 everywhere), and every arm
whose net turned positive did so with a collapsed matched book and a flipped
coin. **No surface is put forward for deployment.**

## G. L5 fidelity — we are not getting closer

| dimension | L5 BTC15m | best replayed surface | closer? |
|---|---|---|---|
| fills/window | 122 | 6.0 | no |
| fills/active-min | 10.72 | 0.96 (2.22 by trading less) | no |
| same-side gap | 3s | ~70s | no |
| fill qty | 10.5 | 1 | no |
| price band >= 0.65 | 26% | 9-10% | marginally |
| price band > 0.80 | 13% | 0% | no |
| distinct levels / 5s | 6 | 1-3 | marginally |
| EXTEND/COMPLETE mix | 37,649 / 32,117 (1.17) | 32 / 16 (2.0) | no |

On every participation dimension we are one to two orders of magnitude away,
and no execution change closes it.

---

## H. The gate

1. **Participation gap, quantified.** 24x on fills per window, 16x on fills
   per active minute, 33x on inter-fill gap, 26% vs 5% on fills >= $0.65.
2. **Can max-two/one-rung reproduce L5?** No — but that is not why. The
   surface, the ledger, the position caps and the retention rule were each
   varied across their plausible range and **none of them binds**. The venue
   binds: a 7-print, $162 median window, of which we were already 33-100%.
3. **Minimum richer surface supported by evidence: none.** 2 and 3 rungs add
   fills (58 -> 66) without moving the fill-price distribution or the
   economics through a mechanism. There is no evidence-supported case for
   widening the surface on this market.
4. **Retention/repricing lifecycle.** The `resting_since` defect is fixed.
   Do not ship the 30s floor. The evidence does not support time-based
   replacement at all on this venue; a long retention (>= 240s) is
   indistinguishable from not replacing, and that is the better description
   of what the data wants.
5. **OOS economics.** Not produced, because no candidate survived C and F to
   be worth validating out of sample. Producing one would be fitting.
6. **Risk/capital implications.** Raising participation on Gemini BTC15m
   would require taking a larger share of a tape we already dominate — the
   opposite of risk reduction, and with no demonstrated economic benefit.
7. **Proposed q=1 live envelope: NONE, and I am not proposing one.** Nothing
   in P1, P2 or P3 has produced a mechanism that turns these economics
   positive, and the participation hypothesis that motivated P3 is now
   falsified for this market.

### What the evidence points at instead

The programme's question has changed shape. L5's edge is inseparable from a
venue that prints 122 trades and $1,087 per 15-minute window. Gemini BTC15m
prints 7 and $162. **The next question is market selection, not policy
tuning:** is there any Gemini (or other lawful) prediction market whose flow
is within an order of magnitude of L5's habitat? The existing flow survey
says Gemini BTC05M reaches 0.79 trades/min in hot hours -- still 13x short.

That is a discovery and screening question, and `analysis/pmus/` already
holds the slate-wide machinery for it. It should be answered before any
further execution work on BTC15m.

**Rebate reconciliation stays separate** and is unaffected by any of this:
`trig_01YUSyE8mwwzdHAmKb61oKZH` fires at 21:15Z. Actual PAID rebates get
added afterwards, to whatever economics exist then.
