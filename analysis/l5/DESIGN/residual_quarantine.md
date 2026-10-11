# Residual quarantine: separating position concurrency from active-order concurrency

**Status: DESIGN ONLY. Not implemented, not enabled, no code path exists.**
OWNER 2026-09-10: *"Separately design—but do not live-enable yet—a residual
quarantine architecture."* No cap, ceiling or concurrency change is proposed
here.

---

## The problem, measured

On 2026-09-10 the live campaign worked 20 markets in 93 minutes, then at
14:59Z took a residual in `aec-county-not-ham-2026-09-08` — a multi-day
County Championship fixture — and stopped. 3h20m later it had opened no new
first leg; 156 of 180 `session_end` rows were `SPRING_RESIDUAL_HOLD`. The
market's book sat at 0.77/0.84, **outside the 0.25–0.75 entry band**, so the
engine could not even work the residual it was guarding. It could only wait.

> **1 residual = 0 new experiments.** The dollar risk was ~$5.70. The cost was
> the entire observation programme for the rest of the session.

## The conflation

One number is currently doing three jobs:

| concept | what it actually protects | today |
|---|---|---|
| **active-order concurrency** | execution safety — one outstanding order, post-only, cancel/deadman, no racing fills | 1 |
| **acquisition-slot concurrency** | how many markets may be *acquiring* (OPEN/EXTEND) at once | 1 |
| **position concurrency** | how many markets hold inventory at all | 1 |

Collapsing them means a **dormant** residual and an **actively working**
market consume the same scarce thing, though only the second needs an order
slot and only the second is generating evidence.

A $2 stranded residual and an active $7.50 acquisition are not operationally
the same object. They *are* the same for risk — and the design below keeps
them identical for risk while separating them for scheduling.

---

## Proposed states

```
        ┌──────────────┐  first leg fills   ┌──────────────┐
   flat │              │ ─────────────────▶ │  ACQUIRING   │  holds the
        │   IDLE       │                    │              │  acquisition slot
        │              │ ◀───────────────── │              │  AND the order slot
        └──────────────┘   flat / settled   └──────┬───────┘
               ▲                                   │ acquisition budget spent,
               │                                   │ or band exited, or
               │ settles                           ▼ session ends with residual
        ┌──────┴───────┐                    ┌──────────────┐
        │   SETTLED    │ ◀───────────────── │ QUARANTINED  │  holds NEITHER slot
        └──────────────┘                    └──────────────┘  still fully in the
                                                              loss budget
```

**QUARANTINED** is the new state. A market in it:

1. **remains owned and monitored** — its inventory is real, its settlement is
   awaited, its audit runs;
2. **stays 100% in the risk budget** — its `guaranteed_settlement_floor` still
   enters `combined_loss` exactly as today. Quarantine changes *scheduling*,
   never *accounting*;
3. **may still receive orders its own state machine permits** — specifically a
   risk-reducing completion. `reduces_risk_only(inv, side_up, px, qty)`
   already exists and already decides this exactly (`floor_after` vs
   `floor_before`, computed rather than reasoned about);
4. **does not hold an acquisition slot**, so another market may begin
   acquiring — subject to the portfolio gate below;
5. **does not gate observation at all** — already true as of the shadow
   breadth loop, which watches markets independently of any position.

---

## The portfolio gate — from the existing machinery, not a new risk system

OWNER: *"The exact gate should come from the existing combined-loss machinery
rather than inventing another risk system."* It does. Every term below is a
function that ships today in `src/pmus_spring_l5_session.py`:

```
may_begin_new_acquisition(realized_today, held_inventories, proposed) :=

      combined_loss(realized_today, held_inventories)      # existing
    + worst_case_of(proposed)                              # existing ladder term
    >  daily_loss_stop()                                   # existing, -$15.00
```

where

- `combined_loss(realized_today, inventories)` = `realized_today +
  unsettled_floor_total(inventories)` — already the single budget for settled
  and guaranteed-but-unsettled losses, and already documented as
  double-count-free: *"a market that settles leaves the HELD set and its
  result enters realized_today in the same transition."*
- `unsettled_floor_total` sums `guaranteed_settlement_floor(inv) =
  min(up, dn) − spent`, which reads no price, no book and no valuation model.
  A quarantined residual therefore contributes its **worst branch**, not a
  mark.
- `worst_case_of(proposed)` is the ladder's existing per-campaign worst case
  (`pmus_l5_ramp_ladder.worst_case`), the same number the arming preflight
  already requires.
- The comparison is **strictly greater than**, mirroring
  `combined_budget_tripped`'s inclusive boundary: at exactly the stop, the
  stop has fired.

**Held inventories include quarantined markets.** That is the whole point:
quarantine frees a *scheduling* slot and frees nothing else. A market that
strands $5.70 of guaranteed floor keeps consuming $5.70 of the −$15 budget
until it settles, and if three of them are outstanding the gate refuses the
fourth acquisition by arithmetic rather than by a hand-set concurrency number.

### Why this is safer than raising concurrency 1 → 5

Raising the number permits five simultaneous acquisitions regardless of what
is already stranded. The gate above permits a second acquisition only when
the *portfolio's worst case still fits the same −$15 budget that governs
today*. Concurrency stops being a policy dial and becomes a consequence.

---

## What must be true before this is enabled

None of these is satisfied today; this section is the promotion bar, written
before any of the evidence exists.

1. **The shadow breadth loop has run long enough to compare ranked cohorts**
   on the prospective funnel (`eligible → selected → virtual first-fill →
   virtual ≤$1 completion → residual`), with tiers stamped at selection time.
2. **A settled live cohort** large enough to state completions per residual
   *dollar* — the shadow analogue is per residual *market* and deliberately
   does not borrow the dollar name.
3. **The gate implemented with its own tests**, including: a quarantined
   market still counted in `combined_loss`; the gate refusing at exactly the
   stop; and a mutation test proving that dropping quarantined inventory from
   the budget changes the answer.
4. **No cap, ceiling, band or per-market risk change** bundled with it. This
   is a scheduling change; if it arrives alongside a risk change neither can
   be attributed.
5. **Deployed at a clean flat/settlement boundary**, never mid-hold — pushing
   a watched deploy path restarts the armed campaign.

## Open questions this design does not answer

- **What ends an acquisition campaign?** Today `opening_budget_spent` and band
  exit both end it. Whether quarantine should trigger on band exit
  specifically — the county-cricket case — needs the shadow data first.
- **Should a quarantined market be able to re-enter ACQUIRING** if it returns
  to the band with budget left? Simplest answer is no; the evidence is not in
  yet.
- **Ordering among quarantined markets** competing for the one order slot when
  several want a risk-reducing completion at once.
