# Stage 1 preflight — $10 cash, $1 worst-case deficit

**VERDICT: FAIL.** One blocking defect in the deployed configuration, plus
the pre-existing economic blocker. Proposed parameters only; nothing here is
authorization and nothing was deployed, armed or changed.

## 1. Merge dependencies, parity, deployed SHA, production configuration

**PR SHA:** `fd7a8c4e9` (code at `f81bd4c5f`; the later commit is
documentation only).

**PR #208 sits on a seven-deep unmerged stack**, and its root is a draft:

```
#208 fix/l5-reopening-decision
 └─ #205 feat/l5-controller-authority
     └─ #204 feat/l5-unmatched-disposition
         └─ #203 feat/l5-queue-depletion-estimator
             └─ #202 fix/l5-pair-aware-stale-recovery
                 └─ #200 fix/l5-requote-pair-ceiling
                     └─ #197 fix/l5-issue195-rollover-...   ** DRAFT **
                         └─ claude/compassionate-allen-wic64u   (not main)
```

**Deployed SHA: `26dbfa119`**, deployment `9ee6b0cc`, branch
`deploy/l5-controller-stack` — a snapshot built from the stack at
`2004acba4`, **not** from any merged PR. So none of #197–#208 is in
production, and Stage 1 at this code would require a new deploy branch or
the whole stack merged. Either is a deployment.

**Production configuration**, heartbeat 2026-11-07T19:11:58Z:

| | value |
|---|---|
| `submission_gate.blocked` / `shadow_lock` / `halt_local_env` | **true / true / true** |
| `armed` | **false** |
| live orders · unresolved accounting | **0 · 0** |
| `authorization_preflight.ok` | **false** — fails `shadow_lock`, `absolute_deadline`, `campaign_approval` |
| `effective_cash_gate.max_cash_deployed_usd` | **12** |
| `effective_cash_gate.cash_policy` | `NO_RECYCLING` |
| `effective_cash_gate.max_unmatched_contracts` | 5 |
| `effective_cash_gate.max_usd_per_market` | 20 |
| `limits.max_total_exposure_usd` | 1.00 |
| `limits.max_live_orders` · `max_concurrent_markets` | 2 · 1 |
| `limits.daily_realized_loss_halt_usd` | 5 |

### THE BLOCKING DEFECT

**`max_worst_case_deficit_usd` is absent from the deployed configuration.**
`effective_cash_gate` enumerates the gates in force and it is not among
them. The gate is written `if self.max_worst_case_deficit_usd is not None:`
— so **with it unset the entire worst-case-deficit check does not run.**

Consequence: the proposed "$1 maximum worst-case deficit" **does not
currently exist in production.** The deployed per-market loss ceiling is
`max_usd_per_market: 20`, twenty times the proposed bound, and
`max_total_exposure_usd: 1.00` bounds a different quantity (gross exposure,
not `C − min(up, dn)`).

The code is correct and tested; **the configuration does not enable it.**
That is the difference between "the executor can enforce this" and "the
executor does enforce this", and item 2 asks for the latter.

## 2. Both limits in four states — code verified, config not

`tests/test_l5_stage1_preflight.py`, 28 tests, all passing:

| state | evidence |
|---|---|
| at submission | committed ≤ $10 and the modelled deficit ≤ $1 on a live run |
| after a partial fill | a deepening order's deficit exceeds $1 and the executor refuses it |
| during a cancellation race | `BOOK_UNCERTAIN`, zero submissions |
| after restart | position and basis re-read from the volume; the cash bound still holds; an unreadable book gives `WORST_CASE_DEFICIT_NO_INVENTORY` |

**All four pass against `ValidationGates` configured with both bounds. None
of it is in force in production until `L5_MAX_WORST_CASE_DEFICIT_USD` is
set.**

## 3. PRESERVE retains the historical complements — PASS

| window | held | resting | pair | verdict |
|---|---|---|---|---|
| 0115z | DOWN 0.24 | UP 0.70 | **0.94** | **PRESERVE** |
| 0145z | DOWN 0.35 | UP 0.61 | **0.96** | **PRESERVE** |

Both were cancelled live at rest 16.62s and 16.79s — **above** both rest
floors (15.0 and 14.0), so the floors do not reach them and PRESERVE is what
does.

## 4. A pair above redemption is rejected, fees included — PASS

The completion is priced as
`eff = (leg_cost_UP + leg_cost_DOWN + px·qty) / matched_after` — **cash
actually paid** over matched quantity. For maker-only that is fee-inclusive
by construction: the published schedule has **no maker-fee term**, and a
zero commission is not a rebate, so none is added.

`completion_max_pair()` defaults to `max_pair_cost()` = **0.96**, not 1.00.
PRESERVE itself goes **infeasible** above redemption with
`RESTING_PAIR_ABOVE_REDEMPTION` (verified at 1.05 and 1.20). The looser
1.20 defensive ceiling exists only for a **mandated unwind** and is
unreachable with crossing disabled.

## 5. Nothing left resting or uncertain at the close — PASS

`cancel_cutoff(900) < 900`, `entry_cutoff_s() = 600`,
`complete_cutoff_s() ≤ 900`: an order cannot outlive its window. The
`maybe_acc_deadline` overdue path is **mandatory** and does not consult the
discretionary controller verdict. Any uncertain store blocks every
submission.

