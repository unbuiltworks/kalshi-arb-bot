# Enabling the corrected maker-completion route

Commit `5fa66775` + successor. **Deployed disarmed. No live rearming.**

## Exact configuration required

The route is inert unless `L5_BOOK_COMPLETION` is set — **and on the
deployed service it is already set.** Confirmed from the live heartbeat at
13:15:53Z on deployment `ac6e6da5`: `book_completion:DECIDE_COOLDOWN`,
`book_completion:NO_ACTION_YET` and `book_completion:NO_INVENTORY` are all
incrementing, and those counters are only reached after `BC.enabled()`
passes.

So the corrected route is **live and deciding right now**, disarmed, which
is the intended pre-live state: it evaluates and records, and the transport
refuses every submission. An earlier draft of this file said the route was
inert; that was wrong about the deployed configuration, though right about
the code.

**Observability gap found and closed.** `L5_EFFECTIVE_RUNTIME_CONFIG` did
not report `BC.enabled()`, so the switch deciding whether held inventory can
be completed at all could only be INFERRED from whether counters appeared.
`book_completion_enabled` and `book_completion_breakeven_pair` are now
observed rows. They take effect on the next deploy, so they should ride the
deploy that accompanies the authorized validation rather than justify one of
their own.

| variable | value | why |
|---|---|---|
| `L5_BOOK_COMPLETION` | `1` | the only switch. Unset/`0` → `BC.enabled()` false → `maybe_book_completion` returns immediately and the engine is byte-identical |
| `L5_TS_DEPTH_TICKS` | `2` (current) | the depth the complement is quoted at; unchanged |
| `PMUS_Q1_LIVE` | `NO` | **stays NO.** Disarmed |
| `L5_CONTROLLER_OPEN` | `1` | required for the controller to open at all, i.e. for this route to have inventory to complete |
| everything else | unchanged | no cap, limit, budget or crossing flag is touched |

Current service state: `PMUS_Q1_LIVE=NO`, authorization expired, campaign
approval stale — three independent refusals. Enabling
`L5_BOOK_COMPLETION` while disarmed makes the route **decide and record**
without submitting, which is the intended pre-live step.

## What to look for once enabled, disarmed

| signal | meaning |
|---|---|
| `book_completion:NO_ACTION_YET` | should now be **0** while inventory is held |
| `book_completion:REENTERED:CONTROLLER` | the fix firing — the count that was structurally 0 |
| `L5_BOOK_COMPLETION_TRIGGER` with `route: CONTROLLER_REENTRY` | provenance `CONTROLLER_GENERATED`, `reused_action_key: null` |
| `book_completion:ORDER_ALREADY_RESTING` | the one-order limit declining before re-entry |
| `book_completion:DECIDE_COOLDOWN` | the 5 s cadence holding |

## Verification performed before deployment

**Check 1 — BTC15m-native breakeven: done.**
`analysis/l5/btc15m_native/BTC15M_NATIVE_BREAKEVEN.md`. 106 settled BTC15m
markets from our own ledger, read-only; 89 usable. OOS paired 82.73% against
a 61.74% breakeven, net **+$2.9108**; TRAIN 68.69% against 68.96%, net
−$4.5982. No historical work repeated — the §8 test that had never been run.

**Check 2 — threshold reconciliation: done, and it reversed my first
answer.** I implemented the tightening (controller route bound by Route B's
0.9532), measured it, and **reverted it**. As a hard ceiling 0.9532 refuses
the completions the separation exists to rescue: 1245z held 2.27 UP for
$0.7264 and could complete DOWN at an effective pair of 0.9699, locking
**+$0.0683**; stranding it realised **−$0.7264**. A 0.9532 ceiling refuses
that — a **$0.7947** swing the wrong way.

So the reconciliation is:

* **the hard economic limit is `eff pair ≤ 1.00`**, at the submission
  boundary (`pair_cost_refusal`), because a matched pair redeems exactly
  $1.00 and anything below par is a locked gain;
