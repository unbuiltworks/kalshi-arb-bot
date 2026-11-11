# Bounded live defensive-completion campaign — readiness, 2026-10-29 22:50Z

Production **disarmed** (`PMUS_Q1_LIVE=NO`, preflight
`fail: ["owner_arm_token"]`). **No authorization is requested by this
document and the draft in §6 must not be installed yet.** §1 says why.

## 1. The campaign cannot produce evidence right now, and the reason is upstream

Defensive completion completes **unmatched inventory**. Live, from the
deployed service at 22:48–22:49Z, every ~13 seconds:

```
L5_TS_COMPLETION {"ok":false,"reason":"NO_INVENTORY_TO_COMPLETE",
  "market":"cpc-btc-updown-15m-2026-10-29-2245z","phase":"MAKER",
  "up_qty":"0","dn_qty":"0","still_fillable_orders":0,
  "cash_headroom":"12.00","worst_case_ceiling":"3.00","armed":false}
```

The chain, each link measured:

1. Defensive completion needs unmatched inventory → **`up_qty 0, dn_qty 0`**.
2. Unmatched inventory needs an opened first leg.
3. A first leg is opened inside `l5_live_execution.handle(row)`, whose only
   hot-path caller is the ingest loop — **one call per admitted L5 action**.
4. `ingest:admitted` has **never incremented**; `last_admitted_utc` is
   **null**.
5. The feed has served the identical 200-row page for **4h 54m**:
   `newest_row_event_utc` **17:54:50Z**, `ingest:polls_ok` **3,166**,
   `ingest:rows` **633,200**, `ingest:dup` **633,200**, `ingest:new` **0**,
   cursor `keys` **5,556** unchanged.

**A three-window live campaign today would arm, spend three of the
authorized windows, log `NO_INVENTORY_TO_COMPLETE` on every tick, and
submit nothing** — the same outcome as Campaign 9 (5 windows spent, 0
submissions). That is why I am not handing over an installable approval.

## 2. Requirement 1 answered: the execution changes are already built

The L5-row defensive path is **fully wired** and needs **no code change**.
`FaithfulExecutor.defensive_execute` already does, in this order:

> *"Cancel, prove by the venue's own re-read, re-read OUR OWN inventory,
> decide again against what is actually there, charge the budget, and only
> then issue a mandate naming that one order. Every failure returns
> `ok: False` and leaves the position exactly as it was."*

Mapped to the requirements:

| required | existing mechanism |
|---|---|
| executable opposite-side purchase on real ask depth, fees, cash | `DEF.decide` with `exit_price`, `displayed_depth`, `taker_fee_per_contract`, `cash_headroom` |
| partial completion | `DEF.alternatives` + `probe_leg_qty_cap`; overhedging is **refused, not resized in flight** |
| never assume a resting maker quote is executable | the resting complement is **cancelled and proven gone** before any cross |
| don't blindly cross above $1.00 | `pair_ceiling`, to be set **1.00** (not the 1.20 default — at 1.20 the bound arithmetic gives **−8.0931**, worse than doing nothing) |
| compare completion / holding / exit | `DEF.alternatives` prices `HOLD_TO_SETTLEMENT`, `MAKER_COMPLETE_AT_CEILING`, `DEFENSIVE_UNWIND` in dollars |
| hard worst-case loss limit | `DEFENSIVE_LOSS_BUDGET_USD` 2.00 + `defensive_charge` + `max_worst_case_deficit_usd` 3.00 |
| exact-order cancellation, terminal proof | `DEF.cancel_confirmed(result, coid)` |
| duplicate prevention | `store.already_submitted(coid)`; re-read of inventory after the cancel |
| registry / watchdog / reconciliation | unchanged: `l5_order_registry_server`, the independent watchdog, `L5_LIVE_RECONCILE` |

So requirement 1 is satisfied by configuration, not by new code:
`L5_DEFENSIVE_COMPLETION=1`, crossing permitted, pair ceiling 1.00.

## 3. The L5-independent path exists but is deliberately unwired — and wiring it would not help

`Runner.maybe_ts_completion` runs on the **timer**, not on an L5 row. It
computes the real executable price, displayed depth, taker fee and
worst-case settlement against holding, and is gated by
`L5_TS_COMPLETION_CROSS` plus an armed transport. But:

