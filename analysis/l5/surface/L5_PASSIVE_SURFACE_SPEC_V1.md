# L5_PASSIVE_SURFACE_SPEC_V1

Machine-readable in `src/l5_passive_surface_shadow.py::SPEC`. Every field
carries its evidence and its confidence, and an unknown is written as an
unknown. **BTC15m / Polymarket only.**

Built from **1,142 chain-proven parent orders** (0 receipts missing) plus the
frozen historical studies. No unfilled L5 order is inferred as observed fact.

| field | value | confidence |
|---|---|---|
| **base quantity** | base lot **100 shares**; working sizes median **24–38** | HIGH on the lot, MEDIUM on the range |
| **sizing function** | **NOT dollar-scaled** — share count is the controlled quantity | **HIGH (a falsification)** |
| inventory conditioning | **weak and non-monotone** | LOW |
| simultaneous levels | **ladder, 1–3 ticks apart** | HIGH that ladders exist |
| is the 100-lot a rung? | **no** — 0.3674 of 100s sit in a ladder vs 0.3700 of all others | HIGH |
| **two-sided** | **yes, but not tick-simultaneous** — ~18 s median cadence | HIGH |
| refresh cadence | a new parent roughly every **8 s** | MEDIUM-HIGH |
| persistence | execution-lifetime **lower bound** p50 2 s, p90 6 s, max 28 s | HIGH as a bound, **NONE** as order lifetime |
| withdrawal conditions | **UNKNOWN** | **NONE** |
| TTE dependence | matched economics concentrate late (≤60 s: 258.7 → 649.3 bp/$) | MEDIUM, historical, **not a gate** |
| reversal relationship | reversals, not drift, generate pairs | MEDIUM |
| recycling clock | **60–300 s**, not 5–30 s | LOW (n=6) and **censored** by a 22.2 s feed lag |

### The two discriminations that decided the sizing question

**Probability-scaled notional is FALSIFIED.** `cv(executed_qty) = 0.797`
against `cv(notional) = 1.212`. A dollar-sizing rule would make notional the
*tighter* quantity; it is **52% more dispersed**. He controls share count.

**The 100-lot is not a ladder rung.** 0.3674 of 100-lots appear in a ladder
against 0.3700 of every other size — indistinguishable. Ladders are built from
all sizes; 100 is a *lot*, used **2.3× more often at p > 0.90 (0.301) than at
0.70–0.90 (0.131)** and more at high inventory (0.218 at >400 vs 0.039 at ≤25).
The honest reading is a **mixture**: a real base lot whose *frequency* is
regime-dependent, sitting on a separate working-size distribution.

### Two-sidedness, quantified

1,015 opposite-outcome parent pairs: **3.8% within 1 s, 20.2% within 5 s,
44.1% within 15 s, 64.0% within 30 s, 79.9% within 60 s**, median gap **18 s**.
The median implied pair is **0.99 at every horizon**, and the share below 1.00
**falls with the gap** — 0.769 same-second → 0.644 at 5 s → 0.565 at 60 s.

**The tighter the two sides are in time, the cheaper the pair.** That is the
strongest argument in the spec for *simultaneous* placement rather than
sequential completion.

### What the spec cannot say

Unfilled and cancelled orders are **`UNOBSERVABLE_OR_UNRESOLVED`** in every
source held here. Every parent measured is one that *executed*, so the ladder's
full width, the levels that never traded, and when any order was submitted or
pulled are outside the evidence. `withdrawal_conditions` is `UNKNOWN` with
confidence `NONE` rather than guessed.

---

# L5_PASSIVE_SURFACE_SHADOW_V1 — status: built, running, **no verdict yet**

`src/l5_passive_surface_shadow.py`. Read-only, zero orders.

**It decides before it sees.** Each arm builds its surface from the book
snapshot at decision time *t*; every fact used to judge it comes from *t′ > t*.
Nothing reacts to the activity feed and then calls itself a prediction.

**One shared observation stream by construction** — all four arms are evaluated
on the same `book_full_joint` and `print_tape` rows in the same order.
**Placement is the only axis that varies**; inventory policy is held at
FIDELITY for every arm.

Arms: **A** inferred L5 surface (ladder 1–3 ticks) · **B** `nearest_maker` ·
**C** `one_behind` · **D** `pair_target` (frozen control).

Six states never collapsed: `RESTABLE` → `BOOK_REACHED` → `TRADE_FLOW` →
`EVIDENCED_FILL` (= `VOLUME_AHEAD_CLEARED`) → `SETTLED` → `REALIZED`. Only the
fourth moves inventory, and the last two are unreachable in a shadow and stay
`false`.

### First run: 4 markets, 300 quotes — and why it says nothing yet

| arm | quotes | restable | post-only rejected | book reached | trade flow | evidenced fill |
|---|---|---|---|---|---|---|
| inferred L5 surface | 150 | 1.00 | 0.00 | 0.693 | 0.000 | 0.000 |
| nearest_maker | 52 | 1.00 | 0.00 | 0.808 | 0.000 | 0.000 |
| one_behind | 48 | 1.00 | 0.00 | 0.625 | 0.000 | 0.000 |
| pair_target | 50 | 1.00 | 0.00 | 0.640 | 0.000 | 0.000 |

**The zero trade-flow is the market, not a bug.** Token mapping was verified at
**100% matched, zero unmatched**, and 127 forward prints exist. The windows the
recorder caught were in their **final minutes**, where the book has already gone
**one-sided**: touch `0.99 / None` on the winning outcome — *no asks at all* —
and `None / 0.01` on the losing one. A two-sided surface cannot exist there, and
every forward print landed at 0.97–0.999, far above any bid we could legally
rest.

**That is the same condition that stranded the live probe's residual** (49 bid
levels, `offers: []`). It is a finding in its own right: **the last minutes of a
BTC15m window are not a passive-surface habitat**, whatever the rest of the
window looks like.

**A verdict needs tape covering whole windows from their open.** The recorder
runs 95 minutes and will capture roughly six complete BTC15m windows. The
comparison re-runs against that, unchanged.
