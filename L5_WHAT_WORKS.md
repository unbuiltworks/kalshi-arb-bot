# What works, and what it would take to be profitable

Measured from the 12 traded windows on 2026-10-25 (both campaigns, venue
settlement rows) and the 453-window shadow controller. **Nothing deployed, no
configuration changed, no authorization requested in this document.**

---

## 1. What works is not in doubt

**A completed pair is profitable by construction.** Pair cost under $1 per unit,
redeems exactly $1 per unit. There is no forecast in it.

| | windows | settled | mean | profitable |
|---|---|---|---|---|
| **completed clean** | **7 of 12** | **+$1.4220** | +$0.2031 | **7 of 7** |
| stranded at expiry | 5 of 12 | −$2.7548 | −$0.5510 | 1 of 5 |
| all | 12 | −$1.3328 | — | 8 of 12 |

7 for 7 is not luck — it is arithmetic. And the return is large:

> **11.81% return on deployed capital per 15-minute window**
> ($1.4220 profit on $12.0380 of matched cost, mean $1.72 deployed per window)

Across all 12 windows, pair P&L is **+$2.1521** and residual P&L is
**−$3.4849**. We do not have an edge problem. **We have a completion problem.**

The 453-window shadow says the same at scale: +$73.48 pair against −$149.18
residual, and **matched units 278 against stranded units 1,057** — we strand
3.8 units for every one we match. Break-even is 1.87. We are over by 2.03×.

---

## 2. The one thing destroying it

Per unit: a matched unit earns **+26.4¢**; a stranded unit loses **−14.1¢**.
Four of four residuals in campaign 2 settled worthless. The shadow's adverse
rate is **0.7754** — 1,219 of 1,572 fills adverse — against a fill rate of
0.8192. Our quotes fill; they fill on the wrong side.

`possible_misses: 0` against 117,937 refusals: **we are not losing money by
being too cautious.** Every refusal protected money or admitted ignorance.

---

## 3. The fix — and the $0.96 cap is the thing in the way

In **0130z** (the −$1.1594 strand) a *taker* completion was available all-in at
**0.9714** at T−723s. That is **under 1.00 — a locked profit** — but over the
0.96 ceiling, so the engine was forbidden from taking it and held a residual
that settled worthless.

**The completion ceiling is tighter than breakeven, so it refuses profitable
rescues.** 0.96 is the right target for *opening* a new pair, where margin
matters. For *closing a residual you already hold*, the comparison is not
"pair < 0.96" but "pair < 1.00 versus a residual that is worthless 64% of the
time."

Causal sweep — fire at the first frame where the **fee-inclusive** all-in pair
clears the cap (taker θ 0.0695, $0.01/order, rounded up; no hindsight):

| cap | 0030z | 0100z | 0115z | **0130z** | 0145z | **total** | vs actual |
|---|---|---|---|---|---|---|---|
| actual | +0.2898 | +0.1144 | +0.5950 | **−1.1594** | +0.2028 | +0.0426 | — |
| 0.96 | +0.2898 | +0.1101 | +0.5222 | **−1.1594** | +0.0692 | −0.1681 | −0.2107 |
| 0.97 | — | — | — | **−1.1594** | — | −0.1681 | −0.2107 |
| **0.975–0.99** | +0.2898 | +0.0615 | +0.5222 | **+0.0535** | +0.0692 | **+0.9962** | **+0.9536** |
| 1.00 | — | — | — | +0.0161 | — | +0.9588 | +0.9162 |
| 1.02 | — | — | — | −0.0026 | −0.0088 | +0.8621 | +0.8195 |

**The change: for an existing residual only, allow a taker completion at a
fee-inclusive pair up to 0.98.** Benefit +$1.2129 in one window; cost −$0.2593
across the three where patience did better; net **+$0.9536** over 5 episodes.
Above 1.00 it degrades, so the plateau is real and bounded.

**1245z corroborates independently.** Its only completing route was a taker at
0.97–0.98 for about 27 seconds — above 0.96, below 0.98. A 0.98 ceiling fires
there too, against an actual residual loss of −$0.4480.

Every completion this permits is still a pair under $1. **It authorizes a
smaller profit, never a loss.**

### Consistent with L5

L5 completes as a taker **15.5%** of the time — rarely, but not never. Our rate
is **0%**. A "cross when the pair is good enough, otherwise rest" policy is
exactly that shape. (The rates are not directly comparable: his denominator is
all completions, ours would be completions-while-stranded.)

---

## 4. Where I was wrong, twice, inside this analysis

**First pass: +$1.1436.** I had not charged taker fees. With fees charged and
gated the same rule **loses $0.2107** at 0.96. The fee term is the entire reason
the answer is a specific number (0.98) rather than "use taker completion."

**Last message I led with taker-at-ENTRY** from L5's 87.2% open-crossing. That
remains an unvalidated hypothesis: it needs taker authorization at entry, a new
risk surface, and a shadow replay to price. **The completion fix is measured,
small, and fits inside the existing structure.** I am reordering my own
recommendation on the evidence.

---

## 5. Limitations, stated plainly

- **The benefit rests on 2 windows** (0130z measured, 1245z corroborating) and
  the cost on 3. One day. Remove 0130z and the change loses $0.2593.
- **Latency is not modelled.** The rule fires at 0.9714 against a 0.98 cap —
  0.9¢ per unit of slack. A λ≥0.5s recheck is required before this is trusted;
  that is what retired an earlier $1.2242 claim.
- **The book file is truncated** for the four completers (coverage ends
  T−390s…T−622s), so "never fires" for 0030z holds only over the frames I have.
- **Fee asymmetry favours the rule slightly.** `settlement_pnl` excludes fees
  entirely, while I charged the rule full taker fees — but the patient path
  would also have earned a maker rebate (θ = −0.0125, a credit) I did not net,
  so the rule's cost in the three windows is understated by roughly $0.005 each.

---

## 6. How we actually become profitable — and the honest gap to $480/day

**Even at a 100% completion rate and current size:**

> 96 windows/day × $0.2031 = **$19.50/day**. The target is **24.6× that.**

At 11.81% per window, $480/day needs **$42.33 of matched notional per window.**
We deploy **$1.72**. So these are two different problems, and conflating them is
what makes the target feel unreachable:

| step | lever | from → to | effect |
|---|---|---|---|
| 1 | **completion** (this fix) | 58% → ~90% clean | turns −$1.33/day positive |
| 2 | **frequency** | 16 authorized windows → continuous | we traded 12 of 96 available; ~6× |
| 3 | **size** | $1.72 → $42 matched/window | the only route to $480 |
| 4 | **concurrency** | 1 market → many | reduces single-window variance |

That is the operating model's own order — **PATIENCE → FREQUENCY → SIZE →
CONCURRENCY** — and the measurement says why: step 1 is the only one that
changes the *sign*. Steps 2–4 multiply whatever sign step 1 leaves. Scaling
before fixing completion multiplies a loss.

**So: profitable is reachable now, by fixing completion. $480/day is a capital
and concurrency question that should not be touched until step 1 is proven over
a few dozen windows.**

---

## 7. What I would do next, needing no authorization and no capital

1. **Latency-aware recheck** of the 0.98 rule at λ = 0.5s / 1s / 2s, against
   these same 5 episodes plus 1245z. If the slack does not survive 1s, the
   number changes and I will say so.
2. **Replay the rule over the 453 shadow windows** — turns 2 rescue
   observations into a real sample, and prices step 2 (frequency) at the same
   time.
3. Only then propose the implementation, with tests, for approval.
