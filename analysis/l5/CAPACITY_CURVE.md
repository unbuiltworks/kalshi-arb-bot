# Capacity and profit curve

**The slope through the current rung is NEGATIVE: at 83.4% completion the lane runs at $-1.84/day. Scaling multiplies unit economics, it does not repair them, so every axis of growth makes the loss bigger until completion clears break-even at 84.5%. Capacity is not the constraint and capital is not the constraint -- completion is.**

## Bankroll

| quantity | value |
|---|---|
| bankroll | $111.0 |
| mean capital in use | $1.0309 (0.93%) |
| largest single position | $19.3141 (17.4%) |

**BANKROLL DOES NOT BIND. Mean utilisation is under 1% and even the largest single position used 17% of it. More money buys nothing while concurrency is 1, and asking for money would be asking for the wrong resource.**

## Candidate supply

222 flat selection cycles

| admitted at least | cycles | share |
|---|---|---|
| 1 | 111 | 50.0% |
| 2 | 93 | 41.9% |
| 3 | 84 | 37.8% |
| 4 | 56 | 25.2% |

41.9% of flat cycles admitted two or more markets, so candidate supply would support a second slot about two cycles in five. That is a supply measurement, not a P&L one.

> while a position is held the selector does not run, so supply during held cycles was never recorded. Supply on flat cycles is not proof of supply on held ones.

## Concurrency P&L

**UNVERIFIABLE** — the frozen concurrency gate already ruled this arm unverifiable: on held cycles select_candidate is never reached, so the admissible universe a second slot would have drawn from was never recorded. Reconstructing it would be an assumption about the very thing being tested.

## Realized $/day vs completion rate

17.4154 opened legs per capital-dollar-hour; break-even at **84.52%**

| completion | $/dollar-hour | $/day | $/day if marginal pair earns half |
|---|---|---|---|
| 80.00% | -0.3057 | -7.56 | -17.43 |
| 83.42% ← observed | -0.0742 | -1.84 | -12.12 |
| 85.00% | +0.0328 | +0.81 | -9.67 |
| 86.00% | +0.1005 | +2.49 | -8.12 |
| 88.00% | +0.2358 | +5.83 | -5.02 |
| 90.00% | +0.3712 | +9.18 | -1.91 |
| 93.00% | +0.5743 | +14.21 | +2.74 |
| 95.00% | +0.7097 | +17.56 | +5.84 |

at the observed 83.42% the curve returns $-0.0742 per dollar-hour against the -$0.0721 measured directly, which is the only check available on it

> pair profit is held constant as completion rises, and the marginal completion is probably the harder one -- worse price, thinner book. Every point carries a sensitivity at half the pair profit for that reason.

## Read-only

Nothing here proposes a rung. No cap, gate, threshold or concurrency setting moves without the owner's explicit go.

## Sources

- **per_leg_economics** — src/l5_breakeven.py over 15 reconciling markets
- **capital** — src/l5_capital_efficiency.py over the same cohort
- **candidate_supply** — src/l5_allocation_metrics.selection_timeline
- **concurrency_pnl** — src/l5_concurrency_gate -- UNVERIFIABLE, not computed
