# Autonomous opening path — what exists, and the one thing that does not

Read against deployed commit `c39183a3`. Read-only investigation; nothing
armed, nothing deployed, no variable changed.

## Your correction is right, and the logic already exists

L5 does not need to be a live signal provider. The opening **decision
logic** is already built and is already L5-independent in its inputs.

### 1. The opening planner — `FaithfulExecutor` `TWO_SIDED_PROBE`

`l5_live_execution.py:4487 probe_plan(market, authenticated, loss_ceiling)`
plans an opening **pair** from the authenticated book. Around it:
`probe_precheck`, `probe_store_plan`, `probe_round`, `probe_legs_claimed`,
`probe_leg_order`, `probe_leg_qty_cap`. It sorts legs cheapest-first
("a cheap leg filling alone is the smaller worst case"), prices the
complement later because one live-order slot forbids both resting at once,
and refuses a new directional opening after the entry cutoff
(`PAST_ENTRY_CUTOFF_NOT_A_COMPLETION`).

Its guards all exist in `l5_two_sided_probe` (TS) and are in force today:

| guard | value |
|---|---|
| `min_first_leg_own_price` | **0.20** |
| `opening_notional_budget_usd` | **$3.00** (code max $5) |
| `opening_leg_max_contracts` | **3** |
| `max_pair_cost` | **0.96** |
| `entry_cutoff_s` / `complete_cutoff_s` | 600 / 720 |
| `min_entry_runway_s` | 300 |
| `round_allocations` | 4 |
| `fee_floor`, `second_leg_slot_refusal`, `may_enter` | present |

### 2. The continuous independent decision loop — `Runner.shadow_tick`

`l5_pmus_live_runner.py:3676` — *"ONE independent inventory-driven decision,
every tick… Runs whatever the money path is doing — cooling off, halted,
disarmed, shadow-locked."* Six policies step on one shared book snapshot per
tick: `ARGMAX`, `ARGMAX@ONE_TICK_BEHIND`, `ARGMAX@TWO_SIDED`,
`ARGMAX@TWO_SIDED_LADDER`, `PAIR_AVAILABLE`, `RESIDUAL_URGENCY`.
`l5_shadow_controller.BASELINE_POLICY = "ARGMAX"` is defined as *what the
money path would have done*, and is compared against L5's observed action.

**The continuous realtime shadow validation of independent opening logic is
therefore already running and already accumulating** — ~862 decisions per
policy in the current container alone. No new service is needed for it.

## The blocker is not L5, and not a flag

### `handle()` is invoked once per admitted L5 action

`l5_book_completion`'s own docstring states the defect precisely:

> "A maker completion is priced by `TS.complement_order`, called from
> `l5_live_execution.handle(row, ...)`, whose only hot-path caller is the
> ingest loop at `l5_pmus_live_runner.py:2450` — **one call per admitted L5
> action.** So a completion refused at the instant an action arrives is not
> reconsidered until the next action, a measured ~34 s later **and longer
> when L5 is quiet.**"

So the opening planner is L5-independent in its inputs but L5-coupled in its
*invocation*.

### A self-generated decision has no route to the venue — by construction

`l5_intent_gate.py`:

> "A controller-generated intent is **NEVER** given an L5 row: fabricating
> one would make our own decision indistinguishable from L5's actual order in
> the tape, the ledger and the provenance chain, which is the error this
> programme has guarded against from the start. It is therefore refused at
> the executor boundary by name
> (**`CONTROLLER_INTENT_HAS_NO_AUTHORIZED_ADAPTER`**) rather than translated."

> "THAT REFUSAL IS THE HONEST STATE OF THE WORK, not a placeholder. Routing a
> generated decision to the venue needs **an adapter from an intent to a
> submission that the registry, the reconciler and the watchdog all
> understand, and building one is a money-path change.**"

And the capability gate, verified by grep against the deployed tree:

- `ControllerLiveAuthorization` is defined in `l5_intent_gate.py:283` and
  accepted at `:358`; refusal at `:557`.
