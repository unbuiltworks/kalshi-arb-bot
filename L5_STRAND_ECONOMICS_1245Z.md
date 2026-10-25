# L5 — why strands erase pair profits: 1245z + 0130z, and what the evidence actually supports

Read-only. **The live campaign was not touched, modified, or redeployed.**

---

## 0. Two corrections to my own earlier reporting

**1. The pre-send crossing guard is LIVE.** I repeatedly described it as
"preserved and still undeployed". Wrong: `9d47ff60` is an ancestor of the
deployed `1752e51b`, so it has been in force since 12:01:30Z. It fired **8
times in 1245z alone** (7 DOWN, 1 UP).

**2. The EV per opening is NEGATIVE, not "positive but thin".** My script
printed the optimistic label before the number was computed. The figure is
**−$0.0168**. Corrected below.

---

## 1. The core economics — this is the whole problem

Every **filled opening leg** across campaign 1 and 1245z:

| | |
|---|---|
| filled openings | **10** |
| completed | 8 |
| **stranded** | **2 (20%)** |
| mean pair gain | **+$0.1799** |
| mean strand loss | **−$0.8037** |
| total | +$1.4390 gain, −$1.6074 loss, **net −$0.1684** |

```
EV per filled opening = 0.8 x 0.1799 - 0.2 x 0.8037 = -$0.0168
breakeven strand rate = 0.1799 / (0.1799 + 0.8037) = 18.3%
observed strand rate                                 20.0%
                                         -> 1.7 points the WRONG side
```

**The asymmetry is 4.5 : 1.** A strand loses its *whole notional* because the
leg settles worthless. A completed pair earns only `(1 − pair) x qty`, i.e.
0.11–0.28 per contract. So we are marginally EV-negative per opening, and the
answer to *"do additional openings improve or reduce expected net profit?"* is:

> **At the current strand rate they reduce it — by about 1.7 cents per
> opening.** Not catastrophically, but the sign is negative.

**Critically, the $0.80 cap cannot fix this.** Halving it to $0.40 halves gains
and losses together: EV goes −$0.0168 → −$0.0084. Same sign. The cap bounds the
*variance*, not the edge. That is a real limitation of the change I recommended
and got approved.

## 2. 1245z forensics

5 orders, 3 fills, 1 pair, 1 strand. Settled **−$0.294**
(`realized_today` −11.4957 → −11.78970000, delta reconciles).

| time | order | side | result |
|---|---|---|---|
| 12:46:34 | `87cd0c93`+`982cb856` | pair placed | — |
| 12:46:47 | `982cb856` | UP | **FILLED 1.4 @ own 0.57** |
| 12:46:52 | `87cd0c93` | DOWN | cancelled, cum 0 |
| 12:47:42 | `784b5389` | DOWN | **FILLED 1.4 @ own 0.26** → pair complete |
| 12:48:29 | `e123c579` | DOWN | **FILLED 1.4 @ own 0.38** → round 2 opens |
| 12:50:44 | `412c851a` | UP | cancelled unfilled → **stranded** |

Settlement: `matched 1.4 pnl +0.154` · `residual DOWN 1.4 cost 0.448 pnl
−0.448` · `settlement_pnl −0.294`. Resolved UP, so the DOWN residual was worthless.

**The cap bound exactly**: 1.4 = ⌊0.80 / 0.57⌋, and 1300z's clip is
1.26 = ⌊0.80 / 0.63⌋. Both are the notional cap, not the contract ceiling.

Gate tally for the window: `MAX_LIVE_ORDERS` 12 · `PAST_ENTRY_CUTOFF` 19 ·
`PRESEND_CROSS` 8 · `BOOK_UNCERTAIN` 3 · `TOO_LATE_TO_WITHDRAW` 1 ·
`ORDER:SUBMITTED` 5.

**After 12:50:44 no complement order was ever placed again** — 9 minutes and
106 residual frames, with ~27 REPLACE tickets free. Same shape as 0130z.

## 3. Policy 3 (selective taker complement) would have fired ZERO times

106 residual frames, 1245z, complement = UP, residual DOWN 1.4 @ own 0.32:

| time | taker complement | pair if completed | under 0.96 cap? |
|---|---|---|---|
| 12:48:32 | 0.66 | **0.98** | refused |
| 12:48:38 | 0.65 | **0.97** ← best all window | refused |
| 12:49:06 | 0.73 | 1.05 | refused |
| 12:50:40 | 0.83 | 1.15 | refused |
| 12:52:50 | 0.68 | 1.00 | refused |
| 12:56:49 | 0.96 | 1.28 | refused |
| 12:58:32+ | — | **ask side empty** | n/a |

**The complement was never achievable at ≤0.96 in any frame.** Displayed depth
was never the constraint (`insufficient_displayed_depth: false`, 1.4 against
1,000+ contracts, one level, until the book emptied at 12:58:32).

So taker completion cannot rescue 1245z *without breaching the 0.96 ceiling*.
At a ≤1.00 gate the best net was **+$0.0220** in a ~28-second window
(12:48:32–12:49:00).

