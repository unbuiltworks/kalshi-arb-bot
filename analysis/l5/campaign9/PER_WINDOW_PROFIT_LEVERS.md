# Net realized profit per window: the three levers, measured

Reproduce with `clock_5m_vs_15m.py`, `clip_size_vs_fill.py`,
`open_state_vs_realised_pair.py` in this directory. Read-only. No capital
used, no limit changed, nothing deployed.

Per-window profit is `matched contracts × (1 − pair_cost)`. Matched contracts
have no safe headroom (coverage 12 %, the idle time is the ceiling refusing
guaranteed losses). So the levers are: a faster clock, a bigger clip, or a
cheaper pair.

---

## 1. BTC5m — NO

**PMUS does not list a 5-minute up/down market.** The `btc-updown-5m` slugs
in `analysis/l5/breadth/universe_scan.json` come from
`gamma-api.polymarket.com` — Polymarket **International**, a different venue
from `api.polymarket.us`. There is no PMUS 5m lane to validate.

The only venue where both clocks exist with book *and* print tapes is
Gemini, so that is where the clock question can be asked from data. Gemini
publishes one signed instrument per window, so a pair is buy-at-bid plus
sell-at-ask and `pair_cost = 1 − spread` exactly.

| | 5m | 15m |
|---|---|---|
| windows with a book | 400 | 136 |
| windows with any print | 384 (96 %) | 25 (18 %) |
| windows with **both sides** printed | 191 | 4 |
| two-sided rate | **47.8 %** | 2.9 % |
| median spread | **0.0200** | **0.0200** |
| median attainable pair cost | **0.9800** | **0.9800** |
| share of windows at pair ≤ 0.96 | 4.5 % | 11.0 % |
| upper-bound cycles/hour | **12.03** | 0.176 |

**The 5-minute clock buys flow, not edge.** 68× the two-sided cycles per
hour — and an *identical* median pair cost of 0.98 on both clocks, because
the spread is 0.02 on both. The clock changes how often a cycle is
available, not what one is worth.

And 0.98 is worth very little. Against PMUS BTC15m's settled **$0.04697 of
matched surplus per contract** (achieved pair cost 0.9530), a 0.98 pair
yields $0.02 gross. Gemini's maker fee is `0.0175·C·P(1−P)` = $0.00875 per
pair at P=0.50, so **$0.01125 net per contract** without the rebate — about
**a quarter** of the PMUS per-contract edge. At 12 cycles/hour and 1
contract that is ≈ **+$0.135/hour**, against PMUS BTC15m's +$0.1933/window
≈ +$0.773/hour at four windows an hour.

The rebate (`0.021·C·P(1−P)`, fee-positive by 1.2×) would roughly double the
Gemini figure, but it **rounds down daily**, which is why five real live
fills credited $0.00; it needs q ≳ 1 contract to pay anything.

**Not recommended, and not close.** Beyond the economics: Gemini has never
opened *and* settled a position in this programme, its P&L is explicitly
"unquantified — not zero", the position-semantics P0 (one signed position
per instrument vs our `Inventory(yes_qty, no_qty)`) is unresolved, and
`PL-1`/`PL-2` are open mandatory pre-live defects.

**Discrepancy flagged:** `GEMINI_L5_CURRENT.md` reports attainable BTC5m
pairs of 0.71–0.79. This tape (Aug 27 – Sep 2, 62.35 h) gives a median of
0.98 with only 4.5 % of windows at or under 0.96. The two do not agree and
I have not reconciled them. The 0.71–0.79 figure should not be relied on
until someone does.

---

## 2. Larger clips — marginal, and capped by the envelope

**Fill rate does not fall with size, over the range we have actually
traded.** 87 venue-confirmed terminals across C6/C7/C8, by quantity tercile:

| clip | n | fills | fill rate |
|---|---|---|---|
| small (≤ 1.93) | 30 | 19 | 63.3 % |
| mid (≤ 2.06) | 32 | 14 | 43.8 % |
| large (≤ 2.47) | 25 | 20 | **80.0 %** |

Non-monotonic, so no size penalty is detectable. **But the whole range is
1.73 → 2.47 contracts.** The terciles break at 1.93 and 2.06. This says
nothing whatever about a 2× or 3× clip — we have never placed one.

