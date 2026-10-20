# Revised submission budgets — for approval

Supersedes the flat `max_submissions = 8` in
`CAMPAIGN_20261020_BOUNDED_FOUR_WINDOW.md`. **Not activated.** Live
submission stays structurally blocked (`L5_SHADOW_ONLY=1`).

## 1. Why the flat 8 was the wrong shape

A single total treats every order as the same act. It is not. L5 works
inventory sequentially — **OPEN → EXTEND → EXTEND/WAIT → COMPLETE → keep
working** — so a flat total is consumed by the orders that *take on* risk and
then has nothing left for the orders that *unwind* it.

The budget that runs out first would be the one that completes a pair. That
is the exact failure the exposure ceiling cannot fix: an exhausted completion
budget strands unmatched directional exposure, and no limit can then unwind
it. Eight total would also have forbidden the behaviour we have live evidence
for — `accumulation_round` reached 3 in a single window on 2026-10-20.

## 2. The window mechanics the numbers come from

Measured from the code at the deployed configuration
(`L5_TS_CANCEL_MARGIN_S=300`, `WINDOW_S=900`):

| Mechanic | Value | Source |
|---|---|---|
| hard terminal, nothing may rest past | T+600 | `cancel_cutoff = t_end − 300` |
| new exposure-increasing leg permitted | T+0 … T+539 (540s) | `may_enter`: `ENTRY_CUTOFF_S=600` ∧ `terminal − MIN_REST_S(60)` |
| complement permitted | T+0 … T+599 (600s) | `may_complete`: later than entry, because completing removes risk |
| per-order TTL | 120s | `ORDER_TTL_S` |
| concurrent orders | 1 | `max_live_orders` |

**Serialization is the real ceiling.** One order at a time with a 120s TTL
over a 600s usable window gives **5 order slots per window** if every order
rests to TTL. Entry-eligible slots: ⌊540/120⌋ = **4**. Replacements only
exist *inside* those slots, when a round is retired early on a drift of ≥ 6
ticks — observed ~3 times in a live window.

## 3. Proposed budgets

Three separate durable budgets, charged by what the order **does** to
|prospective unmatched| (realized inventory **plus every outstanding order as
if it filled**):

| Class | Definition | Per window | Campaign (4 windows) | Basis |
|---|---|---|---|---|
| **OPEN** | increases \|prospective unmatched\| — first leg or same-side EXTEND | 3 | **8** | L5's OPEN + up to 2 EXTEND; the 4th entry slot is left for completion so a window cannot spend every slot on exposure. 8 campaign-wide is a *backstop*: the $5 worst-case ceiling admits only ~2 simultaneously unmatched legs at the observed 2.46 clip ($2.21 worst case each), so the exposure cap binds first. |
| **COMPLETE** | strictly reduces \|prospective unmatched\| | 4 | **12** | one complement per OPEN leg, plus one re-derivation after a partial fill. **Deliberately larger than OPEN** — completing inventory must never be the budget that runs out. |
| **REPLACE** | re-price of a leg already placed (attempt > 0) | 5 | **16** | one early re-price per serialization slot per window. Also bounded independently by `L5_ACC_MAX_ATTEMPTS=4` per leg per round. |
| *overall backstop* | any class | — | **28** | deliberately **below** 8+12+16 = 36, so no two classes can both run to their ceiling. Not the operative limit. |

**Cancellations are not budgeted, at all.** A bound that could refuse a
cancel is a bound that traps an order on the venue. Only the *replacement
submission* after a cancel is charged.

## 4. The safeguards that make a looser COMPLETE budget safe

Evaluated at the common submission boundary, so they bind whichever engine
chose the order, and **before any claim or ticket is spent** — a refusal
costs nothing.

1. **`BALANCING_DOES_NOT_REDUCE`** — an order charged to COMPLETE must have
   `|prospective after| < |prospective before|`. An order in the
   risk-reducing budget that does not reduce risk is refused.
2. **`BALANCING_WOULD_OVERSHOOT`** — it may not cross zero. A complement
   larger than the remaining unmatched need would retire one imbalance and
   open an equal one on the other side: the same exposure, with another pair
   of fees paid for it. The refusal names the maximum quantity that would
   have been allowed.
3. **Cash and outstanding commitments** — unchanged, enforced account-wide at
   the cash gate (`filled + still-fillable + this order`) and at
   `max_live_orders` (which fails closed). Deliberately *not* duplicated in
   the safeguard: a second copy of that arithmetic is what produced the
   phantom `CASH_COMMITMENT:5.9760>4.65` by counting a filled leg twice.
4. **Cannot create excess inventory if all outstanding orders fill** — this
   is the *basis* of every figure above, not a separate check: the
   prospective book counts resting orders as filled.

## 5. Durability

Each class's consumption is a sequence of single-use ticket locks
(`cls1.<experiment>.<CLASS>.<n>`) on the same volume as the auth ledger,
taken `O_CREAT|O_EXCL` and fsynced **before the wire**. A redeploy, crash,
cancelled order, rejected order or ambiguous acknowledgement all leave the
ticket spent. Unset budget = not binding; **unreadable budget = zero**, never
unlimited. A class whose budget exists but which the caller does not name is
refused (`SUBMISSION_CLASS_NOT_STATED`) — an order nobody can charge is an
unbudgeted order.

An exhausted class does **not** consume the leg's own `(market, round, leg)`
slot, so the same intent can be placed later under a replenished budget
rather than being lost twice.

## 6. Unchanged

$5 total exposure · $5 daily realized loss halt (read against
`realized_current_utc_day()`, account-wide) · $0.96 maximum completed-pair
cost (plan time **and** against actual fill prices) · 4 windows · 4 hours
from issue · 1 outstanding order · 120s TTL · exact-order-ID cancellation ·
venue reconciliation before arming · independent HTTP halt latch (absent
latch = refusal) · watchdog cannot submit · residual cap ·
unresolved-accounting refusal · settlement-only realized labelling.

**No limit was increased.** The three budgets replace one total with three
tighter, purpose-scoped ones plus two new refusals.

## 7. Environment variables these become

```
L5_LIVE_AUTH_MAX_OPEN=8
L5_LIVE_AUTH_MAX_COMPLETE=12
L5_LIVE_AUTH_MAX_REPLACE=16
L5_LIVE_AUTH_MAX_SUBMISSIONS=28
```

Set once per campaign, with the approval document. Published on every
heartbeat as `campaign.class_budgets` with spent/remaining per class.

## 8. Awaiting approval

These figures, then: deploy with live trading off → verify health,
reconciliation and the durable counters → generate the approval document →
activate.
