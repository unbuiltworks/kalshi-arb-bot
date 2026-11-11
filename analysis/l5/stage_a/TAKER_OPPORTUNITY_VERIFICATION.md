# Verifying the 2145z completion: executable, or only attractive?

Every figure below comes from the Stage A campaign's own timestamped records
(deployment `9b43b312`, commit `0304fbe8`). Where I recomputed the engine's
arithmetic I did so independently rather than quoting it back.

## Verdict: genuinely executable

| dimension | evidence | verdict |
|---|---|---|
| **persistence** | `both_positive_after: true` from 21:48:23 to beyond 21:54:55 — ~6.5 min, ~30 consecutive decision ticks, 45 `L5_TS_COMPLETION` samples in the window | not a one-tick artifact |
| **depth** | displayed depth at full size usually 400–1500 contracts against a 2-contract need (200x-750x) | ample |
| **depth failures** | 2 of 45 samples starved: 21:51:36 (1.00 displayed) and 21:52:15 (0.69) | real but rare, and the policy refuses them |
| **latency** | the opportunity outlived ~390 s; observed decision cadence was 11-13 s | immaterial |
| **fees** | `taker_fee_usd 0.02`; independently `ceil(0.0695 x 2 x 0.10 x 0.90) = 0.02` | correct |
| **order constraints** | on-tick, above min-qty, inside cash headroom and the worst-case ceiling | already enforced |
| **partial fills** | monotone (below) | cannot flip the sign |
| **settlement** | 2145z resolved DOWN; 2 DOWN @ 0.45 redeemed $2.00 = +$1.10, which ties to the ledger delta | consistent |

## The engine's arithmetic is conservative, and I checked it

Recomputing `net_settlement` from scratch reproduces the engine exactly:

```
UP filled 0    of 2 -> cost 0.9400  worst -0.9400   (engine: -0.9400)
UP filled 1    of 2 -> cost 1.0500  worst -0.0500   (engine: -0.050000)
UP filled 2    of 2 -> cost 1.1600  worst +0.8400   (engine: +0.8400)
```

`worst_before` charges a **taker** fee of $0.04 on a DOWN leg that actually
filled as a **maker for $0.0000**. So the true status quo was **-$0.90** and
the true full-fill outcome **+$0.88**; the engine reports -$0.94 and +$0.84.
Both are understated by the same $0.04, so `worst_improvement_usd 1.78` is
exact and the `both_positive_after` gate (`cost < min(up_qty, dn_qty)`) is
strictly conservative. **No correction needed** — the engine errs toward
refusing.

## Partial fills: a shortfall, never a new loss

The venue offers `TIME_IN_FORCE_IMMEDIATE_OR_CANCEL`
(`src/pmus_migration_close_live.py:179`) but **no fill-or-kill**, so partials
are real. On the 2145z state, worst-case P&L by fill quantity:

| complement price | 0 | 0.5 | 1 | 1.5 | 2 | monotone? |
|---|---|---|---|---|---|---|
| 0.10 | -0.9400 | -0.5000 | -0.0500 | +0.4000 | +0.8400 | yes |
| 0.30 | -0.9400 | -0.6000 | -0.2600 | +0.0800 | +0.4300 | yes |
| 0.50 | -0.9400 | -0.7000 | -0.4600 | -0.2200 | +0.0200 | yes |
| 0.54 | -0.9400 | -0.7200 | -0.5000 | -0.2800 | -0.0600 | yes |
| 0.60 | -0.9400 | -0.7500 | -0.5600 | -0.3700 | -0.1800 | yes |

Worst case is **monotonically non-decreasing in fill quantity** at every
price, so any partial beats doing nothing. The break-even fill fraction at
0.10 is ~53% of 2 contracts; below that the cross is still an improvement,
just not a positive outcome. The invariant holds **only while the side being
bought is the deficient one**, which is why the policy re-asserts
`CROSS_EXCEEDS_DEFICIT` rather than trusting `top_up` to have done it.

## Independent opportunities, deduplicated

Dedupe key: one opportunity per `(market, held side, held qty, held basis)` —
a stranded leg, not a decision tick. Four stranded legs, two real exits:

| window | stranded | viable? | best `worst_after` | actual settled |
|---|---|---|---|---|
| 2115z | DOWN 1.74 @ 0.237 | **no** — best -0.0400, never positive | — | -$0.2756 |
| 2130z | UP 2.05 @ 0.46 | **yes** — 22 of 49 samples, intermittent | +$0.3915 | -$0.9430 |
| 2145z | DOWN 2.00 @ 0.45 | **yes** — sustained | +$0.9500 | +$1.1000 |
| 2200z | DOWN 1.85 @ 0.33 | **no** — 0 of 58, complement ~0.99 | — | -$0.6105 |

2115z is the instructive refusal: the cross *improved* the worst outcome and
*paid for its own fee* — passing everything `completion_decision` tests — and
still never made the worst outcome positive. That is precisely the gate the
new policy adds.

2200z resolved UP, so our DOWN leg was the loser and no complement was
affordable. **Its -$0.610 was not avoidable by crossing.**

## Modelled campaign effect

| firing rule | total | vs actual -$0.7291 |
|---|---|---|
| first both-positive tick | -$0.6421 | +$0.0870 |
| `worst_after >= +$0.25` | -$0.3371 | **+$0.3920** |
| best observed tick | +$0.4554 | +$1.1845 (hindsight) |

Positivity alone buys almost nothing; the margin floor is what makes the
policy worth the one cross a window gets. **MODELLED**, assuming an IOC fills
at displayed-minus-haircut depth. Not realized.

Note that 2145z's actual +$1.10 **exceeds** the +$0.84 the policy would have
taken: there the policy gives up ~$0.15-$0.26 of directional luck for
certainty. That is the trade, and it is right only because the identical
position was -$0.94 the other way — which is what 2130z and 2200z were.