- The Runner builds `self.intent_gate = GATE.IntentGate(self)` (`:1021`) and
  uses only `evaluate_prefix` (`:2586`) — a **read-only** evaluation.
- **The Runner constructs ZERO `ControllerLiveAuthorization` objects**
  (grep count 0).

Hence `l5_book_completion`'s statement: *"`Runner` passes none, so in
production this path is not disabled, it is **ABSENT**. That is the feature
gate, and it is stronger than a flag because no deploy template or copied
variable can flip it."*

## CORRECTION TO MY EARLIER REPORT

**Route B is completion-only and could never have submitted an order.**

- `l5_book_completion.ACTIONS = ("COMPLETE", "REPRICE", "HOLD")` — there is
  no OPEN. It is *"BOOK-DRIVEN COMPLETION for inventory we already own."*
- `L5_BOOK_COMPLETION` is documented as an **EVALUATION flag**: *"ON makes
  this module run and RECORD; it does not make anything submittable — that
  needs the capability object."*

So setting `L5_BOOK_COMPLETION=1` activated a **recorder**, not a trading
path. It could not have produced a fill in any market condition. My earlier
reports described Route B as "live" and attributed the campaign's zero
orders solely to L5 inactivity. The L5 coupling is real and does explain the
**money path's** zero orders, but Route B specifically was never capable of
trading, and I should have said so.

Also note `book_completion:NO_ACTION_YET` — Route B re-runs the completion
path *using the last admitted L5 action row*, so even as a recorder it needs
one L5 action per window to have something to re-evaluate.

## What the already-running shadow has measured — and it is not encouraging

From `L5_SHADOW_SETTLED` on window 1830z (modelled, never submitted):

| policy | matched units | residual units | modelled net |
|---|---:|---:|---:|
| `ARGMAX@TWO_SIDED_LADDER` | **0** | 3 | **−$1.3400** |
| `PAIR_AVAILABLE` | **0** | 1 | **−$0.4800** |
| `RESIDUAL_URGENCY` | **0** | 2 | **−$1.0100** |

Across ~862 decisions per policy, `shadow:PAIR_AVAILABLE:complete` is **3**.
The rest are `no_action` or `extend`.

Against that, the system's own measured economics: completion pair
break-even **0.9532** versus an achieved quantity-weighted **0.9530** — i.e.
adverse selection consumes the half-spread that makes the rest look
profitable — and the C6–C8 live baseline was `+$0.1933/window` matched
surplus with `pairs_completed` 23 across 15 reconstructable windows.

**This is evidence against the stated goal of multiple profitable completed
pairs per BTC15m window.** It is the independent logic's own continuous
shadow saying it opens residuals it does not complete. That must be resolved
in shadow before any money is committed, and it is the cheapest place to
resolve it.

## Smallest honest path to verified live execution

1. **Mine the shadow record that already exists** for opening-condition
   validation: per-window matched vs residual, pair cost achieved vs the
   0.9532 break-even, and side/price/size/timing/inventory against L5's
   observed actions via `BASELINE_POLICY`. No new code, no new service.
   This is where "do the opening conditions pay?" gets answered.
2. Only if (1) shows completed pairs at a pair cost under break-even:
   build the **intent→submission adapter** so a `CONTROLLER_GENERATED`
   intent is understood by the order registry, the reconciler and the
   watchdog, and have the Runner pass a `ControllerLiveAuthorization`.
   This is a money-path change, scoped to the existing executor — it
   preserves `handle`, Route B, reconciliation, the watchdog and every risk
   control, and adds no service.
3. **A new bounded live authorization is required.** This changes the
   authorized strategy from mirroring L5's actions to opening autonomously.
   The installed approval `ac855ce3…` does not cover it, and its own series
   /scope were approved for the L5-followed path. It must not be reused.

## Current state

Engine disarmed (`PMUS_Q1_LIVE=NO`, preflight `fail: ["owner_arm_token"]`).
3 of 8 windows unspent, approval `ac855ce3…` valid to 22:20:15Z.
Nothing submitted, ever: campaign ledger `peak_usd 0`.
