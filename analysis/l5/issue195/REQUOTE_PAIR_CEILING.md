# The requote cancelled profitable pairs to chase worthless ones

**Scope.** 2026-11-07 01:00–01:15 UTC, `cpc-btc-updown-15m-2026-11-07-0100z`.
One authorized window, $1 aggregate exposure, maker-only, all three
completion switches off, pair-cost ceiling 0.96.
Deployment `8fc60289-b778-4120-9163-46b42652d5b7`, SUCCESS 00:34:07Z, commit
`0be92a32c6b0ff4b25d875a9f5592e2f42319275`, branch
`deploy/l5-issue195-phaseA5` — confirmed current and unchanged since, via the
Railway `list-deployments` record.

## 1. Venue-confirmed sequence

| time | event | evidence |
|---|---|---|
| 01:00:39 | DOWN 1 fills, maker, own 0.4900 | fill record, attested basis |
| 01:00:40.137 | `L5_MISSED_PAIR`: `pair_own_cost 1.0000`, `inside_entry_ceiling NO`, `gain_per_contract_usd 0.0000`, `missed_profitable_pair NO` — **declines to complete** | controller log |
| 01:00:40.593 | `L5_TS_REQUOTE act:true` — **cancels UP own 0.4600 → target 0.51** | venue `DJHDVD5014ET`, `ORDER_STATE_CANCELED`, `cum_qty 0`, `verify_attempts 2` |
| 01:01:00.697 | `L5_TS_REQUOTE act:true` — **cancels UP own 0.4900 → target 0.51** | venue `DJHE328054EX`, `ORDER_STATE_CANCELED`, `cum_qty 0` |
| 01:15 | settled. `matched_qty 0`, residual 1 DOWN, UP won, `yes_value 1.0000` | settlement record |

Confirmed settled economics: **fees $0.0000, rebates $0.00, realized net
−$0.4900.** Cumulative across both live windows: **+$0.0386 − $0.4900 =
−$0.4514.** No order remained open; watchdog `open_orders: 0`; no unresolved
accounting.

## 2. Root cause

`completion_requote`'s target is `min(touch, completion_ceiling, redeem_cap)`.

- `redeem_cap = 1 − held_cost` is **breakeven**, not margin.
- `completion_ceiling` is a **single-leg** own-price cap (0.95 here; it
  permits a pair of 1.44).
- The only term bounding the **pair** is `edge_cap = completion_max_pair()`,
  which **defaulted to 1.00** — the redemption line — and whose variable
  `L5_TS_COMPLETION_MAX_PAIR` was never set, and is absent from the deployed
  variable set to this day.

So no term in that `min()` preserved any pair margin. Holding DOWN at own
0.4900, the resting UP quote at own 0.4600 was a pair of **0.9500** —
**+$0.05** of matched edge. The target 0.5100 was a pair of **exactly
1.0000** — **$0.0000**. Edge per contract on a matched pair is exactly
`(1.00 − pair)`, so the chase could not have been profitable **even if it
had filled.** The engine priced the giveaway itself and logged it as
`extra_cash_usd: 0.0500`, then `0.0200`, while making the move.

**The cause was repricing, exclusively.** Not risk limits
(`ceiling_binds: false`, `redemption_cap_binds: false` on the first),
not allocation exhaustion, not order-slot contention, not the watchdog, not
stale data (`stream_age_s 0.061`, `basis AUTHENTICATED_WS`,
`queue_evidence_required: true` and satisfied: 2923.02 ahead at the resting
price, 561.02 at the target).

**The deeper defect is an asymmetry between two controllers.** `l5_book_completion.decide`
is pair-aware: it priced the same completion at 1.0000, called it outside the
entry ceiling, worth $0.0000, and declined. `completion_requote` was not
pair-aware, and cancelled toward that same price 456 ms later. One path knew
the pair was out of bounds and worth nothing; the other acted on the queue
improvement alone. **The owner's authorization for this campaign capped pair
cost at 0.96; the requote path did not enforce it and targeted 1.0000 twice.**

### The contradiction in my earlier report, resolved

I reported both that all UP orders were cancelled and that a resting UP order
could still complete the pair. The first is correct and venue-confirmed:
`DJHDVD5014ET` and `DJHE328054EX` both reached `ORDER_STATE_CANCELED` with
`cum_qty 0`, no UP order was open after 01:01:00, and `matched_qty 0` at
settlement proves no pair existed. **The second claim was speculation with no
supporting record and is withdrawn.** No valid completion order was resting
after the entry cutoff.

### Was completing economically justified at either decision?

No. At 01:00:40 the best executable UP own price was 0.5100 → pair 1.0000,
gain $0.0000. At 01:01:00 the touch had moved to 0.7100 → pair 1.2000, a
loss. The justified action at both decisions was to **leave the 0.4600 quote
resting**, which is precisely what the fix produces.

