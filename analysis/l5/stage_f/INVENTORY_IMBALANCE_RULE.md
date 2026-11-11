# Is L5's dynamic inventory imbalance a source of profitability?

**Answer: no. It is his default behaviour, it generates no matched volume, and
it converts into directional residual.**

**One rule recommended: COMPLETION PRIORITY** — while unmatched inventory
exists in the live market, the next maker order must be on the **lighter**
side. No new opening pair rests until the book is balanced within tolerance.

Read-only. The active campaign was not touched and no risk was changed.

---

## 1. When does L5 add to an already-heavy side?

**Essentially always, and that is the finding.** `analysis/l5/underwater/`,
2,201 episodes, **71,847 decision points**, every feature from fills already
seen:

| gain bucket | n | **EXTEND** | SWITCH | HOLD | residual p50 |
|---|---|---|---|---|---|
| **< −20c** | 10,637 | **69.2%** | 27.1% | 3.7% | **192.7** |
| −20..−10c | 12,514 | 69.6% | 28.3% | 2.1% | 143.0 |
| −10..−5c | 6,875 | 68.7% | 29.6% | 1.7% | 112.2 |
| −5..0c | 8,117 | 65.7% | 31.5% | 2.8% | 82.2 |
| 0..+5c | 11,559 | 62.9% | 31.8% | 5.3% | **52.9** |
| > +5c | 21,918 | 65.9% | 31.5% | 2.6% | 118.3 |

EXTEND runs **62.9%–69.6% at every gain level**. A behaviour that fires
two-thirds of the time regardless of state is **a default, not a signal** —
there is no trigger to reconstruct. He almost never abandons (HOLD ≤ 5.3%),
and when underwater he carries **3.6× the residual** (192.7 vs 52.9).

## 2. What does the action follow?

`analysis/l5/switch_trigger/`, 1,652 decisions with a defined complement (656
switches, 39.7%), 1,484 of them BTC15m, every feature as-of the instant before
the fill, chronological split:

| feature | OOS AUC |
|---|---|
| **pair_cost** | **0.6264** |
| complement_price | 0.5827 |
| rel_imbalance | 0.5747 |
| tte_s | 0.5545 |
| d_complement (book change) | 0.5508 |
| since_prior_fill_s | 0.5119 |
| **abs_residual** | **0.5025 — no information** |

**Of your four candidates: favorable entry price, weakly. Price movement,
time remaining and order-book change — all ≈0.55, barely above chance.**

Two results worth more than the table. **Inventory carries no information at
all** (0.5025): "the position does not appear to demand the switch." And the
7-term interaction model **degrades** OOS AUC from 0.6264 to **0.528** — a
more expressive model losing to one variable is evidence the interactions are
noise. Nothing beats the majority baseline on accuracy (0.5746 / 0.5504 vs
**0.5847**).

**So there is no L5 inventory rule to copy.** Whatever selects his side is
dominated by persistence — same-side 69.2% over 61,493 consecutive pairs,
while "repairs imbalance" is 48.3%, a coin flip.

## 3. Does the switch reduce risk or capture profit?

**Capture, weakly — not risk.** `pair_cost` is the only feature that
separates, so he switches when the attainable pair is cheap. `abs_residual` at
0.5025 says the switch is **not** inventory management. Risk reduction is not
what the switch is doing.

## 4. How do profitable and unprofitable windows differ?

`analysis/l5/inventory_trajectory/`, 527 BTC15m windows, 62,020 fills. The
trajectory is the mechanism:

| % of window's fills | median pair cost | median imbalance |
|---|---|---|
| 10% | 0.9968 | **0.359** |
| 50% | 0.9845 | 0.103 |
| 90% | 0.9655 | 0.083 |
| **100%** | **0.9603** | 0.098 |

