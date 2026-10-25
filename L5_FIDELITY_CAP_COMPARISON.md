# L5 vs our engine, decision by decision — **0.99 improves fidelity by ZERO**

PMUS both sides. L5's fills from the committed `data/l5/raw/activity.jsonl` through the
existing pipeline (`analysis/l5/inventory_trajectory/trajectory.py`). No new collection, no
new service, no deployment. Gate values are the **deployed** ones read from
`L5_EFFECTIVE_RUNTIME_CONFIG`, not from a replay harness.

**Scope: 527 windows, 62,020 decisions, chronological, 1773917919 -> 1788217875.**
527 openings, 33,240 extensions, 28,253 completions.

Script: `analysis/l5/fidelity_cap/compare.py`; per-decision ledger:
`analysis/l5/fidelity_cap/decisions.jsonl`.

## VERDICT: raising the cap to 0.99 does NOT materially improve behavioural fidelity

**Fidelity 0.96 -> 0.99: 0.04% -> 0.04%. +0.00 points. Zero decisions flip.**

| | cap 0.96 (production) | cap 0.99 (candidate) |
|---|---|---|
| **reproduced** | **22 / 62,020 = 0.04%** | **22 / 62,020 = 0.04%** |
| refused by the cap specifically | 11,912 = **19.21%** | 10,020 = 16.16% |
| refused for other reasons | 50,086 = **80.76%** | 51,978 = 83.81% |

## Why zero: the cap was masking a tighter gate

| gate | at 0.96 | at 0.99 | change |
|---|---|---|---|
| `PAIR_CEILING` | 11,912 | 10,020 | **−1,892** |
| `MAX_UNMATCHED_CONTRACTS` | 44,202 | 46,094 | **+1,892** |

**The same 1,892 decisions move from cap-refusal to unmatched-refusal.** Loosening the ceiling
hands them straight to the next gate. Net fidelity change: **nil**.

Full refusal attribution at the production cap:

| gate | count | share of all decisions |
|---|---|---|
| **`MAX_UNMATCHED_CONTRACTS`** | **44,202** | **71.27%** |
| `PAIR_CEILING` | 11,912 | 19.21% |
| `COMPLETE_CUTOFF` | 5,231 | 8.43% |
| `OPENING_LEG_MAX_CONTRACTS` | 497 | 0.80% |
| `MAX_CASH_DEPLOYED` | 140 | 0.23% |
| `ENTRY_WINDOW_CLOSED` | 8 | 0.01% |
| `OPENING_NOTIONAL_BUDGET` | 6 | 0.01% |
| `MIN_FIRST_LEG_PRICE` | 2 | 0.00% |

By action type at 0.96 — note the cap touches **only** completions, and never an opening or
an extension:

| kind | n | reproduced | cap-refused | other |
|---|---|---|---|---|
| OPENING | 527 | 3.23% | 0.00% | 96.77% |
| EXTENSION | 33,240 | 0.02% | 0.00% | 99.98% |
| COMPLETION | 28,253 | 0.00% | **42.16%** | 57.84% |

## The actual binding constraint is inventory scale, by a factor of ~23

L5's unmatched residual at the moment of each decision, against our `MAX_UNMATCHED_CONTRACTS`
of **5**:

| p10 | p25 | **p50** | p75 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| 17.2 | 47.7 | **117.1** | 218.8 | 342.5 | 670.8 | 1154.0 |

**96.8% of L5's decisions occur with a residual already larger than our entire limit.** He
medians 117 contracts of unmatched inventory; we permit 5.

Fidelity is essentially a function of that limit, not of the ceiling:

| `max_unmatched` | 5 | 10 | 25 | 50 | 100 | 250 | 1000 |
|---|---|---|---|---|---|---|---|
| reproduced | 0.14% | 1.09% | 4.75% | 11.47% | 22.23% | 43.37% | 66.21% |

## And this is a reason NOT to chase fidelity that way

Raising `MAX_UNMATCHED_CONTRACTS` is a **risk-limit increase**, not a fidelity tweak, and the
measured economics argue against it directly:

- our own residual carries a **negative drift**: `residual_net` −$85.2296 over 355 settled
  windows, **−10.3 sd** below zero, and **−$0.6932/window** once `|resid|` reaches 3+
- L5 carries one-sided inventory as a matter of course and is profitable; **we are not**

So scaling inventory toward L5's 117-contract median would scale **our measured loss**, not
his profit. The fidelity gap and the profitability gap have the same cause — we are selected
into the wrong side — and inventory capacity is the multiplier on it, not the fix.

## What this retires

- **"The 0.96 cap is why we never complete"** — true of the PMUS *book* (pair cost is 0.99
  and the cap requires 0.96) but **false as an explanation of behavioural divergence**. On
  L5's own 62,020 decisions the cap accounts for 19.21% of refusals and `MAX_UNMATCHED` for
  71.27%, and removing the cap moves decisions between gates without reproducing one more.
- **The cap-to-0.99 proposal** — no economic support (the sweep reversed at n=14) and now **no
  fidelity support either**. Both legs of the case are gone.

## Smallest justified next action

**None on the cap.** It is not the binding constraint on fidelity and not shown to pay.

The single measured fact that governs everything above is the **negative residual drift**. It
makes inventory scale dangerous rather than desirable, and it is the common cause of both
gaps. The next question is therefore whether that drift is a property of *our entry selection*
— which L5's own profitability says it need not be — and that is an entry question on
PMUS data, not a cap or inventory question.

**Requirement honoured: production cap stays 0.96, `shadow_lock=true`, `armed=false`,
`campaign_id=null`, no live order, no deployment, no new service.** No claim of improved
profitability is made anywhere above.
