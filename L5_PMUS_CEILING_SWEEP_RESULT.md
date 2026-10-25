# PMUS BTC15m: the first positive out-of-sample result in this thread — and it is weak

**VENUE: PMUS**, `cpc-btc-updown-15m`, venue-native terminal-print labels.
Read-only. Nothing deployed, no order, no limit changed, $90 probe unarmed,
`l5-pmus-live` untouched.

10 out-of-sample windows, 60 seeds per cell, chronological split. **Read the
caveats in §4 before quoting any number here.**

---

## 1. Result

Net is in dollars over the 10 OOS windows. `net_pre` excludes the maker rebate;
`net+rbt` adds the cent-rounded credit the 25 observed credits establish is paid.

| ceiling | clip | break-even | matched windows | completions | **net_pre** | sd | **worst seed** | net+rbt |
|---|---|---|---|---|---|---|---|---|
| 0.960 | 1.56 | 1.0000 | 7.9 | 7.9 | +0.8315 | 0.695 | **−0.5928** | +0.8315 |
| **0.990** | 1.56 | 1.0000 | **8.9** | 8.9 | **+0.9212** | 0.528 | −0.1560 | +0.9212 |
| 1.004 | 1.56 | 1.0000 | 8.9 | 8.9 | +0.8367 | 0.492 | −0.0468 | +0.8367 |
| 0.960 | 5.00 | 1.0080 | 7.9 | 7.9 | +2.6650 | 2.228 | **−1.9000** | +2.9470 |
| **0.990** | 5.00 | 1.0080 | **8.9** | 8.9 | **+2.9525** | 1.691 | −0.5000 | +3.2520 |
| 1.004 | 5.00 | 1.0080 | 8.9 | 8.9 | +2.6817 | 1.575 | −0.1500 | +2.9792 |

**0.99 is the optimum at every clip**, and the ordering is identical across all
three clips — 0.96 worse, 1.004 worse, 1.008 identical to 1.004.

## 2. The consistency check failed, and the failure was informative

The peer session pre-registered: at clip 1.56 break-even is exactly 1.0000, so
the 1.004 and 1.008 arms must come back negative or the model is wrong. **They
came back positive.** The diagnosis is the useful part:

**A ceiling is a PERMISSION, not a PRICE.** The market offers a pair at
`1 − spread`, and **0 of 228 OOS frames offer one above 1.0000** (max 0.9900).
So a ceiling of 1.004 permits something never on offer; a cell at that ceiling
*completes at 0.99* and is positive for that reason. The check tested the cell
when the quantity it meant to test was the **marginal** effect of raising the
ceiling past break-even.

Restated marginally, the model passes exactly as predicted:

| clip | 0.96 → 0.99 | 0.99 → 1.004 | 1.004 → 1.008 |
|---|---|---|---|
| 1.56 | **+0.0897** | **−0.0845** | 0.0000 |
| 2.50 | **+0.1438** | **−0.1354** | 0.0000 |
| 5.00 | **+0.2875** | **−0.2708** | 0.0000 |

Loosening **into** the offered range pays; loosening **past break-even** costs;
loosening beyond what the market offers is exactly inert. All three signs are
right at all three clips.

Why 0.99 → 1.004 hurts at all, when the market never offers above 0.99: the
completion test is `held + own_complement ≤ ceiling`, and `held` is the
**realized** first-leg cost, not the current touch. Raising the ceiling permits
completing windows whose held leg filled expensively — precisely the marginal
ones. The negative sign is real and correctly located.

## 3. What this confirms

`L5_PMUS_COMPLETION_CAP_FINDING.md` argued the 0.96 cap, not the market, is why
windows end unmatched. That now has an economic number attached: moving
0.96 → 0.99 raises matched windows **7.9 → 8.9 of 10** and net by **+$0.09 to
+$0.29** depending on clip. And it locates the right value — 0.99, the modal
offered pair cost — rather than "higher is better".

It also closes the loop on the decomposition in `L5_BTC15M_ADVERSE_DECOMPOSITION.md`:
the loss was ~91 % residual settlement, completion removes residual, and
permitting completion is worth money. The three findings are consistent.

## 4. Why this is weak, stated plainly

- **n = 10 OOS windows**, one ~7-hour slice of 2026-10-25. That is not a sample
  from which to run money.
- **The spread of outcomes swamps the mean.** At the best cell, sd is $1.691
  against a mean of $2.9525 — 57 % — and the **worst seed is −$0.5000**. At
  0.96/clip 5 the worst seed is −$1.9000.
- **Fills are drawn, not observed.** P(fill) 0.4325/leg is measured on 965 real
  orders, but which orders fill here is a Bernoulli draw, and the PMUS tape
  carries no prints so no queue is modelled.
- **Capacity still does not reach the target.** $0.295/window at clip 5 is about
  **$28/day** on one series at 96 windows/day. Reaching $480/day needs ~17× more
  — through clip, series or concurrency — and clip 5 already puts a pair at ~$5,
  which is the entire currently authorized aggregate exposure. **This is a
  better gap than the three-to-four orders of magnitude in
  `L5_VENUE_SELECTION_RANKING.md`, and it is still a gap requiring authorization
  increases this evidence does not justify.**

**Status: suggestive, not established.** It is the first positive OOS result in
this thread, the sign is stable across three clips, and the mechanism is
explained and checked. It is not a basis for changing a risk control.

## 5. What would establish it

More OOS windows, from the existing collectors, at the same venue — nothing new
to build. The 0.99 cell should be re-run as windows accumulate, and the decision
rule fixed in advance: adopt only if net stays positive with a worst seed above
zero on a materially larger sample.

**I am not proposing a cap change.** `TS.max_pair_cost` is a risk control on a
money-capable service another session owns, and the owner decides. This is
evidence handed over, not a change made.
