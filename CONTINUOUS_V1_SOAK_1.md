# Continuous V1 — shadow soak #1

Actions run 35416555994, `f2ba8066`. **4 windows, 86 cycles, 21.8 minutes,
zero orders.**

```
mode SHADOW   windows 4/4 COMPLETED   stopped RISK_BUDGET_EXHAUSTED
              (max_windows 4 reached -- a clean bound, not a failure)
total_spend_usd 0   realized_usd 0   mutations_sent 0
```

| window | entry TTE | waited | polls | cycles | rollover | account |
|---|---|---|---|---|---|---|
| `…0255-UP` | 275.8s | 222.6s | 35 | 20 | **safe** | readable |
| `…0300-UP` | 267.4s | 47.9s | 5 | 22 | **safe** | readable |
| `…0305-UP` | 278.8s | 20.0s | 2 | 22 | **safe** | readable |
| `…0310-UP` | 276.7s | 106.5s | 18 | 22 | **safe** | readable |

Every window entered inside 240:280. Every rollover gate passed on positive
evidence — a readable open-order list with none of ours in it, and a readable
execution view. Read completeness: **book 86/86, depth 86/86, flow 86/86.**

## A NEW MATERIAL STATE-INTEGRITY DEFECT, found by the soak

**Two of 86 cycles published a CROSSED book, and the frozen controller quoted
into it.**

`GEMI-BTC05M2609190305-UP`, cycles 20 and 21:

```
YES bid 0.95 / ask 0.65     spread -0.30
NO  bid 0.35 / ask 0.05     spread -0.30
passive_pair_cost 1.30      crossing_pair_cost 0.70
identity_consistent TRUE
L5 action: OPEN YES @ 0.95 · OPEN NO @ 0.35
```

A bid **thirty cents above its own ask**, and a pair cost of **1.30 for a
pair worth 1.00**. In a live window those would have been two maker orders at
a guaranteed loss if both filled.

**The existing check could not have caught it.** `identity_consistent`
verifies `no_bid == 1 − yes_ask` and `no_ask == 1 − yes_bid`. Those hold
*exactly* on a crossed book — crossing both sides preserves the complement
identity by construction — and both crossed cycles reported
`identity_consistent: True`.

**The mechanism is established, not guessed.** All 2 crossed cycles are
*exactly* the 2 cycles whose **ask ladder was empty**. With no resting ask
the contract object still publishes an ask price, and that number describes
nothing — so the NO bid derived from it describes nothing either.

### Fixed, as a readability gate

`CP.crossed()` refuses a book whose bid is at or above its own ask, or which
is missing a side, exactly as `state_from_venue` already refuses an
identity-inconsistent one. `inventory_known` goes False, `state_id` becomes
`unreadable`, and the controller quotes nothing.

**This is not a pair-cost ceiling.** It says nothing about what a pair may
cost — a wide, expensive, perfectly readable book still passes, and a test
pins that. The owner's decision that no pair-cost ceiling exists on this path
is untouched. It moved `executable_sha`; `envelope_sha` is unmoved for a
ninth window.

## Habitat evidence — first real measurements (model B only)

Across the 84 non-crossed cycles:

| measure | value |
|---|---|
| YES spread | min **0.02** · median **0.28** · max **0.36** |
| touch bid size | min **5** · median **217** · max **3005** |
| public prints per read | min **3** · median **4.5** · max **15** |
| repricing rate | **0.86 – 0.96** surface moves per cycle |
| flow readable | **86/86** |

Two things stand out against the manual windows, as observations not
conclusions:

* **There is real depth here.** Median touch size 217, max 3005 — against
  C8B, where the bid at our price read `0.01` for seven consecutive cycles
  because our own 0.01 was the entire bid. Our own size at a level is
  flagged, never subtracted.
* **The surface moves almost every cycle** (0.86–0.96 reprices/cycle). At 12
  slots and two rungs, a window that re-priced on every move would exhaust
  its budget in roughly six cycles. C8B stopped on budget at cycle 9. This is
  the number to watch when sizing a live continuous budget, and it is
  recorded rather than acted on.

**No controller tuning from any of this.** Collection only, as instructed.

## What this soak did and did not prove

**Proved:** discovery, entry banding (4/4 in band), authoritative state
maintenance across 86 cycles, complete per-cycle evidence (market state →
book/depth/flow → inventory → L5 action → execution plan), the rollover gate
on positive evidence, clean bounded termination, and zero reachability of the
order path.

**Did not prove, and cannot:** with nothing resting there are no fills, so
inventory stayed flat and the policy stayed in `OPEN` for all 86 cycles.
KEEP/CANCEL and the fill-driven transitions are not exercised in shadow.
**C8B proved those live.**

**State-integrity failures observed: one** — the crossed book above, now
fixed and pinned. No `UNKNOWN_TERMINAL`, no `EXECUTION_CONTRADICTION`, no
`ORDERS_LIVE_AFTER_STOP`, no unreadable state.
