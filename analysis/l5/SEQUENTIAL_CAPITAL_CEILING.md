# The real capital ceiling, from a sequential replay of the production gate

`analysis/l5/capital_replay/seq_capital.py` calls
`ValidationGates.refuse`'s own arithmetic — `cash_committed`,
`pending_obligation`, and the `CASH_COMMITMENT` comparison at
`l5_live_validation_gates.py:669` — round by round. Nothing is modelled.

## Two of my own claims were wrong

**I said the ceiling was `envelope / obligation` = 8.00 matched contracts =
$0.615/window, and that it was invariant to `round_allocations`. Both are
wrong.**

The error: I assumed the $1.50 placement-time obligation persists. It does
not. `probe_cash_envelope` returns the **static** $12.00 — it never
subtracts committed cash — and the gate charges a *completed* pair only
its realised pair cost. So each completed round frees
`obligation − pair_cost` of headroom for the next one.

Measured, per-round, at the deployed `round_allocations=4`, pair 0.9231:

| rnd | clip | committed | outstanding | this order | total | verdict |
|---|---:|---:|---:|---:|---:|---|
| 1 | 2.00 | 0.0000 | 2.0000 | 1.0000 | 3.00 | PLACE |
| 2 | 2.00 | 1.9000 | 2.0000 | 1.0000 | 4.90 | PLACE |
| 3 | 2.00 | 3.8000 | 2.0000 | 1.0000 | 6.80 | PLACE |
| 4 | 2.00 | 5.7000 | 2.0000 | 1.0000 | 8.70 | PLACE |
| 5 | 2.00 | 7.6000 | 2.0000 | 1.0000 | 10.60 | PLACE |
| 6 | 2.00 | 9.5000 | 2.0000 | 1.0000 | 12.50 | **REFUSED CASH_COMMITMENT** |

**Five rounds, not four.** `round_allocations` splits the sizing, it does
not cap the round count.

## The actual ceiling under the unchanged $12 envelope

Pair cost 0.9231 (measured mean), clip capped at `OPENING_LEG_MAX_CONTRACTS` 3:

| round_allocations | rounds placed | matched contracts | $/window | s/round for a 900s window |
|---:|---:|---:|---:|---:|
| 1 | 3 | 9.00 | $0.692 | 300 |
| **4 (deployed)** | **5** | **10.26** | **$0.789** | 180 |
| 8 | 12 | 12.32 | **$0.947** | 75 |
| 16 | 24 | 12.32 | $0.947 | 37 |
| 32 | 50 | 12.83 | $0.987 | 18 |
| asymptote (clip → 0) | — | **13.00** | **$0.9999** | → 0 |

Entry price matters too, through the obligation `1 + up_px`: at the
cheapest observed entry (up 0.32) five rounds reach **11.36 contracts =
$0.874/window**; at the dearest (up 0.64), six rounds reach 10.98 =
$0.844.

## $1/window, exactly

**$1.00/window needs 13.00 matched contracts holding $12.00 of committed
cost — precisely the envelope.** At the measured pair cost it is the
asymptotic limit of the $12 envelope, approached only as clip → 0.

So the honest statement is neither my earlier "$0.615, not feasible" nor
"feasible":

> **$1/window is the exact asymptote of the existing $12 envelope at our
> measured pair cost. It is unreachable in practice and ~95% of it is
> reachable: $0.947/window at `round_allocations=8`, which needs 12
> completed rounds in one window.**

No capital increase, no limit change, and the $20 exposure ceiling is
never approached — peak committed stays ≤ $12 by construction.

## What that costs in execution, and where the boundary comes in

12 rounds in a 900-second window is **75 s per round**. Measured round
duration is **~99 s** (1500z: first order +142.4 s, round 4 by +538 s),
and we currently lose **84–142 s** of every window before the first order.

* Available today: `900 − 95 = 805 s` → at 99 s/round, **8 rounds**.
* With the boundary recovered: `900 − 6 = 894 s` → at 99 s/round, **9
  rounds**; at 75 s/round, **11–12 rounds**.

**This is the first time the boundary latency has a direct line to the
target.** It is not worth $0.098/window as a standalone saving; it is one
of the two things that make 12 rounds fit at all. The other is round
duration itself.

Measured today: **~1.2 rounds/window**, 2.389 matched contracts,
$0.1838/window of matched surplus. The gap to $0.947 is **rounds per
window**, not capital.

## The chain, stated once

```
$1/window  needs 13.00 matched contracts = $12.00 committed  (the envelope, exactly)
$0.947     needs 12.32 contracts at round_allocations=8      (inside the envelope)
12 rounds  needs 75 s/round in a 900 s window
75 s/round needs the ~95 s boundary delay removed AND rounds that complete faster
today      1.2 rounds/window -> $0.1838
```

Every link is measured except the last two, which are what the preflight
diagnostics and the stale-plan question are for.
