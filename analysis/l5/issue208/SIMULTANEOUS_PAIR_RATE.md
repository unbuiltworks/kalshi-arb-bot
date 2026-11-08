# More pairs without more residual: which lever actually works

PRIORITY 4. Out-of-sample only. Every figure is **historical replay** on
recorded ladders — no fill, no profit, nothing realized.

Method reuses `queue_scenarios` wholesale: its ladder loading, its
PROVEN / POSSIBLE queue verdicts, its −0.0236/contract residual constant
and its settled-equivalent accounting. The only new question is the joint
outcome of the two legs, and the only new lever is **asymmetric depth**.
Rebate is the **confirmed credited** $0.00727/fill.

Read the `pessimistic` arm. It is the PROVEN, queue-independent lower
bound — a fill it counts would have happened whatever our queue position.

---

## 1. The answer, in one line

**The residual cost per entry barely moves with depth at all** — it stays
between −0.0081 and −0.0114 across all sixteen depth combinations. What
depth changes is the **value of a completed pair**. So "more pairs without
more residual losses" is the wrong trade to look for: the residual is
near-invariant and **pair value is the lever.**

## 2. Symmetric depth: deeper is monotonically better, in every arm

| depth | P(pair) | P(resid) | mean pair cost | pair/entry | resid/entry | rebate/entry | **net/entry** |
|--:|--:|--:|--:|--:|--:|--:|--:|
| 0 | 0.6534 | 0.3420 | 0.9899 | 0.006626 | −0.008072 | 0.011987 | **+0.010541** |
| 1 | 0.5665 | 0.4194 | 0.9699 | 0.017074 | −0.009898 | 0.011286 | **+0.018462** |
| 2 | 0.5247 | 0.4226 | 0.9498 | 0.026316 | −0.009974 | 0.010702 | **+0.027043** |
| 3 | 0.4785 | 0.4818 | 0.9298 | 0.033576 | −0.011370 | 0.010460 | **+0.032666** |

The pair *rate* falls from 65% to 48% and net still **triples**, because
the mean pair cost falls 0.9899 → 0.9298. Fewer pairs, each worth much
more, and the residual leaks only 0.0033/entry more.

**Internal consistency check:** at d=2, `pair + resid` excluding rebate is
`0.026316 − 0.009974 = +0.016342`, which reproduces the **+0.0163/entry**
figure reported earlier for simultaneous d=2 OOS pessimistic. That figure
excluded rebates; this one states them separately. The two agree exactly.

### And it connects to the deployed configuration

Mean pair cost at **d=0 is 0.9899 — above the 0.96 fee-safe ceiling.** At
d≥2 it is 0.9498 or better, inside it. ROUTE B's deployed completion depth
is **0**. The opening probe's is **2**.

## 3. Asymmetric depth: my hypothesis, and the data refuting it

The hypothesis was that a lone leg is an *asymmetry* of outcome, so if one
side fills more readily, resting that side deeper would equalise the two
fill probabilities and convert lone legs into pairs or harmless no-fills.

**The first half is confirmed.** Holding DOWN at the touch and deepening
UP moves which side gets stranded, cleanly:

| up depth | dn depth | lone UP | lone DOWN |
|--:|--:|--:|--:|
| 0 | 0 | 135 | 88 |
| 1 | 0 | 129 | 106 |
| 2 | 0 | **124** | **130** |
| 3 | 0 | 116 | 139 |

At (2, 0) the stranding is almost exactly balanced — 124 against 130, from
135/88.

**The second half is false.** Balancing *which* side strands did **not**
reduce *how often* stranding happens:

| cell | P(resid) | net/entry |
|---|--:|--:|
| (0, 0) | 0.3420 | +0.010541 |
| (2, 0) | **0.4045** | +0.019078 |
| (2, 2) | 0.4226 | **+0.027043** |
| (3, 3) | 0.4818 | **+0.032666** |

P(residual) at (2,0) is *higher* than at (0,0), and (2,0) is beaten by
symmetric (2,2) on net. Across the whole grid, no asymmetric cell beats
the symmetric cell at the deeper of its two depths.

**So asymmetric depth is not the lever.** It redistributes the stranding
without reducing it, and gives up pair value to do so. Recorded because a
plausible idea that the data kills is worth as much as one it supports,
and because someone will propose it again.

## 4. What this does and does not license

**Does:** entry depth is the highest-value dial available, it is already
implemented, and the evidence points the same way in all three queue arms.
Deeper entry gives cheaper pairs, pairs inside the 0.96 ceiling, and
better net per entry even on the queue-independent lower bound.

**Does not:**

- **These entries are not independent.** Every frame of a window generates
  one, drawn from a handful of OOS windows, so the frames are heavily
  autocorrelated and the effective sample is far smaller than n≈600. The
  *ordering* across depths is consistent and reproducible; the magnitudes
  are not precise to three decimals.
- It says nothing about **completion** depth. This is an ENTRY analysis:
  both legs quoted simultaneously from the same frame. The sequential
  rescue of an already-stranded leg remains negative in every cell
  (`SEQUENTIAL_COMPLETION.md`), and nothing here changes that.
- Deeper entry means **fewer entries filled per window**. At d=3 more than
  half of entries end as a lone leg or nothing. Absolute pair volume
  falls even as net per entry rises.
- It is not permission to **increase frequency or enable replenishment**.
  Per-entry economics improving is not the same as more entries being
  better, and the owner's instruction on that stands.

## 5. The one thing to measure live, which this cannot

Every fill above is a **queue model**, not a fill. The pessimistic arm is
a lower bound by construction, but it is still a model of our position in
a queue the venue does not publish. One capped window measuring our
**actual** queue position at a known depth would tell us which arm we
really live in — and that is worth far more than another depth sweep.

## 6. Provenance

- `analysis/l5/issue208/simultaneous_pair_rate.py` — this analysis.
- `analysis/l5/issue208/queue_scenarios.py` — ladders, queue verdicts,
  residual constant, settled-equivalent accounting. Unmodified.
- Sources: `QS.OUT_OF_SAMPLE` only.
- Residual −0.0236/contract (measured); rebate $0.00727/fill (confirmed
  credited, $2.13 over 293 fills).