**L5 never sells** — 66,949 BTC15m trades, every one a BUY. So the only way his
pair VWAP can improve is by buying *more, cheaper*. Paired within the same
windows (n=290 with ≥100 fills): median 0.9829 at fill 100 → **0.9599** final,
median delta **−0.0177**, improved in **75.2%**.

**His 96¢ is emergent, not acquired.** At first pair formation the median pair
is **0.9900**, at fill #3, ~49s in — and only **27.9%** of his own entries
clear our 0.96 ceiling.

**The decisive comparison, and it cuts against importing his method.** His
averaging-down needs ~100 fills per window to reach 0.9603. **Our simultaneous
two-sided quote reached pair cost 0.9500 at entry in today's window 1** — the
spread identity `1 − spread − 2·d·t` delivers at entry what costs him a whole
window of accumulation. **We do not need his averaging-down; we already have
its endpoint.** What we lack is volume, and §5 shows EXTEND does not supply it.

## 5. Does bounded adaptive imbalance beat forced balancing?

**No, and this was already tested to destruction.**
`analysis/l5/extend_preserve/` — **VERDICT: KILL THE HYPOTHESIS.** Preserving a
model-selected EXTEND instead of converting it to COMPLETE:

> **extra matched contracts = 0.00, matched-pair P&L delta = $0.0000.**
> Two independent studies, on two fill rules each, all agree.

Its apparent **+$1.5994** was one market's held side winning a coin flip; a
second settled at **−$0.3944**. "It buys more of the side we already hold, that
inventory never becomes matched, and it settles as directional residual."

And the residual-cap economics, fee-inclusive and OOS
(`PROD_VS_CAPS`, BTC15m): uncapped exact-L5 is matched +$89,028, residual
−$90,452, **net −$1,424 (−4 bp/$)**; cap 6 **+35 bp/$**; cap 35 **+45 bp/$**.

**Adaptive imbalance adds zero matched volume and all of the variance.**

## 6. The one rule: COMPLETION PRIORITY

> **While unmatched inventory exists in the live market, the next maker order
> must be on the lighter side. No new opening pair may rest until |residual|
> is within tolerance.**

**Why this rule and not L5's:**

| evidence | bearing |
|---|---|
| EXTEND → 0.00 extra matched, $0.0000 matched P&L | adding to the heavy side buys nothing |
| EXTEND fires 62.9–69.6% at every gain level | it is a default, so there is no state-aware version to tune |
| `abs_residual` AUC 0.5025 | L5 offers no inventory rule to copy; we must decide on our own economics |
| residual = 72.5% of Stage B2's headline; −$0.898 of window 1's −$0.842 | residual is where our P&L variance lives |
| uncapped L5 −4 bp/$ vs capped +35/+45 bp/$ | bounding residual is what makes the policy pay |

**It is bounded and adaptive in the only direction the evidence supports** —
adaptive about *which side* to quote, bounded by the existing 3-contract cap,
and it never requires a view on which outcome wins.

**One piece of counter-evidence I will not hide.** In today's window 1 the dear
completion — pair 2 at **1.0400** for **−$0.090** — was the right trade. Without
it the residual would have been **2.14 + 2.25 = 4.39 UP**, breaching the
3-contract cap, and the loss far larger than $0.842. **So completing even at a
certain small loss beat not completing**, which is the same direction as this
rule and is why the rule governs *openings*, never completions. A ceiling
applied to completions would have made window 1 worse.

## 7. After the campaign settles

I will test the rule against the campaign's own losing residuals: for each, whether
the residual arose from an opening that rested while unmatched inventory
already existed — in which case the rule would have prevented it — or from a
simultaneous pair that half-filled, in which case it would not, and the
residual is the irreducible cost of maker uncertainty. **Window 1's up_qty 6.47
against dn_qty 4.33 is exactly the case to examine.** I will not claim the rule
would have helped without that check.

**No live policy change and no risk increase is proposed.** This needs fresh
authorization and a bounded test of its own.