## 4. The intuitive fix would have killed the WINNER

The obvious rule — *"don't open unless the complement is already affordable"* —
fails on its first test.

| | round 1 (profitable) | round 2 (stranded) |
|---|---|---|
| complement cost 1s after the opening filled | **pair 1.04** | pair 0.98 |
| 45s later | **0.86** | 1.10 |
| outcome | **completed at 0.83, +$0.154** | **never completed, −$0.448** |

**Round 1 started in a worse state than round 2 and ended profitable.**
Complement affordability at the moment of the opening fill does **not** predict
completion. n=2, but it is a direct counterexample, and it rules out the
cheapest-looking fix.

What actually separated them: the market trended UP through both. Round 1 held
UP (favourable), round 2 held DOWN (adverse). That discriminator is directional
prediction, which `L5_OPERATING_MODEL.md` says we have no demonstrated skill at.

## 5. Out-of-sample: does L5 open while holding unmatched inventory?

**Yes — it is his dominant mode.** 662,116 buy rows, 5,503 BTC15m markets:

| inventory state at the buy | count | share |
|---|---|---|
| from flat | 5,958 | **0.9%** |
| **increased an existing imbalance** | 348,544 | **52.6%** |
| of which same-side EXTEND | 332,353 | |
| reduced an imbalance | 307,614 | 46.5% |

Median imbalance **already held** when he added to it: **99.09 contracts**
(p90 307, max 1,736). **5,412 of 5,503 markets saw 10+ buys** — roughly 120
actions per market.

**So policy 2 (completion-first) is not L5's policy; he does the opposite.**
But the scale is not comparable: he carries a 99-contract working inventory
across ~120 actions, continuously rebalancing. We carried 1.4 contracts across
**5 actions** and then stopped. His residual is inventory being worked; ours is
a position abandoned mid-window.

## 6. Policy comparison

| | completed pairs | strands | net | notes |
|---|---|---|---|---|
| **1. current** (realized) | 1 in 1245z, 7 in c1 | 2 | **−$0.1684** over both | EV/opening −$0.0168 |
| **2. completion-first** | unchanged | unchanged | **no change on this evidence** | round 2 was opened *from flat* — round 1 had already completed at 12:47:42 — so a "no opening while imbalanced" rule never triggers. It cannot prevent either strand. |
| **3. + selective taker** | unchanged | unchanged | **no change at ≤0.96** (zero fires); **+$0.42 in 1245z at ≤1.00**, 28-second window | needs the cap breached; 0130z's own window was 6.4s at ≤0.96 and unreachable at ≥0.5s latency |

Policy 2 deserves emphasis because it sounds right and does nothing: **at
12:48:08, when round 2 was placed, inventory was balanced.** Completion-first
had nothing to prioritise.

## 7. The single change the evidence supports

**Raise the ceiling on COMPLETING a residual above the ceiling on OPENING a
pair.** One number, applied only to risk-reducing legs.

The reasoning is the asymmetry in §1. The 0.96 cap exists to protect pair
quality at *entry*. Applied at *exit* it is doing the opposite of its purpose:
when it refuses a 0.98 completion, the alternative is not a cheaper completion
later — on this evidence it is a **total loss of the notional**.

| | 1245z |
|---|---|
| complete at 0.98 (available 12:48:32) | 1.4 × −0.02 = **−$0.028** |
| realized: refuse, carry to expiry | **−$0.448** |
| **difference** | **+$0.420** |

0130z is consistent: its complement *did* reach 0.95–0.96, inside the cap, so
there the blocker was the maker-only re-price rule rather than the ceiling —
two different failures, one shared consequence.

**Why this and not sizing:** sizing scales both sides and leaves EV negative.
This changes the loss-given-strand from ~0.45/contract to ~0.02/contract
without touching the gain, which is the only lever that moves the 4.5:1 ratio
without requiring prediction.

**What it costs and needs — stated plainly:**

- At the prices seen, a 0.97–1.00 completion was a **taker** price. As a maker
  quote two ticks behind it was ~0.96, i.e. exactly at the current cap. So this
  likely requires **taker complements for risk reduction only** — which you
  have forbidden, and I am **not** implementing or proposing to enable.
- It is justified on **n=2 strands**. The 20% strand rate has a wide interval
  on 10 openings; breakeven is 18.3%, so the sign of the whole argument rests
  on a difference of 1.7 points.
- It would have to be bounded: a completion ceiling of 1.00 caps the loss at
  zero-minus-fees per contract, but a ceiling above 1.00 would book guaranteed
  losses to avoid probabilistic ones.

**What I am NOT recommending:** more openings (EV-negative), a lower notional
cap (does not change the sign), completion-first (never triggers on this
evidence), or taker execution under current authorization.

**Honest bottom line:** the campaign is running inside every limit, and the
measured per-opening edge is currently **negative by 1.7 cents**. The cap is
doing what it was specified to do — bounding each strand to under $0.80 — but
bounding variance does not create edge. If the remaining six windows hold the
20% strand rate, I expect the campaign to finish slightly negative, and the
decision worth making then is about the completion ceiling, not about size.