```python
if dec.get("ok") and authorized and t.armed:
    rec["crossed"] = "WOULD_SUBMIT"
    rec["submission_route"] = ("deliberately not wired: crossing requires
                               the owner's separate explicit authorization
                               for live taker execution")
```

Wiring it is a money-path change: a new send site spending
`claim_submission`, plus an edit to `tests/test_l5_one_submission_claim.py`,
which scans `src/` and fails if a third send site appears.

**But it would change nothing today.** Completion is **downstream of
opening**. With `up_qty 0, dn_qty 0` the timer path reports
`NO_INVENTORY_TO_COMPLETE` whether or not it can submit. I have therefore
**not** written that send: it would be a money-path change that cannot
produce the evidence it is for.

## 4. The fastest route to real-money evidence

Fix the feed. It is the only change that restores **openings**, and the
completion path behind it is already wired.

That is consistent with "L5 as an execution reference, not the strategy":
L5's action would only **trigger** the opening. The thing under test —
whether to cross, at what size, against holding — is **our own autonomous
decision**, which is exactly what the campaign is meant to validate.

The alternative route, autonomous opening via the controller adapter, is a
**NO-GO on its own evidence**: autonomous ARGMAX is **−$3.1400** over 38
windows, negative in every opening-price band, and no opening gate makes it
positive. Going live on it would convert modelled losses into realized ones.

The feed diagnosis is unchanged and needs one allowlisted host to settle:
`data-api.polymarket.com` (ideally also `polymarket.com`,
`gamma-api.polymarket.com`) under Network access → Allowed domains. The
three live hypotheses — a newly added proxy wallet, a venue move, or a
stale server-side view — demand different fixes and cannot be separated
from this session.

## 5. Current state for the record

| | |
|---|---|
| service / deployment | `l5-pmus-live` / `a9bbbd02`, SUCCESS, commit `c39183a3` |
| armed | **false** (`PMUS_Q1_LIVE=NO`) |
| preflight | `ok: false`, `fail: ["owner_arm_token"]`; all other rows PASS |
| prior approval `ac855ce3…` | **EXPIRED** at 22:20:15Z — must not be reused |
| venue | `open_orders_at_venue 0`, `pending_terminal_total 0`, adopted/quarantined empty |
| inventory | `up_qty 0`, `dn_qty 0`, `still_fillable_orders 0` |
| gates in force | cash $12.00, worst-case deficit $3.00, daily $5, L5 cumulative $20, account-wide $18.6325, `NO_RECYCLING`, halt gate configured, `journal_errors 0` |

## 6. DRAFT authorization — do not install until §1 clears

Proposed, not a mandate. Scope is **defensive completion only**; it does
**not** grant autonomous opening.

```json
{
  "approved": true,
  "nonce": "<owner-minted, unique>",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<at install>",
  "expires_utc": "<issue + up to 4h>",
  "max_windows": 3,
  "max_total_exposure_usd": "20",
  "max_daily_loss_usd": "5",
  "execution_scope": "DEFENSIVE_COMPLETION_ONLY",
  "defensive_pair_ceiling": "1.00",
  "defensive_loss_budget_usd": "2.00",
  "max_worst_case_deficit_usd": "3.00",
  "max_live_orders": 1
}
```

Owner settings alongside it: `L5_DEFENSIVE_COMPLETION=1`,
`L5_LIVE_AUTH_NOT_AFTER` = the campaign end, a **new experiment id**, and
`PMUS_Q1_LIVE=YES` last. Every other limit unchanged.

Halt conditions: stop after the **first** defensive crossing and reconcile
before a second; existing halt latch; `DEFENSIVE_LOSS_BUDGET_USD` 2.00;
daily $5; account-wide $18.6325.

Acceptance, reported separately as submitted / filled / settled / confirmed
realized: at least one venue-confirmed defensive completion with an
all-in pair below $1.00 and no window worse than −$3.00.

## 7. What I need from you

One decision:

- **(a) Fix the feed first** — cheapest route to real money evidence; the
  completion path is already wired and needs only §6. Requires the network
  allowlist entry so the identity/venue/cache question can be settled.
- **(b) Wire the timer path anyway** — a money-path change I can implement,
  but it cannot produce evidence until (a) or an autonomous opening exists.
- **(c) Both**, in that order.

I recommend **(a)**. I have not installed anything, requested
authorization, or written the unwired send.
