# Why a pair planned at 0.95 completes near 0.99

**Verdict: it is the structure of the trade, not the execution, and no
maker-only repricing fixes it. NO PROMOTION for continuous multi-round
trading: every additional round has negative expected settled value.**

Reproduce with `PYTHONPATH=. python3 analysis/l5/pair_completion_replay.py`.
Tests: `tests/test_l5_pair_completion_replay.py`. Offline; nothing under
`src/` imports it and no configuration changed.

## The mechanism, in one sentence

A resting leg fills when the market trades **through** it, so it is already
under water at the fill, and the other leg is then only reachable near
redemption.

Measured on 488 first fills: the filled leg is **4.5 cents** below its cost
at the print that fills it (median 3.0, p90 9.0), and there is no drift
afterwards to win it back (+0.2 cents at 60 s, +0.9 cents to settlement).
A pair is two maker fills. The plan pays 5 cents for both.

## What the replay reproduces

526 settled BTC15m windows (2026-03-19 to 2026-08-31), L5's Polymarket price
path and the venue's settlement, PMUS ticks, rules and fees, one contract
per round, a fill only when the path passes one tick beyond the order.

| | replay, deployed rule | live, this engine |
|---|---|---|
| median completed pair cost | **0.99** | 0.99, 0.99, 0.987 |
| pairs completed at plan (<= 0.955) | **7%** | 0 of 3 |
| first fills left stranded | **25.4%** | 3 of 11 (27%) |
| stranded leg, per contract | -0.333 | -0.44 (1530z); -0.169 over 33 own settled legs |
| **net per first fill** | **-0.073** | -0.056 settlement-backed |

The shadow controller on live PMUS books, 956 modelled fills over 475
windows, measured **-0.083 per filled contract with 92% of fills adverse**
(`ADVERSE_SELECTION_MEASUREMENT_20261024.md`). Three sources, one sign.

## Can inventory-aware repricing improve it?

The only thing a maker-only controller can change after a first fill is the
most it will pay to complete. Every rule tried lands in the same place:

| completion rule | strand rate | median pair | net / window | 95% CI |
|---|---|---|---|---|
| up to 0.99 at once (deployed) | 25.4% | 0.99 | -0.0681 | [-0.0867, -0.0494] |
| never above 0.98 | 27.0% | 0.98 | -0.0675 | [-0.0869, -0.0482] |
| never above 0.97 | 28.7% | 0.97 | -0.0690 | [-0.0890, -0.0489] |
| never above 0.96 (patient) | 30.1% | 0.96 | -0.0660 | [-0.0868, -0.0452] |
| 0.96 for 30 s, then 0.99 | 27.3% | 0.99 | -0.0697 | [-0.0892, -0.0503] |
| 0.96 for 120 s, then 0.99 | 27.7% | 0.96 | -0.0648 | [-0.0846, -0.0451] |
| 0.99 only once the held leg is 3 ticks under | 27.0% | 0.99 | -0.0678 | [-0.0873, -0.0483] |
| 0.99 only once the held leg is 6 ticks under | 27.7% | 0.96 | -0.0667 | [-0.0865, -0.0470] |

**A lower pair cost is bought one-for-one with more stranded legs.** The
intervals overlap completely and none reaches zero. That is what a fair
price path predicts: completing at the touch and holding have the same
expectation, so a ceiling moves variance, not the mean. It holds in sample
(-0.071) and out of sample (-0.064).

## What else was tried

| change | net / first fill |
|---|---|
| opening 1 / 2 / 3 / 4 ticks behind the touch | -0.078 / -0.073 / -0.078 / -0.083 |
| fill model: touch counts / one tick beyond / two beyond | -0.060 / -0.073 / -0.093 |
| only near the money (both legs >= 0.45) | -0.063 |
| enter only 5-120 s / 120-300 s / 300-480 s | -0.073 / -0.083 / -0.090 |
| order TTL 30 / 60 / 300 s | -0.074 / -0.072 / -0.075 |
| completion cutoff 300 / 840 s | -0.072 / -0.077 |
| March / April / August | -0.068 / -0.074 / -0.074 |

Nothing changes the sign. On the same windows L5 himself spent $571,096 and
redeemed $570,660: **-0.08% per dollar** before incentives.

## More rounds

| rounds allowed per window | first fills / window | net / window | 95% CI |
|---|---|---|---|
| 1 | 0.93 | -0.0681 | [-0.0867, -0.0494] |
| 2 | 1.51 | -0.1140 | [-0.1377, -0.0903] |
| 4 | 2.04 | -0.1584 | [-0.1849, -0.1320] |
| 8 | 2.16 | -0.1744 | [-0.2010, -0.1479] |

Per one contract per round. The loss scales with the number of first fills.

## What this cannot say

It is Polymarket's path, not PMUS's. The path is L5's own prints and is
silent between them. Queue position is unmodelled beyond the trade-through
rule. It ranks rules against each other on identical paths; it is not a
forecast of our fills. What makes it worth acting on is that our own venue
record and the PMUS shadow measurement agree with it.

## What was changed in the engine because of it

`reopen_decision` priced a further round with the **planned** pair edge
(0.05) and L5's residual figure (-0.0236), which made a second round look
worth +0.03 a contract. It now also requires the ledger's own venue-settled
record -- net per contract that took a first fill, over the last 50 settled
windows -- to clear the same floor, once that record holds 20 windows. It can
only refuse. The first round of a window is still the owner's decision and
is not gated by it.
