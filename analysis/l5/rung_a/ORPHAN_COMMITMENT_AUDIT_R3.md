# Orphan commitment audit — OWNER-2026-09-28-NATIVEQ10-R3

The 2345z window admitted one order against the mandate and then died before
the POST. This establishes exactly what is persisted and proves a fresh
authorization cannot inherit or count it.

## What is written, and where

`GuardedTransport.request`'s POST branch calls `admit()` **before** the wire
and `link_order()` **after** it. `admit()` -> `_admit_locked` appends to
`<state_dir>/ledger.jsonl` under an exclusive `flock`:

```
{"t": "<iso>", "kind": "mandate_commit",
 "authorization_id": "OWNER-2026-09-28-NATIVEQ10-R3",
 "commit_id": "OWNER-2026-09-28-NATIVEQ10-R3:cpc-btc-updown-15m-2026-09-28-2345z:<epoch.6f>:10.0",
 "slug": "cpc-btc-updown-15m-2026-09-28-2345z",
 "cash_at_risk": "<10 x price>", "quantity": "10.0",
 "price_wire": "<wire price>", "side": "OUTCOME_SIDE_YES|NO",
 "action": "ORDER_ACTION_BUY",
 "labels": "committed BEFORE the venue call; charged in full until a
            terminal row proves a smaller fill"}
```

`state_dir` is `os.path.dirname(L5_PROBE_STATE)` = **`/state/pmus-ws`**, so
the file is **`/state/pmus-ws/ledger.jsonl`** and it is SHARED by every run on
that volume. No `mandate_order` (LINK_KIND) row exists for that `commit_id`,
because `link_order` runs only after the POST returns and the POST raised
`AttributeError` first.

`quantity` is `10.0` (the report's `paired.submitted_qty`). The exact
`cash_at_risk` and `price_wire` are not in the Railway logs -- `_emit` was
wired to the journal rather than stdout on this run -- so they are on the
volume only. The commitment's SIZE is bounded regardless: one q10 intent
under a $81 per-market cap.

## Why it is charged forever, deliberately

`consumption()` charges every `mandate_commit` at its full proposed notional
until an `order_terminal` row from venue reconciliation proves a smaller
fill. A commitment whose outcome was never learned therefore keeps costing
its full size. That is the safe direction and is not a bug to clean up: it
is what stops an ambiguous submission quietly freeing budget.

## A fresh authorization cannot inherit it — PROVEN, NOT ASSUMED

`consumption(state_dir, authorization_id)` filters:

```python
if k == COMMIT_KIND and r.get("authorization_id") == authorization_id:
```

and the same for LINK_KIND. So commitments are scoped to the authorization
that made them, over the same ledger file.

Executed, not read off the source —
`tests/test_production_composition_submit.py`:

* `test_a_submit_that_dies_after_admission_leaves_an_ORPHAN_commitment`
  reproduces the exact failure (the POST raises the same AttributeError),
  then asserts: **one** `mandate_commit` row persisted, **zero**
  `mandate_order` rows, `consumption(dir, "OWNER-R3-ORPHAN").total == 4.00`
  (10 x 0.40), and `consumption(dir, "OWNER-2026-09-29-NATIVEQ10-R4").total
  == 0` with an empty `per_market` — **over the same ledger file**.
* `test_a_fresh_authorization_can_still_spend_its_whole_envelope` goes
  further than arithmetic: after the orphan is written, a run under a new
  `authorization_id` is admitted and reaches the wire.

## Consequence for the next run

Use a NEW `authorization_id`. Reusing `OWNER-2026-09-28-NATIVEQ10-R3` would
start with that orphan charged against its envelope, which is correct
behaviour and not what we want. No ledger edit is needed or wanted: the
orphan stays as evidence, scoped to a spent authorization.

**Not verified from the volume itself.** Reading
`/state/pmus-ws/ledger.jsonl` needs a deployment, which is not authorized.
Everything above is established from the code that wrote it and from
executing that code, and the one field I cannot state exactly
(`cash_at_risk`) is named as such rather than guessed.