* **0.9532 is a preferred TARGET**, Route B's default, not a ceiling —
  which is what its own docstring says ("a DEFAULT, overridable by the
  caller, never a second copy of a gate");
* both routes now read **one constant**, `l5_two_sided_probe.BREAKEVEN_PAIR`,
  with `l5_book_completion.BREAKEVEN_PAIR` aliasing it. No second copy to
  drift.
* it is sourced from our own ledger: achieved pair cost ≈ **0.908**, inside
  both numbers, so neither binds on the pairs we actually form. The expected
  cost of carrying unmatched inventory is **−0.185458/unit**, measured.

**Raising Route B's default toward 1.00 is the open question and is NOT
done here** — it loosens tolerance and needs explicit authorization.

**Check 3 — the fix cannot misbehave: 13 tests.**

| property | mechanism |
|---|---|
| cannot invent an L5 action | `row=None`, `reused_action_key=None`, provenance `CONTROLLER_GENERATED`; never writes `_last_action_row` |
| cannot double-submit | resting orders passed for idempotence; 5 s cadence stamped even on refusal; deterministic `controller_action_key` → same `client_order_id` → `already_submitted` suppresses |
| cannot exceed one order | `ORDER_ALREADY_RESTING` declines *before* re-entry, asserted to precede it in source order; the executor's `MAX_LIVE_ORDERS` gate still applies on top |
| cannot disturb cancellation | places through the ordinary path, so the order lands in `live_orders` with its venue id and `owns_venue_order_id` resolves it; the method contains no `cancel`/`deadman` |
| cannot disturb reconciliation | contains no `reconcile_from_venue`/`adopt_venue_position`/`retire_settled_market` |
| fails closed | unreadable ledger → `LEDGER_UNREADABLE`, not "flat" |
| still inert | `BC.enabled()` checked before anything |

**Check 4 — baseline parity: confirmed.** 14452 passed against a 14344
baseline. Failure set **unchanged**: 54 failed / 23 errors across the same 19
modules (pre-existing; gemini/spread-scanner, largely a missing `httpx`;
none in the money path). 34 tests in the new module; one superseded invariant
restated, not deleted.

## Bounded live validation — proposed, requires separate authorization

Nothing is armed and no campaign exists. Limits are the already-approved ones,
unchanged.

| | |
|---|---|
| markets | 1 |
| windows | 3 |
| execution | `MAKER_ONLY`, defensive crossing **disabled** |
| total exposure ceiling | **$20** |
| per-market worst-case deficit | **$3.00** |
| daily realized-loss halt | **$5** |
| resting orders | 1 |
| flags | `L5_CONTROLLER_OPEN=1`, `L5_BOOK_COMPLETION=1` |
| pair cap | **0.96, unchanged** |

### What is measured, and the labels are strict

| measure | definition |
|---|---|
| **complements submitted** | venue-ACKed orders on the complement side. A quote opportunity is **not** a submission |
| **actual maker fills** | venue `cumQuantity` > 0 on a post-only order, with `commission` recorded |
| **matched pairs** | `min(up_qty, dn_qty)` from the ledger after reconciliation, with the effective pair cost |
| **residual inventory** | `|up_qty − dn_qty|` at window close, and its cost basis |
| **settled net P&L** | `retire_settled_market` against an authoritative binary settlement only |

**Quote opportunities are not fills, executable is not filled, and modelled
pair profit is not realized.** The only number that counts as a result is
settled net P&L on a venue-confirmed matched pair.

### Success and stop criteria

* **Success:** ≥1 venue-confirmed **matched pair** on controller-opened
  inventory, settled, with the effective pair cost recorded. One pair is the
  milestone — the programme has never produced one.
* **Informative null:** complements submitted and none filled. That is the
  first real joint-maker-fill datum on this venue and replay cannot supply
  it.
* **Stop:** any automatic halt; `book_completion:REENTERED:CONTROLLER` still
  0 while inventory is held (the fix did not engage — a defect, not a
  result); or any ambiguous submission.

The objective is **repeatable positive settled pair economics**, not another
successful order submission.
