# Band × spread: the answer is "yes, and we have been fishing in the wrong pond"

Run against `analysis/l5/BAND_SPREAD_PREREGISTRATION.md`. Offline, read-only,
nothing deployed, nothing fitted.

**Headline: clearing cells exist, we have almost never traded them, and L5's
own tape independently points at the same subset. This is the first result in
the programme that says "go somewhere else *in* this venue" rather than "stop".**

---

## 1. The grid: which cells clear

5,381 admitted observations across **376 distinct markets**. A cell clears when
the spread it offers pays for the observed 28.2% one-sided-fill risk.

At the measured median matched size (**q = 10.3**, required spread 0.0729):

| band | spread bucket | obs | markets | med spread | edge $ | break-even incidence | clears |
|---|---|---:|---:|---:|---:|---:|---|
| 35–65 | ≤0.01 | 3578 | 286 | 0.010 | 0.103 | 5.1% | no |
| 35–65 | 0.01–0.02 | 863 | 139 | 0.020 | 0.206 | 9.7% | no |
| 35–65 | 0.02–0.03 | 336 | 91 | 0.030 | 0.309 | 13.9% | no |
| 35–65 | 0.03–0.05 | 226 | 63 | 0.040 | 0.412 | 17.7% | no |
| **35–65** | **>0.05** | **148** | **54** | **0.080** | **0.824** | **30.1%** | **YES** |
| 45–55 | >0.05 | 64 | 30 | 0.080 | 0.824 | 30.1% | YES |
| 40–60 | >0.05 | 109 | 45 | 0.080 | 0.824 | 30.1% | YES |
| 30–70 | >0.05 | 210 | 68 | 0.070 | 0.721 | 27.4% | no |

At **q = 20** eight cells clear, including 0.03–0.05 in every band (63–68
markets each). At **q = 5.75** none clear — size does matter here, because
unlike the residual ratio the *required spread* falls with size.

Note the 30–70 row: widening the band to 30–70 pulls the wide bucket's median
spread from 0.080 down to 0.070 and it stops clearing. **The widest spreads sit
nearest 50/50**, which is the owner's hypothesis appearing in the venue's own
book data.

## 2. We concentrated our trading in the one bucket that cannot pay

Our 78 filled episodes, by the spread we actually entered at:

| bucket | n | paired | stranded | incidence | spread shortfall |
|---|---:|---:|---:|---:|---:|
| ≤0.01 | **46** | 31 | 15 | 32.6% | **7.29×** |
| 0.01–0.02 | 15 | 13 | 2 | 13.3% | 3.65× |
| 0.02–0.03 | 10 | 8 | 2 | 20.0% | 2.43× |
| 0.03–0.05 | 4 | 2 | 2 | 50.0% | 1.82× |
| >0.05 | **3** | 2 | 1 | 33.3% | 0.91× |

**59% of everything we traded was in the bucket that needs 7.3× more spread
than it has.** Seven episodes total in the two buckets that can clear. The
incidence column is reported for completeness and should not be read as
evidence — n = 3 and n = 4 in the cells that matter.

## 3. L5's own tape says the same thing, on 2,216 windows

From `l5_residual_state_replay` (measured previously, cited not recomputed):

| L5's first leg | n | paired | **residual incidence** |
|---|---:|---:|---:|
| balanced 0.35–0.65 | 1143 | 84.25% | **15.75%** |
| leaning 0.20–0.35 / 0.65–0.80 | 606 | 69.14% | 30.86% |
| lopsided <0.20 or >0.80 | 467 | 48.82% | 51.18% |

By first-leg price the peak is exactly where the owner put it: 0.4–0.5 pairs
**88.9%**, 0.5–0.6 **85.2%**, falling to 46.7% at 0.8–0.9 and 28.4% below 0.10.

**In the balanced band L5's residual incidence is 15.75% — below the 17–19%
break-even range.** Outside it, it doubles, then triples.

## 4. The finding that reframes the whole programme

```
L5 overall residual incidence   27.35%   (1610 paired of 2216)
our  overall residual incidence 28.2%    (78 filled episodes)
```

**L5 strands at essentially the same rate we do.** Our incidence was never a
skill deficit and no amount of behavioural fidelity was ever going to fix it —
which is why two pre-registered studies failed to move it. L5's advantage in
the balanced band comes from *being in the balanced band*, not from managing
residuals better once in one.

So the instruction that follows is the opposite of the last three weeks of
work: **stop trying to reduce incidence, and change which markets are eligible
at all.** L5 does not strand less; he strands in cheaper places, and more of
his trading sits where the book pays for it.

## 5. What this study cannot tell you, as pre-registered

**The both-fill rate is not observable.** We have never quoted both sides
simultaneously, so there is no tape of it. The only route to a number is the
product of two one-sided fill rates, and independence is exactly what the
two-sided-flow hypothesis denies. No such number is computed here.

**And the caveat that actually decides this.** Depth at the touch collapses as
the spread widens, in the same 35–65 band:

```
spread <=0.01   median bid_touch_qty  7500.0    (286 markets)
spread 0.01-0.02                       810.4
spread 0.02-0.03                       821.3
spread 0.03-0.05                       127.7
spread >0.05                            91.0    ( 54 markets)
```

**82× thinner.** The "clears" column holds residual incidence fixed at 28.2%,
a figure measured almost entirely in books 80× deeper. If a 91-deep book
strands more often — and the obvious mechanism says it should, since fewer
counterparties means a passive complement waits longer — the cell stops
clearing. **That is the single unknown this grid cannot resolve, and it is the
thing a shadow observation could resolve at zero risk.**

Depth 91 against our size of ~10 contracts is still 9× our size, so posting is
not the constraint. Being traded against is.

## 6. What to do next, in the order the evidence supports

1. **Shadow-observe the clearing cells.** 54 markets in 35–65 with spread
   >0.05. Watch, without quoting, how often both touches trade within a
   session. That measures the one unknown — whether wide-spread books actually
   fill — with no order path and no risk.
2. **Only then** consider a bounded two-sided probe, and only in a cell that
   survived step 1.
3. **Do not re-run incidence-reduction work.** §4 closes it: L5 does not beat
   us on incidence and never did.

---

*Read-only. No live change. Campaign stopped, deployments paused, holdout
still sealed.*
