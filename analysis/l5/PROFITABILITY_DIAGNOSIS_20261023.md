# Why the campaign lost money, and why no exit policy fixes it

**Verdict: the policy has negative expectancy under every defensible fill
assumption, and no point on the ceiling/depth curve clears breakeven. No
candidate is promoted. One control was tightened; no economics were changed.**

Reproducible: `analysis/l5/residual_exit/profitability_bridge.py` →
`profitability_bridge.json`. 24 tests in
`tests/test_l5_profitability_bridge.py`. Every input is measured and sourced.

---

## Are we trading live?

**No. Shadow only.** `L5_SHADOW_ONLY` is set and re-asserted every heartbeat
(13:00, 13:15, 13:21Z) — `submit() raises NotArmed regardless of
PMUS_Q1_LIVE/SESSION_ID/ARM`. `armed: false`, `submission_gate.blocked: true`,
`halt: true`, `approval_state: approval_stale`. The campaign budget ledger is
closed.

The account has traded live **once**: three fills on 2026-10-23 totalling
**−$0.4048** realized. Nothing since, and nothing can submit while the lock
holds.

## 1. The 72% assumption, reconciled — and the policy was never profitable

The unit economics are measured over 15 reconciling markets
(`src/l5_breakeven.py`): **+$0.057230 per matched pair**, **−$0.331449 per
residual unit**, **+$0.002953 rebate per leg**. A residual costs **5.79×** what
a pair earns, so

> **breakeven leg completion = 84.52%**

| completion | source | EV/leg | |
|---|---|---|---|
| 84.52% | breakeven | 0 | |
| **83.42%** | **observed, our 15 markets** | **−0.004262** | below breakeven |
| 72% | the planning figure | −0.048647 | |
| 14.40% | depth-2 per-complement-ORDER fill, n=868 | −0.272526 | |

**0.144 and 0.72 are not in conflict — they are different denominators.** 0.144 is
per complement *order*; 0.72 is per *leg* compounded over the permitted
accumulation rounds: `1−(1−0.144)^8 = 0.712`. The campaign's
`accumulation_max_rounds` was 8, which is where ~72% came from.

**The campaign placed one complement per window, not eight.** 0230z and 0300z
each placed exactly one, cancelled unfilled. So the realized assumption was
**0.144 → −0.27/leg**, not 0.72.

**Every one of these is negative.** There is no defensible fill assumption under
which the existing policy has positive expectancy.

## 2. The 0230z defensive decision, audited

The 02:39:04 order was **not** an expected-value optimization and never claimed
to be: the record carries `improves_expected_value: false` and the module's own
label, *"a defensive exit bounds a tail, it does not create edge: at the touch
it is expected-value neutral before fees and negative by exactly the fee."*
That is tail-loss protection, and it worked — **−$0.0468 crossed against
−$0.0819 held**.

**Which mandate authorized it.** Not the owner's. The chain was:

1. `L5_DEFENSIVE_COMPLETION` was set in the environment (`flag_enabled: true`).
2. `decide()` returned `act: true` and issued **its own mandate**:
   *"permits ONE crossing order and the defensive pair ceiling for that order
   alone"*, `budget_total_usd 0.04916626650000`.
3. That mandate carved a **taker** order at pair cost **1.04** out of a campaign
   whose stated terms were *"0.96 pair ceiling, maker-only, post-only"*.

**And `execution_mode` is inside the owner's approval digest.**
`l5_live_authorization.EXECUTION_MODE = "MAKER_ONLY"` is a member of
`SCOPE_FIELDS`, the fixed-order tuple the approval digest is computed over. So
the crossing order contradicted a **field of the envelope the owner approved**,
not merely a local preference.

**Why nothing caught it, including my own part in that.**
`APPROVED["defensive_execution_enabled"]` is `False`, but the row had been moved
to `observed_not_graded` — by me, earlier in this session's work — so the
pre-deploy verifier reported the flag and graded nothing. The stated reason was
sound (a fixed expectation fails either the activation or the rollback), but the
consequence was that no gate compared the armed crosser against the approved
maker-only mode.

## 3–4. The dwell gate and the action comparison

Both were measured in the previous pass and are unchanged by this one:
`DWELL − ARM` out of sample is **+0.01572/unit, 95% CI [−0.030, +0.062]**,
n=167 — inconclusive; it forfeits **0.1422/unit** of opportunity to net
**+0.0157**, and **88.4%** of its exits land in windows where the profitable
completion became reachable later. Repricing a passive complement closer to the
touch was the **worst** of the five actions: it fills when our side was going to
win anyway. Neither is promoted. Details in `DWELL_GATE_IMPLEMENTATION.md`.

## 5. Deadlines, venue errors, runway

The retirement deadline is now `terminal − DEFENSIVE_MIN_EXECUTION_S` rather
than the terminal itself (`TSP.complement_retire_from_start`), so a resting
complement can never be retired at the instant the defensive sequence's own
window has already shut. The 0245z window shows the venue-error leg of this:
a ~100-second `HTTPError` storm (02:49:12–02:50:51) during which both progress
and pending-resolve polling failed; the recorder recovered and booked the
terminal state from the venue's own order row, and that window realized $0.

