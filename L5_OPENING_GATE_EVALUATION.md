# Inventory-aware opening evaluation + sampler instrumentation — 2026-10-29

Production **disarmed** (`PMUS_Q1_LIVE=NO`). No new service. No money-path
behaviour change: the only code touched is a reporting snapshot and the
shadow sampler's call into it.

## 1. Instrumentation — DONE, and the root cause was smaller than expected

`Runner.shadow_residual_book` already passed
`first_leg_own_px=inv.get("pair_unit_cost")`. The wiring was right; the
field did not exist. **`Inventory.snapshot()` returned only
`{yes, no, matched, residual}` — no cost fields at all.**

And `pair_unit_cost` could never have worked:

```python
def pair_cost(self):
    if self.matched == 0:
        return None          # None exactly when a residual is outstanding
```

A pair cost only exists once something is matched, which is precisely the
case the residual sampler does **not** fire on. The figure a completion
decision needs is the average own cost of the side carrying the imbalance.

**Fix:** `snapshot()` now also reports `avg_yes_cost`, `avg_no_cost`,
`held_leg_side` and `held_leg_own_cost`, and the sampler prefers
`held_leg_own_cost` with `pair_unit_cost` kept as a fallback. Additive and
reporting-only.

**Proven on a synthetic book** (bids 0.52/0.51, asks 0.53/0.54, depth 2):

| | before | after |
|---|---|---|
| `pair_if_maker` | `why: no_first_leg_price` | **0.9200**, clears ceiling |
| `pair_if_maker_behind_touch` | `why: no_first_leg_price` | **0.9000**, clears ceiling |
| `pair_if_taker_walk` | `why: no_first_leg_price` | **0.930000** |

It also tracks the held side correctly through an extend (0.45 → 0.46) and
after a partial completion (still the YES side at 0.46, not the pair).

**Tests:** 13 new in `tests/test_l5_residual_sampler_first_leg_cost.py`, all
pass. Three existing tests pinned `snapshot()` by exact dict equality and
I extended it additively, so they were updated to assert the four original
keys as a subset — and the restart test now *additionally* asserts the cost
basis survives a redeploy (`held_leg_own_cost == "0.48"`), which is a
stronger assertion than it made before. 192 pass across both controller
suites plus the new file. No other regressions.

**One redeploy of the existing disarmed service is required** for this to
start producing data; the code is inert until then.

## 2. Inventory-aware opening gate — evaluated on the 38 ARGMAX windows

Net P&L by the opening own price of the dearer leg:

| band | n | matched | pair_net | resid_net | **NET** | net/window |
|---|---:|---:|---:|---:|---:|---:|
| 0.20–0.30 | 3 | 3.0 | +1.3500 | −2.2400 | −0.8900 | −0.2967 |
| 0.30–0.40 | 9 | 8.0 | +2.2393 | −2.2493 | **−0.0100** | **−0.0011** |
| 0.40–0.50 | 20 | 19.0 | +5.6793 | −7.1193 | −1.4400 | −0.0720 |
| 0.50–0.60 | 2 | 8.0 | +0.9150 | −1.7150 | −0.8000 | −0.4000 |
| (no priced inventory) | 4 | — | — | — | 0.0000 | — |

**Every band is net-negative.** Band exclusion, against ARGMAX's −3.1400:

| excluded band | windows kept | NET kept | vs ARGMAX |
|---|---:|---:|---:|
| 0.40–0.50 | 18 | **−1.7000** | +1.4400 |
| 0.20–0.30 | 35 | −2.2500 | +0.8900 |
| 0.50–0.60 | 36 | −2.3400 | +0.8000 |
| 0.30–0.40 | 29 | −3.1300 | +0.0100 |

**No opening gate makes ARGMAX positive.** The best single exclusion still
leaves −1.7000 while discarding **20 of 38 windows** — most of the activity
— to get there. 0.30–0.40 is the only near-breakeven band and it is zero
(−$0.0011/window, n=9), not positive.

### What this establishes

**The opening decision is not where this is fixable.** Refusing openings
removes losses in proportion to the activity removed; it never creates
profit. That is consistent with every other measurement: `resid_net`
−13.3237 against `pair_net` +10.1837, a median complement queue of **316×**
our q=1 need, and matched pairs that are already excellent at a median cost
of 0.7213. **The loss is in completion, not selection.**

## 3. Measurement limits, stated rather than papered over

- **Queue-adjusted fill rate per window is NOT computable from this pull.**
  The 401 residual frames span only **2 markets** (2100z, 2115z) — 5-second
  cadence across 6 variants covers minutes, not hours — and only **1**
  overlaps the 38 settled ARGMAX windows. So the median **316× need** is an
  aggregate over 401 frames in 2 windows, not a 38-window predictor, and I
  could not test queue depth as a per-window predictor of residual loss.
- Fixing that needs no new code: with the instrumentation above deployed, a
  longer residual-sample pull becomes informative because the pair
  economics are populated.
- **L5's historical profits are not assumed to transfer.** L5's behaviour is
  used only as the sequential-inventory reference: it ends 99.0% of windows
  with a residual, runs a median 107 legs per window at ~$1,095, and is
  77.3% maker. We run one order slot at $12. Nothing here imports its P&L.

## 4. GO / NO-GO for bounded autonomous live validation

### NO-GO — autonomous opening is not ready, and the opening gate does not fix it

- ARGMAX is negative in **every** opening-price band.
- **No** opening-price gate produces a positive total; the best discards the
  majority of windows and is still −1.7000.
- ARGMAX needs **~31% more matched units per unit of residual** to break
  even (matched +$0.268/unit against residual −$1.110/unit, currently
  3.17:1 against a required 4.14:1). An opening filter cannot supply that.
- Routing this to the venue would convert modelled losses into realized
  ones.

### The one positive result is on the completion side, and it already has a GO

The defensive-completion replay (prior turn, same 290 authenticated frames)
returned **+1.1199 at λ=0.5 and +1.1492 at λ=8.818** against a +0.1256
baseline, rescuing 0130z from −1.1594 to +0.0343 by crossing at pair 0.9656.
That is on the **L5-followed** path, where the completion machinery already
exists (`l5_defensive_completion`, ceiling 1.20 vs the 0.96 maker ceiling,
$2.00 loss budget, `cancel_confirmed` for cancel-by-exact-coid).

**So the next bounded live validation should be defensive completion on the
L5-followed path — not autonomous opening.** The controller adapter design
stands as designed and stays unbuilt.

### Evidence required to justify autonomous live validation

All four, none yet met:

1. a policy with **positive total settled net** — pair surplus minus
   residual settlement — over **>=30 windows**;
2. a matched:residual unit ratio **above 4.14:1** at the measured unit
   economics;
3. the now-unlocked `pair_if_maker_behind_touch` showing a completing pair
   reachable at **<=0.96 inside the window** often enough to support (2),
   measured over **>=30 windows**;
4. a per-window queue-adjusted fill rate, which (3) makes computable, rather
   than the current 2-window aggregate.

Steps 1–4 are measurements on the existing shadow with the instrumentation
above. They need one redeploy and no new infrastructure.
