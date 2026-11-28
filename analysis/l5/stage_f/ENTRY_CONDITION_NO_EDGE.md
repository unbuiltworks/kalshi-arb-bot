# Every entry in the seven windows — **VERDICT: NO EDGE**

No condition survives. The reason is **statistical power, not absence of
effect**, and the evidence required to settle it is quantified in §6.

System intact. No new infrastructure, no capital increase, no deployment.
`maker_pair_ceiling` 0.96. Canonical BTC15m net +$16,436. None of the
previously rejected experiments is revisited.

---

## 1. Every entry, reconstructed

| | |
|---|---|
| pairs completed | **8** |
| windows stranding a leg | **7** — exactly one residual in every window |
| **entry outcomes** | **15** |
| **joint-fill share** | **53.3%** (8 of 15) |

Stranded-leg own prices: **0.40, 0.46, 0.44, 0.47, 0.48, 0.45**. Completed
pairs' legs span **0.42–0.62**.

**No separation on entry price.** The stranded legs cluster at 0.40–0.48 —
the middle of the authorized 0.20–0.60 band, which is exactly where most
activity sits. The one pre-entry observable available for all seven windows
carries no signal.

## 2. Expected value per entry, using entry-time information only

| | |
|---|---|
| edge per completed pair | **+0.034630** (0.017315/contract × 2.0) |
| mean settled residual | **−0.048159** (7 strands, net −0.337110) |
| observed P(both fill) | **0.5333** |
| **EV per entry** | **−0.004005** |
| × 15 entries | **−0.060070** |
| actual campaign | **−0.075500** |

The EV model reproduces the campaign to within $0.015 — it is the right model
of the failure.

| | |
|---|---|
| **break-even P(both fill)** | **0.5817** |
| observed | 0.5333 |
| **shortfall** | **4.8 percentage points** |

**The policy misses break-even by under five points of joint-fill rate.**

## 3. And n=15 cannot resolve five points

| | |
|---|---|
| Wilson 95% CI on 8/15 | **[0.301, 0.752]** |
| break-even 0.5817 inside it? | **YES** |

**The interval is 45 points wide and the question is 4.8 points.** The campaign
cannot tell whether we are above or below break-even — which means it also
cannot tell whether any entry condition helps.

**Any condition I fitted to these 15 entries would be fitted to noise.** I did
not fit one, and that is the finding rather than a gap in the work.

## 4. The comparable L5 observation

| | residual / matched |
|---|---|
| L5 regime A (03-19 → 04-19) | 0.3556 |
| L5 regime B (04-20 → 07-13) | 0.2260 |
| L5 regime C (07-14 → 08-31) | 0.3594 |
| **L5 mean** | **0.3137** |
| **ours, 7 windows** | **0.9530** |

**Our residual per matched contract is 3.04× L5's.** He completes ~3.2 matched
contracts for every residual contract; we complete **~1.05**.

That is the economic failure stated as a ratio measurable on his own fills, and
it is stable across an 87× change in his capital — so it is a property of his
*sequence*, not of his size. It is the one quantity on which a future
correction should be scored.

## 5. Would no-entry have helped?

| | |
|---|---|
| skip every window | **$0.0000** vs actual −$0.0755 → **+$0.0755** |
| skip only the 4 losing windows | **+$3.4042** |

**The second figure is leakage and I report it only to size the prize.** It
selects windows by their settlement, which is not available at entry. A valid
condition would have to capture up to **$3.40 across seven windows** — and no
observable we hold captures any of it.

## 6. NO EDGE — and the exact evidence required

**To resolve 53.3% against the 58.2% break-even at 80% power and 5%
two-sided: 1,630 entries.** At our observed 2.1 entries per window that is:

| | |
|---|---|
| **windows** | **≈ 760** |
| **continuous days at 96 windows/day** | **≈ 8** |
| expected cost if the true rate is 53.3% | **−$6.52** (1,630 × −$0.004005) |
| expected daily cost | **−$0.82/day** — inside the existing $5 halt |
| capital required | **none beyond the existing $20 authorization** |

**This datum cannot be obtained in shadow.** The shadow's fill model is
`QUEUE_AWARE_THREE_SCENARIO` — **modelled**, not observed. Joint-fill
probability is the one quantity that must come from real resting orders meeting
a real queue, so the 384-window control cannot supply it however long it runs.

**So the evidence requirement is specific and bounded:** ~760 live windows at
the current limits, expected cost ≈ $6.50, to learn whether a 4.8-point gap is
real. I am **not** requesting that campaign. I am stating its exact price, so
the decision is yours and is made on a number rather than on hope.

**What I will not do:** fit a condition to 15 entries and present it as an
edge. The prize is $3.40 over seven windows, the measurement error is 45 points
wide, and every candidate I checked — entry price, imbalance state, pair cost,
liquidity — either has no variance, has been rejected already, or sits inside
the noise.

## 7. The honest summary

Matched inventory earns **+$0.017315/contract** and the residual loses more,
and the gap is **4.8 points of joint-fill rate**. That is a smaller failure
than it has looked throughout: the policy is close to break-even, not far from
it. But "close" is unmeasurable at n=15, and the only instrument that can
measure it costs ~760 live windows and ~$6.50.

Whether that is worth spending is a capital-allocation judgement, not a
research one, and it is yours. **Against the $480/day objective it is not
worth it** — even a confirmed break-even joint-fill rate earns approximately
zero, and the 82–85% incentive term credited to us at **$0.00** remains the
only quantity that would change the scale.