## 6–7. The profitability bridge, reconciled to the venue

| window | planned edge | complement slippage | residual settlement | realized |
|---|---|---|---|---|
| 0230z | +0.0585 | **−0.1053** | 0 | −0.0468 |
| 0300z | +0.0895 | 0 | **−0.4475** | −0.3580 |
| 0245z | 0 | 0 | 0 | 0 |
| **total** | **+0.1480** | **−0.1053** | **−0.4475** | **−0.4048** |

Reconciles **exactly** to the venue's −$0.4048. Attribution of the −$0.5528
shortfall:

* **missed complement → residual settlement: 81.0%**
* complement price slippage (the defensive cross): 19.0%
* **fees: 0.0%** — the venue charged `commission 0.0000` on all three fills
* **queue adverse selection: not claimed.** No complement ever filled, so no
  fill was adversely selected. Naming it a loss bucket would invent a mechanism.
* entry selection: see §1 — EV was already −0.27/leg before any of this.

## 8. Why the one lever with a positive story fails

More complement attempts is the only lever that improves completion. It
survives under **independence** and dies under the **measured** decay.

Conditional fill by attempt number (`COMPLETE_FILL_PREDICTORS.md`, n=716):
**22.0% → 25.3% → 15.8% → 8.5%**. The rate decays; it does not hold.

| assumption | attempts to breakeven |
|---|---|
| independent at 14.4% | **12** |
| measured decay, L5's levels | 16 |
| **measured decay scaled to our 14.4% attempt-1** | **never within 16** (69.6% at 16, EV −0.058) |

The third is the defensible one for us: our own attempt-1 rate, the only
measured decay shape. **No attempt budget clears breakeven.**

### And the ceiling/depth curve closes from both ends

A ceiling `T` forces `d_min = ⌈(1 − spread − T)/(2·tick)⌉`, which fixes the
gross edge at `1 − pair_cost`:

| ceiling | min depth | gross edge | pair profit | breakeven | need/attempt @8 |
|---|---|---|---|---|---|
| 0.96 | 2 | 0.05 | 0.0572 | 84.52% | **20.80%** |
| 0.97 | 1 | 0.03 | 0.0372 | 89.10% | 24.20% |
| 0.99 | 0 | 0.01 | 0.0172 | 94.21% | 29.96% |

Measured depth-2 fill: **14.40%**. Tightening forces a deeper quote whose fill
rate is too low; loosening permits a shallower quote whose edge is too small and
**raises** the requirement from 20.8% to 30.0%. The shadow's own
threshold×depth sweep agrees structurally: at depth 0 the 0.96/0.97/0.98 cells
quote **zero shares** (arithmetic — at a 1-tick spread, depth 0 prices the pair
at 0.99), only the 0.99 cell quotes at all, and **`matched_shares` is 0 in every
cell of the grid**.

One coincidence worth naming: at **12** rounds the 0.96 requirement is **14.40%**
— exactly the measured rate. That is why independence looked survivable, and
the decay is why it is not.

## What shipped

**No economic change.** Every candidate is negative or inconclusive, so per the
standing instruction none is promoted and the engine's economics are untouched.

**One control tightened**, closing §2's gap:
`l5_runtime_config_verify` gains a graded row
`maker_only_forbids_defensive_crossing`. It grades **consistency between two
things the deployment already states** — not a fixed expectation of either — so
both postures stay deployable: defensive off under MAKER_ONLY passes
unconditionally (a rollback can never be obstructed), an explicit crossing mode
with defensive on passes, and only the contradiction fails. It **fails closed**:
an absent, empty, unreadable or unrecognised mode is treated as MAKER_ONLY,
because a mode we cannot read is not consent. `checked` 21 → 22.

## The remaining blocker to positive realized profit

**The 5.79:1 residual-to-pair loss ratio, which sets breakeven at 84.52%
completion that no reachable configuration delivers.** Concretely, all three
ways out are closed by measurement:

* **raise pair profit** → needs a cheaper pair, but the ceiling already caps
  pair cost and loosening it shrinks the edge faster than it helps;
* **cut residual loss** → exit is EV-neutral-minus-fee; it compresses the tail
  (21.0% → 1.8% below −0.30/unit) and leaves the mean unchanged;
* **raise completion** → capped at ~70% by the depth-2 fill rate the ceiling
  forces, and the attempt decay prevents compounding past it.

The one quantity that could still move this and is **not measured on PMUS** is
the per-attempt complement fill rate at depth 0 and depth 1. The existing
read-only components already target it — `l5_depth_comparison.py` reports
per-depth fill rates and the break-even rate each depth's edge requires, and the
shadow sweeps thresholds 0.96–0.99 at depths 0 and 2 with conservative and
optimistic fills. Both currently lack sample (`matched_shares` 0 across the grid
on the windows read). **Accumulating that sample in the shadow is the next
action, and it needs no risk-control change and no live order.** If depth-1 fill
comes in below ~24%, there is no configuration of this strategy on BTC15m with
positive expectancy, and that is the finding to act on rather than a parameter
to keep tuning.