## 6. Watchdog, daily halt, exact cancellation, settlement — PASS with one caveat

Daily halt is a **third independent bound**, refusing on settled realized
loss alone (`DAILY_REALIZED_LOSS_HALT` at −$5, not at −$1). Cancellation
names an exact `venue_order_id` and verifies the terminal state.
`CANCEL_UNRESOLVED` is its own refusal. Settlement attributes only from
`realized_attributed` + `retired_markets` under a venue price.

**Caveat:** watchdog *intervention* is exercised in deployment verification,
not offline. Its health is observable in production; its action is not
covered by this suite.

## 7. Modelled deficit versus guaranteed maximum loss

**Where the model is a genuine bound.** `C − min(up, dn)` is evaluated
assuming the proposed order fills *at its limit price* — sound for a maker
buy, since the venue cannot fill a limit order worse than its limit — and
maximised over **all four corners** of which outstanding orders fill
(nothing / UP only / DOWN only / everything). That is exact, not
conservative. Fees are charged at the **taker** theta even for a maker
order, which over-states cost on purpose.

**The one gap, and it is not priced — it is refused.** The model is exact
over the book it can *see*. An order the venue acknowledged but our ledger
has not reconciled is outside it. That is precisely the state
`orders_uncertain` names, and it refuses every submission until an
authoritative read clears it. So the gap is bounded by declining to add to
it.

**Residual gaps that remain real:**

- **Stale book data.** Prices come from a snapshot with `stream_age_s`; the
  gates refuse above `MAX_BOOK_AGE_S`, but a fill can still land between
  snapshot and decision. This cannot move a *maker* fill price against us —
  only our estimate of the touch.
- **Latency.** Between decision and acknowledgement another fill may land.
  Bounded by `max_live_orders: 2` and by every gate pricing outstanding
  orders as if they filled.
- **Venue behaviour.** A partial fill is handled; a fill at a *better* price
  only helps; settlement is binary so there is no price uncertainty. The
  unmodelled case is a venue-side fill never reported, which is the
  unreconciled-order gap above.

**So: the modelled deficit is a guaranteed maximum loss only while the
ledger is complete.** When it is not, the system refuses rather than
bounding. That is the correct design and it is also the honest limit of the
guarantee.

## 8. One-window authorization specification — what would be needed

**Not a request. Nothing below is authorized.**

| parameter | value |
|---|---|
| venue / series | PMUS BTC15m, **one market** |
| windows | **exactly one**, named explicitly at approval time |
| mode | **maker only**; taker switches remain off |
| max cash commitment | **$10** (`L5_MAX_CASH_DEPLOYED_USD=10`) |
| **max worst-case deficit** | **$1** (`L5_MAX_WORST_CASE_DEFICIT_USD=1`) ← **not currently set** |
| max live orders | 2 |
| concurrent markets | 1 |
| daily realized-loss halt | $5 (already configured) |
| expiry | `L5_LIVE_AUTH_NOT_AFTER` ≤ 20 minutes past the window close |
| campaign | fresh document, `window_starts` = the one window, nonce supplied by the owner |
| disarm | **automatic** at window close and at the absolute deadline |
| rollover | **none** — no second window on any outcome |
| stopping rules | immediate stop on any unresolved cancel, any `BOOK_UNCERTAIN`, any watchdog trip, a residual whose deficit exceeds $1, or the daily halt |

**Required evidence afterwards:** every order reconciled by venue id to a
terminal state; `L5_LIVE_SETTLED` with `VENUE_SETTLEMENT_PX`; matched-pair
and residual P&L reconciled **separately**; fees recorded apart from P&L
with no rebate counted until credited.

**Authorization that would be needed, in order:**

1. **Lift the deployment freeze** for one disarmed deploy of this code —
   seven PRs merged, or a new deploy branch from `f81bd4c5f`.
2. **Set two Railway variables**: `L5_MAX_CASH_DEPLOYED_USD=10` (from 12)
   and `L5_MAX_WORST_CASE_DEFICIT_USD=1` (**from unset**).
3. **A fresh campaign approval** naming the single window, plus
   `L5_LIVE_AUTH_NOT_AFTER`.
4. **Lift `L5_SHADOW_ONLY` and `L5_LIVE_HALT`** for that window only.

## Unresolved issues

1. **`max_worst_case_deficit_usd` is unset in production** — the proposed
   $1 bound does not exist until it is configured. **Blocking.**
2. **The residual's sign** is measured negative at ~2 points of win rate on
   1.24M of L5's contracts, 51% of windows negative, wins recovering 82.9%
   of losses. At Stage 1 size ~10 pairs earn **+$0.35** while the deficit
   bound allows **$1.00**, so the risk still exceeds the modelled edge.
   **Economic, not code.**
3. **No rebate has ever been credited**, so every figure is gross of an
   incentive that may not exist for this account.
4. **Watchdog intervention is not covered offline.**
5. **PR #197 is a draft** and roots the whole stack, which bottoms out on a
   feature branch rather than `main`.

**Our confirmed settled realized net remains −$15.6109 all-time.**