## 2a. Three UP orders, two cancel mechanisms

The requote-filtered tape showed two cancels; the unfiltered one shows
**three orders terminated by two different mechanisms**, which matches the
owner's tape in issue #195 and corrects my earlier account that all the
cancels were requote-driven:

| client order id | venue id | own px | cancelled | by |
|---|---|---|---|---|
| `l5f-b0b2…9bb2` | `DJHDVD5014ET` | 0.4600 | 01:00:40.456 | `L5_TS_REQUOTE`, `completion_requote` |
| `l5f-dc6b…f904` | `DJHE2YA0144P` | — | 01:00:59.510 | `L5_ACC_DEADLINE`, `acc_order_deadline`, **`stale_by_distance`**, `ttl_s 120.0`, `recoveries_used 1` |
| `l5f-3df3…e403` | `DJHE328054EX` | 0.4900 | 01:01:00.551 | `L5_TS_REQUOTE`, `completion_requote` |

All three `cum_qty 0`, all `ORDER_STATE_CANCELED` confirmed by re-read
(`verify_attempts 2`), each scoped to exactly one client order id. The
second was not a TTL expiry — it was cancelled ~15s into a 120s TTL by the
**distance** rule, which cleared the second order and so let the requote act
again on the third (`maybe_completion_requote` requires exactly one resting
order, and `requote_refused:MULTIPLE_RESTING_ORDERS_IN_MARKET` appears 4
times in the window while both were live).

**So the obvious question is whether a requote fix is defeated by the
distance rule.** `recover_stale_leg` accepts no held quantity and no cost
basis: it judges drift only. Measured against the exact post-fill state —
resting 0.4600, touch 0.5100, T+40s:

    recover: False   reason: WITHIN_TOLERANCE
    would_quote_now 0.49   drift 3.00 ticks   allowed 4 ticks

**It does not cancel the quote the fix preserves.** The order it *did*
recover was the 0.4900 replacement at 01:00:59, by which time the touch had
reached 0.7100 — a 20-tick drift against a 4-tick tolerance, which is a
genuine stale leg. So the preserved quote survives the post-fill cancel and
the first distance check, and is released only once the market has moved
more than 4 ticks past where a replacement would be quoted — at which point
the pair available is above the ceiling anyway. Patience while the pair is
good; release when it cannot be.

This holds **because 3 ticks is inside the tolerance, not because the
recovery understands that 0.46 was a 0.9500 pair.** That bound on the claim
is pinned by a test.

## 3. The fix

One default. `completion_max_pair()` now falls back to the **opening**
ceiling `max_pair_cost()` instead of 1.00. The 1.00 hard clamp is unchanged,
and `L5_TS_COMPLETION_MAX_PAIR` still overrides, up to that clamp, for the
genuine stranded-leg case.

Why the opening ceiling is right *here*, when `completion_pair_ceiling`
deliberately lets a completion be dearer than an opening: that latitude
exists for a choice between a dear pair and a **stranded** leg, where paying
up converts the residual. A **requote** is not that choice — it already holds
a cheaper resting quote, so it chooses between a cheap maker quote that may
fill and a dear maker quote that may equally not. Touching the book
guarantees nothing, and a pair at 1.00 earns $0.00 even when it does fill.

It only ever **tightens**: it can remove a requote and can never add one,
raise exposure, cross a book, or spend a dollar the opening path would not
already have spent.

## 4. Original vs corrected, on recorded live books

`analysis/l5/issue195/requote_ceiling_compare.py`, driving the deployed
`completion_requote` over `tape_20261028.jsonl` (real authenticated ladders,
two settled windows) plus the 0100z state.

| window | cancels (orig → fixed) | worst pair | mean pair | zero-edge cancels | cancels above the 0.96 cap |
|---|---|---|---|---|---|
| 1215z, held UP, rested 0.47 | 51 → **51** | 0.9993 → **0.9593** | 0.9554 → **0.9346** | 0 → 0 | 28 → **0** |
| 1245z, held UP, rested 0.63 | 83 → **0** | 1.0000 → — | 0.9984 → — | 77 → **0** | 83 → **0** |
| 0100z (venue-confirmed) | 1 → **0** | 1.0000 → — | 1.0000 → — | 1 → **0** | 1 → **0** |

The two characters of the fix are both visible:

- **1215z — repricing is preserved, not disabled.** All 51 reprices still
  happen; they are simply priced inside the ceiling. The 28 that would have
  bought a pair above the approved cap are capped instead of cancelled.
- **1245z and 0100z — repricing that could only buy a worthless pair is
  eliminated.** 1245z ran the 0100z pathology 83 times nine days earlier, 77
  of them at a pair ≥ 1.0000.

### What this is not

