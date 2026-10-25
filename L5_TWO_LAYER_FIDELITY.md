# Two-layer comparison: policy fidelity is 10.82%, and the gap is ENTRY SELECTION

Supersedes `L5_FIDELITY_CAP_COMPARISON.md` wherever they disagree. 527 windows, 62,020 L5
decisions, chronological. PMUS both sides. Fees from `src/pmus_fees.py` only. Settlement
recovered from the `REDEEM` rows in `data/l5/raw/activity.jsonl` (517 of 527 windows).

Scripts: `analysis/l5/fidelity_cap/two_layer.py`, `analysis/l5/fidelity_cap/extend_econ.py`.

## 0. The flaw that is now fixed

My previous comparison fed **L5's** inventory into **our** gates and reported that our
5-contract limit refused his 117-contract residual. That is partly tautological: his
inventory is the product of his earlier decisions and our engine would never have reached it.

**Here our book starts flat in every window and advances only through actions our own policy
would have taken and that would realistically have filled.** The consequences are large:

| | conflated (old) | two-layer (correct) |
|---|---|---|
| fidelity | **0.04%** | **10.82%** |
| `MAX_UNMATCHED_CONTRACTS` refusals | 44,202 = 71.27% | **never fires** |

**The "71.27% refused by MAX_UNMATCHED" was entirely an artifact of importing his inventory.**
On our own reconstructed book that gate does not bind at all, because our policy never
extends, so we never accumulate.

## 1. The capital-scale confound, removed — and it inverts the risk story

Measured, not assumed: L5's median peak capital per window is **$937.68** (p10 $49, p90
$2,239) against our **$5.00** aggregate — **187.5x**.

| normalized to deployable capital | L5 | ours | |
|---|---|---|---|
| clip size (median) | **0.2514%** | **16.00%** | we are **64x LARGER** |
| unmatched residual (median) | **3.53%** | **50.0%** | we are **14.2x LOOSER** |

**So `MAX_UNMATCHED` is not "too tight".** Relative to bankroll our residual tolerance is 14x
more permissive than L5's. What is wrong is the **clip**: at 16% of capital per order we hit
any sane inventory limit after ~3 fills, which is why we run ~4.5 fills a sequence against his
119 median. My earlier "the limit is 23x too tight" was the confound talking.

## 2. LAYER 1 — policy fidelity, before risk limits

**6,711 of 62,020 = 10.82% agreement.** Disagreements by cause:

| cause | count | share |
|---|---|---|
| **EXTENSION — we never extend** | **31,001** | **49.99%** |
| COMPLETION refused by the 0.96 price ceiling | 11,167 | 18.01% |
| TIMING (entry window / complete cutoff) | 5,841 | 9.42% |
| we wanted COMPLETION, he EXTENDED | 5,619 | 9.06% |
| we wanted OPENING, he EXTENDED | 1,040 | 1.68% |
| we wanted OPENING, he COMPLETED | 579 | 0.93% |
| PRICE below our entry floor | 62 | 0.10% |

**THE SINGLE LARGEST ACTIONABLE POLICY MISMATCH: we never EXTEND.** Taken with the related
row ("we wanted COMPLETION, he EXTENDED") the extension divergence is **~59% of all his
decisions**. Our policy always prefers completing the short side and treats a same-side add as
refused.

This contradicts our own operating model. `CLAUDE.md`: *"Same-side `EXTEND` is a first-class
action, not an error to flatten immediately."* The deployed policy does the opposite.

## 3. LAYER 2 — executability on our own book

| | Arm A (policy today) | Arm B (EXTEND permitted) |
|---|---|---|
| filled | 876 | 2,346 |
| no fill at P=0.4325 | 1,179 | 3,181 |
| `OPENING_LEG_MAX_CONTRACTS` | 360 | **17,412** |
| `OPENING_NOTIONAL_BUDGET` | 576 | 4,131 |
| `MAX_CASH_DEPLOYED` | — | **8,963** |
| `MAX_UNMATCHED_CONTRACTS` | **never** | 1,523 |
| `PAIR_CEILING` | 11,665 | 12,126 |

Note what binds once extension is allowed: **clip-size and cash gates**, not the unmatched
limit. Again the clip, not the inventory ceiling.

## 4. THE ECONOMIC CONSEQUENCE of the leading mismatch

12 seeds, real settlement, deployed gates and ceiling unchanged in both arms:

| | net mean | sd | worst seed | best | pairs | stranded |
|---|---|---|---|---|---|---|
| **A — no extend (today)** | **+$12.6492** | 6.0661 | **+$2.9408** | +$22.34 | 535 | 134 (27.5%) |
| **B — extend permitted** | **+$15.2234** | 8.2979 | **−$0.0659** | +$30.86 | 1,424 | 259 (53.2%) |

**Consequence of permitting EXTEND: +$2.5742 over 517 windows = +$0.0050/window.**

That is **0.42 sd** of Arm A's own dispersion — indistinguishable from zero. And it **doubles
the strand rate (27.5% -> 53.2%)** and turns the worst seed negative (+$2.94 -> −$0.07).

**So the largest mismatch by frequency is economically negligible, and buying it costs
worst-case robustness.** Frequency and consequence point in different directions, which is
exactly why the owner asked for the consequence.

## 5. THE FINDING THAT MATTERS — and the confound that produces it

Arm A is **positive in all 12 seeds**. It must not be read as "our strategy is profitable":

| | simulation (Arm A) | LIVE |
|---|---|---|
| net per traded window | **+$0.0245** | **−$0.1553** |
| strand rate | **27.5%** | **28-33% measured** |

**The strand rate matches live. The P&L does not** — opposite sign, a swing of $0.1798/window.

The reason is structural and disqualifying: **the simulation only ever acts at L5's own
timestamps and prices.** It inherits his entry selection for free. Live, we choose our own
moments. So +$12.65 is evidence that **our rules are profitable on his selection**, which we
cannot obtain — not that our strategy works.

**And that localizes the gap precisely.** Same inventory mechanics, same strand rate, opposite
sign. **The difference is which moments we enter, not how we manage inventory afterwards.**
Extension, completion ceiling, timing and inventory limits are all downstream of a selection
we do not have. This is the same conclusion the residual-drift work reached from the other
direction — the drift is a property of *our* accumulation, i.e. our selection, not of carrying.

## 6. Verdict and smallest next action

- **Policy fidelity is 10.82%**, not 0.04%. The owner's criticism was correct and the
  correction is 270x.
- **Largest mismatch: we never extend** (~59% of decisions) — and it is worth
  **+$0.0050/window, 0.42 sd, at double the strand rate.** Not worth buying.
- **`MAX_UNMATCHED` never binds on our own book.** Retracted.
- **The clip is 64x too large relative to bankroll**; that, not the inventory ceiling, is what
  truncates our sequences to ~4.5 fills.
- **The real gap is entry selection**, and it is the only thing that reverses the sign.

**Smallest justified next action:** none of the above is actionable as a parameter. The one
measurable question left is whether our entry selection can be improved on PMUS data — and
that is blocked, because the PMUS tape carries no print rows and the selection question needs
them. That blocker is now the whole program, and it is a data-access decision rather than an
analysis one.

**Cap unchanged at 0.96. All risk limits unchanged. `shadow_lock=true`, `armed=false`,
`campaign_id=null`. No deployment, no new service, no strategy change. No profitability claim.**
