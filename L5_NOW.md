# L5_NOW — current operating note

Updated 2026-09-20. Supersedes nothing in `L5_OPERATING_MODEL.md`; this file
records what the live sports-transfer experiment currently establishes.

## 1. The PMUS zero-match is NOT a liquidity failure

Across the venue-separated chain run:

| venue | first fills | matched | dominant break |
|---|---|---|---|
| PMUS (executable) | 13 | 0 | `1a_POLICY_NEVER_RECOMMENDED_COMPLETE` 92.3% |
| International (reference only) | 33 | 11 (33.3/100) | `1a` 57.6% |

**The frozen policy declined to seek an opposite position in 92.3% of observed
PMUS chains; only 3 chains were ever COMPLETE-eligible.** Nothing in that result
shows PMUS liquidity cannot complete a pair. Conditional on becoming
COMPLETE-eligible, PMUS is 0/3 — a sample of three. No transfer verdict either
way.

## 2. CORRECTION: the "p10-25c only" rule was mine, not v2's

On 2026-09-20 I reported that "the frozen v2 model recommends COMPLETE in
exactly one price bucket: p10-25c." **That statement was wrong as framed.** It
came from a probe that covered only `imb1-10 | tte>10m` with
`last_rel ∈ {open, extend}`. At that same slice with `last_rel=complete`, v2
recommends COMPLETE in *every* price bucket.

## 3. What v2 actually encodes: persistence in `last_rel`

Audited against the canonical L5 corpus, systematic-regime cohort, 69,003
imbalanced decisions, replicating `build_model`'s own decision loop:

| last_rel | n | L5's ACTUAL complete rate | v2 says |
|---|---|---|---|
| extend | 33,889 | **31.5%** | extend |
| complete | 31,931 | **68.4%** | complete |
| open | 1,902 | 34.5% | extend |

v2 tracks this correctly. Only 14 of 165 cross-tab cells (6.5% of decisions)
mismatch, and those sit at the 35–45% boundary. **Verdict: A, a real L5
behavioural rule — not a discretization artifact.** The marginal ~50/50
complete rate seen per price bucket is a *mixture* of these two modes; price is
not the predictor, `last_rel` is.

## 4. The real transfer blocker is the inventory regime

L5's own recorded behaviour sits at:

| imbalance | share of L5 decisions | actual complete rate |
|---|---|---|
| imb>50 | **75.6%** | 48.9% |
| imb10-50 | 19.9% | 48.9% |
| imb1-10 | **4.5%** | 50.4% |

The frozen shadow caps residual at `MAX_RESIDUAL_UNITS=3`, so every shadow chain
lives permanently in `imb1-10` — the regime L5 occupied 4.5% of the time — and
is then frozen at `no_action` by the cap after roughly two EXTENDs. L5 flips
extend→complete about 31.5% of the time *per decision*, so it completes
constantly over a long run; a three-decision sequence terminated by a cap gets
few chances to flip.

**This is a diagnosis, not a proposed change.** No model, bucket, threshold,
cap or parameter has been modified.

## 5. Habitat: book reach is not executable recycling

Individual live-sports markets do reach the International BTC15m book-reach
reference (INTL lol 90.9%, wta 77.5%, PMUS sea full-game-total 71.5% at 120s).
But P(a reach is followed by a *compatible print* within 60s) reorders
everything: PMUS cs2 76.9%, PMUS valorant 77.8%, INTL wta 6.7%, PMUS atpcq
tennis 0.0%. Reach is an upper bound on fill opportunity, not a measure of it.

## 6. Standing separations

* International is **reference/fidelity only** — L5's own venue, not executable
  for us, never a trading target.
* PMUS is the **primary executable transfer target**.
* The two are never pooled in any flow or chain statistic (asserted in code:
  zero slug overlap, disjoint venue tags).
* Gemini A+B continues unchanged as the negative control.
* No orders, no arming, no live capital, no Model A change.
