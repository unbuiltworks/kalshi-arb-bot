# MATCHED_SHARE_LINEAGE_V1 — which passive inventory pays, and which becomes residual

`analysis/l5/causal/lineage.py` · BTC15m · 5,504 windows · **631,556 FIFO pairs**
and **60,685 residual lots** · chronological TRAIN/OOS · `btc15m_lineage_v1.json`

Every ultimately matched share traced from acquisition to the complementary
share that paired with it, carrying the observable state at the moment its
first leg was acquired.

## The finding that qualifies everything written before it

Average cost cannot say *which* share was matched, so this pairs shares
first-in-first-out. Both conventions reconcile to the same cash:

| | matched | residual | **total** |
|---|---|---|---|
| authoritative (average cost) | +$162,257 | −$144,190 | **+$18,067** |
| FIFO lineage | **+$57,344** | **−$39,277** | **+$18,067** |
| reallocation | **−$104,913** | **+$104,913** | **0** |

**The matched/residual split is an accounting choice, not a fact.** The two
conventions differ by **$104,913 on an $18,067 total** and agree exactly on the
bottom line. Statements of the form *"every dollar of BTC15m profit comes from
the matched leg and the residual gives most of it back"* are true under average
cost and materially weaker under FIFO.

The mechanism is now understood: L5 buys the **falling** side
(`L5_CAUSAL_MECHANISM_V1`, 62.4%), so the *last* shares on the heavy side are
the cheapest. FIFO leaves those as the residual; average cost charges the
residual the mean price instead. **Average cost therefore overstates both the
matched gain and the residual loss.**

**Cash accounting is unchanged.** The authoritative totals remain the
average-cost decomposition in `analysis/l5/btc15m/decompose.py`. FIFO exists
here only to say which shares paired.

## Matched economics, by the state at acquisition (bp per dollar, TRAIN → OOS)

**Time to expiry is the dominant, monotone, replicating signal.**

| TTE at acquisition | TRAIN | OOS |
|---|---|---|
| ≤ 60 s | 258.7 | **649.3** |
| 60–180 s | 230.1 | 161.1 |
| 180–420 s | 109.3 | 117.3 |
| 420–700 s | 77.1 | 45.0 |
| **> 700 s** | 48.2 | **−11.0** |

Shares acquired with more than ~12 minutes left produce **nothing** out of
sample. Nearly all matched economics are created in the last few minutes.

**Probability is U-shaped and replicates**: >0.90 → 196.7/**258.6**, ≤0.10 →
142.8/**199.8**, against 0.30–0.50 → 81.7/**15.9** and 0.50–0.70 → 70.5/31.4.
The decided end of the window pays; the middle does not.

**Reversals, not drift, generate pairs.** Rising and replicating: 0 reversals
101.0/**14.8**; 1–2 → 70.3/44.6; 3–5 → 101.9/78.9; **6+ → 145.8/93.9**. This
refines the volatility result — |Δp| magnitude does not raise matched yield,
but *choppiness* does, which is exactly what sweeps both sides of a passive
surface.

Two results that are directionally stable but weaker: **run position 1 is best**
(129.7/106.6, decaying to 72.9/42.7 by 4–5), and **acquiring the already-long
side pays better** than completing (105.2/**81.8** vs 98.6/**19.8**) — the
extend-like share pairs with a later complement across a wider spread.
`fee_bearing` **flips sign between halves** and is not stable.

## Matched economics, by what happened afterwards

**The complement has to arrive fast. This is the most actionable number here.**

| time to complement | TRAIN | OOS |
|---|---|---|
| ≤ 5 s | 137.9 | 128.7 |
| **5–30 s** | **148.1** | **137.6** |
| 30–120 s | 84.6 | 76.0 |
| **120–300 s** | 81.0 | **−191.9** |
| **> 300 s** | 27.2 | **−474.7** |

Past two minutes the pair is **value-destroying out of sample**. That is the
maximum useful patience, and it is far shorter than any policy here assumes.

Realized pair cost is monotone and near-identical across halves — ≤0.90 →
+2611/+2397, 0.96–1.00 → +177/+172, 1.00–1.05 → −229/−232, >1.05 →
−1461/−1283. **A realized FIFO pair above 1.00 destroys value.** This is a
different object from EXPLORATORY_V2's *instantaneous* pair at one print, and
the two are not in conflict: the instantaneous pair carries little information
about the realized one.

## Residual economics, by the state at acquisition

**Run position is the clean, monotone, replicating driver of residual loss:**

| run position | TRAIN | OOS |
|---|---|---|
| 1 | −470 | −2,168 |
| 2–3 | −543 | −2,040 |
| 4–5 | −705 | −2,424 |
| 6–8 | −1,113 | −3,018 |
| 9–12 | −1,497 | −3,446 |
| **13+** | **−2,756** | **−5,883** |

This resolves the run-length puzzle with lineage rather than inference: long
runs do **not** reduce matched yield — they produce **catastrophic residual**.
The earlier per-position attribution said the same thing; this says which
shares.

Volatility hurts the residual leg specifically (|Δp| >0.10 → −1,358/**−3,499**;
range >0.15 → −1,194/**−3,440**; 6+ reversals → −1,132/**−3,336**), and
mid-probability residual is the worst of all (0.30–0.50 → −2,604/**−4,346**),
which is what a coin flip at a high cost basis should look like.

## The minimum inventory behavior the matched component appears to require

Stated as what the evidence supports, not as a policy:

1. **Be present late.** Nearly all matched economics come from TTE ≤ 180 s;
   beyond ~700 s the contribution is negative out of sample.
2. **Value a pair only if the complement is fast.** Positive to ~120 s, clearly
   negative past it.
3. **Bound the run.** Matched yield does not fall with run position; residual
   cost rises monotonically and steeply.
4. **Prefer decided probabilities.** Extremes pay on the matched leg and cost
   least on the residual leg; 0.30–0.50 is the worst region for both.
5. **Choppiness is only attractive when the complement is fast**, because the
   same reversals that create pairs also worsen the residual.

## What is not claimed

A lineage is not a causal claim: that a share paired with a later one does not
mean it was bought in order to. Why any share was bought is **unobservable** —
there is no book and no unfilled order in this dataset. And the FIFO pairing is
a convention, which the reconciliation above quantifies rather than hides.

BTC15m only. Findings, not proposals. No cash accounting changed, no risk limit
changed, no executor started.