**The cash envelope caps it at 1.32×.** C8's worst simultaneous committed
cash was **$9.0833 of the $12** `max_cash_deployed_usd` gate. (Not the
$22.2436 figure — that is the worst-case budget high-water mark against
$29.30 and is explicitly not a gate.) So the largest clip that stays inside
the approved envelope is about **1.32×** current, i.e. ~2.4 contracts, which
is already the top of the range we have traded.

**What 1.32× buys, stated honestly.** Matched surplus is linear in
contracts, so it scales the measured mean by 1.32× — and the measured
standard deviation by 1.32× too. The t-statistic is **unchanged**. It buys
roughly **+$0.062/window** of expected matched surplus and **zero additional
confidence**, while taking worst committed cash to the gate's edge with no
margin for a window that commits more than C8's worst did.

---

## 3. A cheaper pair through selectivity — NO SIGNAL

This was the lever with the most room. Realised pair costs across C6/C7/C8
span **0.55 to 1.10** (23 pairs, median 0.94, mean 0.9296), and the opening
gate cannot discriminate at all: C7 measured `PAIR_COST_ABOVE_CAP` firing
**zero** times and showed it is structurally incapable of firing, because
the planned pair is `1 − spread − 2·depth·tick` = exactly 0.9500 at a 1-tick
spread and depth 2. Every opening is admitted on an identical 0.95
projection.

So: does anything observable when the first leg is placed separate the cheap
pairs from the dear ones? Spearman rank correlation against realised pair
cost, 23 pairs:

| observable at the open | ρ |
|---|---|
| `open_spread` | +0.2414 |
| `open_own_px` | −0.1538 |
| `open_depth` | −0.1069 |
| `open_queue_ahead` | −0.0935 |

**Nothing. Every |ρ| is inside 0.25.**

**And this is a correction to a signal I nearly reported.** On C6+C7 alone
(13 pairs) `open_queue_ahead` gave **ρ = −0.4959** with a clean mechanism —
a deep queue means we only fill when the market moves through us, by which
time the complement is cheap, whereas a thin queue fills us instantly at a
price that then goes against us. The median split showed 0.880 vs 0.957, a
**7.7 ¢** difference per pair, which would have roughly tripled the
per-contract surplus.

Adding C8's 10 pairs takes ρ to **−0.0935**, and removing the single 0.55
observation **reverses the split's sign** (high-queue 0.9527 vs low-queue
0.9409). The apparent signal was one data point. I had originally excluded
C8 from this test on a schema argument — its fields are `qa`/`depth`/`spr`/
`own` rather than the long names — and that exclusion is exactly what made
the dead signal look alive.

**Selectivity is closed on existing evidence.** Not "promising"; closed.

---

## Recommendation

**There is no evidence-backed improvement to net realized profit per window
available from existing data.** The clock is worse, selectivity has no
signal, and the clip is capped at +32 % with proportional variance. I am not
going to dress any of those up as a finding.

Two distinct questions are worth separating, because they have different
answers:

**For profit** — the only lever inside approved limits is the clip, at
**1.25×** (not the full 1.32×, so the $12 gate keeps margin for a window
that commits more than C8's worst). Expected effect ≈ **+$0.048/window** of
matched surplus, scaling stranded losses by the same factor, t unchanged.
Requires owner approval. It is a small, honest gain and it buys no
information.

**For evidence** — the binding resource is **pairs observed: 23 across three
campaigns**. Every conditional rule that could raise per-window profit needs
several times that, and the matched series itself reaches t=2 at 6 windows
only unconditionally. More windows is the cheapest source of pairs at
unchanged maximum loss, since the $5 daily and $20 cumulative halts bound
the day regardless of count. That is the 8→16 proposal, which the owner has
correctly demoted from a profitability improvement — it is not one. It is a
sample-size purchase.

**My recommendation for the highest-return next live validation: the clip
increase to 1.25× run inside the existing 8-window campaign shape.** It is
the only change that raises expected per-window profit at all within
approved limits, it needs no code change, it keeps every gate, and it
accumulates pairs at 1.25× the rate per window as a side effect. If the
choice is instead to buy evidence outright, 16 windows at the current clip
dominates it — but those are different purchases and should be approved as
such.