No fill is claimed anywhere. Both cancelled 0100z orders show `cum_qty 0`,
and whether the preserved 0.4600 quote would have filled is **unknown**: it
rested 5 ticks behind the touch with 2923.02 contracts of measured displayed
depth ahead of it. Reaching a price level is necessary for a maker fill and
not sufficient. Counterfactual fills are not simulated and no shadow
profitability is asserted. What the comparison establishes is a change in
**controller decisions and the pair costs they target**, measured against
venue-confirmed and recorded data.

The honest bound on the 0100z counterfactual: had the 0.46 quote been
preserved, the outcome was either a matched pair at 0.9500 (**+$0.05**) or
the identical unmatched residual (**−$0.49**). The chase, had *it* filled,
was worth exactly **$0.00**.

## 5. Related reliability defects

**The deployment watcher's empty status — and it was not a malformed query.**
Raw GraphQL from this session is *unauthorized*. `deployments`, `projects`
and `me` all answer HTTP 200 with
`{"errors":[{"message":"Not Authorized",...}],"data":null}`. The ad-hoc
watcher read `.data.deployments.edges[0].node.status`, which yields nothing
from a null `data`, and printed an empty string — read as "not deployed".
That produced two false negatives on deployments that had in fact succeeded.
There is no query to repair; the authorized path is the Railway MCP
`list-deployments` tool. `scripts/l5_railway_deploy_status.py` now parses a
response and **raises** on every shape that does not carry a status —
`errors` present, null `data`, missing field, empty edges, non-JSON, unknown
status — so "not authorized" can never again render as "not deployed". It
deliberately makes no request of its own, which a test enforces on the parsed
AST.

**Rollover.** Measured on the deployed SHA at the 01:30z boundary:
`model_close` 0.002s, `close_window` 0.000s, `shadow_close` 0.203s,
**`two_sided_close` 6.221s**, `shadow_attach_ladder` 0.000s,
`evidence_attach` 0.000s — **6.45s total, no `L5_LIVE_ROLL_OVER_BUDGET`, and
no remaining ~93-second stall.** The residual 6.2s is the durable ledger's
`save()` against the network volume, whose `finalized` section is unbounded;
that is issue #198 (retention), scheduled after #195, and it delays
historical settlement processing rather than new-market decisions — 0100z
quoted at the open and turned a 01:00:39 fill into a decision at 01:00:40.1.
No change made here.

**Knob digest.** `strategy_config_fingerprint` reported the module *constant*
`OPENING_LEG_MAX_CONTRACTS` (3) rather than the resolver
`opening_leg_max_contracts()` (1 under the campaign's
`L5_TS_OPENING_LEG_MAX_CONTRACTS=1`), so a digest that claims to cover the
effective knob values carried a figure the engine was not using, and two
different configurations hashed alike. Now reports the resolver, and
`completion_max_pair` is in the digest.

**`DECIDE_COOLDOWN` / `REQUOTE_CHURN`** appeared 56 times in the window and is
**not** a defect: it is the bounded re-decision cadence in
`l5_book_completion.decide`, reporting-only, and it placed, repriced and
withdrew nothing. No change made.

## 6. The next blocker: the complement rescue can also breach the cap

**Not fixed here, deliberately.** The 0.4900 replacement posted after the
first cancel was a pair of **0.98**, above the campaign's approved 0.96.
That price came from `complement_order`, which tries the 0.96 opening cap
first and then falls back to `completion_pair_ceiling()` — a *third* pair
ceiling, also defaulting to **1.00** and also unset in the deployed
configuration.

I have not changed it, and the reason is a real asymmetry rather than
caution:

- **The requote had no defensible rationale.** Cancelling a cheaper resting
  maker quote for a dearer one converts nothing: both outcomes are maker
  quotes, and the dear one earns $0.00 even when it fills. There is no state
  in which it is the better choice, so tightening it needs no judgement call.
- **The complement rescue has one.** It *places* a completion rather than
  destroying a cheaper one, so it is a genuine choice between a dear pair and
  a stranded leg — and paying 0.98 for a pair does beat holding a leg worth
  either $0 or $1. Its own code comment makes that argument, and tightening
  it would make some completions refuse outright.

That trade-off is an owner decision about the approved limit, not an
engineering one, so it is reported rather than taken. **Recommendation:**
set `L5_COMPLETION_PAIR_CEILING` to the approved campaign cap for the next
experiment, so all three ceilings agree, and measure completed pairs against
stranded legs. That is one variable and it only tightens.

## 7. Still open

- The **$0.0521 completion-budget discrepancy remains open** and the durable
  budget has **not** been reset. (Resolvable later by emitting the durable
  `defensive_charge` journal; not touched here.)
- Taker completion remains **disabled**.
- No variable was written, no authorization changed, nothing armed.
