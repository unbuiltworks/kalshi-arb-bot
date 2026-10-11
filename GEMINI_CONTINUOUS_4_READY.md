# Unarmed production-path verification — CONTINUOUS-4 readiness

Run [35372](https://github.com/unbuiltworks/kalshi-arb-bot/actions), artifact
`analysis/gemini/continuous_unarmed_20260918T171024Z.json`, 17:10:24 → 17:10:25Z.

This is **`run_continuous` itself** — the same entry point the armed step calls
— with real credentials, the real metadata readers, the real authoritative
account reads and the real recovery. No arm, no authorization installed.

**Terminal state `READY_TO_MUTATE_BUT_UNAUTHORIZED`. Zero frames. No latch.**

## The eight proofs

| # | required | evidence |
|---|---|---|
| 1 | historical slot 2 resolved | `ledger_unresolved: []`, `ledger_consumed_slots: 3` |
| 2 | recovery succeeds from ledger + venue | `pre_latch_recovery.recovered: true` |
| 3 | budget starts correctly | `budget_consumed: 3`, `budget_left: 9` of 12 — `max(ledger 3, venue 0)` |
| 4 | inventory / open orders authoritative | `yes_qty 0, no_qty 0, n_fills 0`; `working: "venue authoritative: 0 resting"`; history `readable: true`, `total_rows 0` |
| 5 | normal preflight gates pass | `agrees: true, discrepancies: []`, on `GEMI-BTC05M2609181715-UP` at TTE 275.4s, active/open |
| 6 | authorization is the sole blocker | `why_not` lists only `REQUIRED_AUTH_ID is None`, `AUTHORIZED_SHA is None`, and the transport arm |
| 7 | zero frames sent | `wire: null`, `place_acks_observed: null`, `session: null` — no session was ever constructed |
| 8 | no latch consumed | `latch.consumed: false`; the job additionally fails if `git diff` on the latch store is dirty, and it was clean |

## The reconciliation that unblocked it

Slot 2 of CONTINUOUS-2 — the CANCEL the venue rejected `400 / -1013 invalid
orderId format` — is closed on venue evidence, not assumption:

- **Definitive rejection.** A 4xx carrying an error code is the venue stating it
  validated the request and declined it. Silence would not have qualified.
- **Still open afterwards.** The venue reported the targeted order
  `145828836242288478` `status: open`, `filledQuantity 0` at 14:31:11Z — after
  the 14:31:09.556Z rejection.
- **Cancelled by the venue at expiry.** Order history shows both orders
  `filledQuantity "0"`, `remainingQuantity "0.01"`,
  `cancelledAt 2026-09-18T14:35:00.164Z` — the contract's expiry, 3m51s later.
- **Nothing dangling.** `active_orders: []`.

The RESOLVE row is **appended**; the original rows are byte-for-byte unchanged,
the slots stay SPENT (`consumed` is still 3), and a second untouched UNKNOWN
would still block — asserted by test.

## The defect this surfaced, which is the important part

`order_history` aggregates its rows into counts and samples and **never returns
the list**. Both the reconciler and `run_continuous.authoritative_state`
extracted `per_status[].orders` — a key that is never produced. It returned `[]`
on every call, so:

- `fills` was always `[]`, making *"a restart never assumes flat"* quietly untrue;
- `orders_seen` was always `[]`, silently degrading `max(ledger, venue)` to
  ledger-only.

**Three live windows ran against that.** It surfaced only because the
reconciliation reported `total_rows: 2` next to zero extracted rows, and that
contradiction was recorded instead of averaged away.

Fixed: `order_history` gains `include_rows`, default `False` so no existing
caller changes; `authoritative_state` opts in and now treats readable-but-not-
extractable as **UNREADABLE**, which makes recovery refuse rather than assume a
flat account. `metadata.py` joined the bundle.

## Hashes

```
executable_sha  842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4   UNCHANGED
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   UNCHANGED
bundle_sha      7e31d5c305f3b0c1695e3b2dbe38162ca63acf60efa6bfbc7dfb979c0ff6e0b4   NEW
```

`executable_sha` is unchanged because **the driver did not change** — no
strategy, controller, sizing or risk-envelope edit. Every fix landed in the
invoker and the readers, which `executable_sha` cannot see. That is precisely
why `bundle_sha` now exists: it covers `ws_continuous`, `run_continuous`,
`continuous_probe`, `continuous_preflight`, `metadata`, `arming` and `bundle`
itself, each with its authorization constants normalized away.

Ordering now pinned by test: **preflight → authoritative read → recovery →
authorization → bundle → latch → socket.** Every read is free; the latch is
last. A known-stale ledger refusal can no longer spend an authorization — pinned
behaviourally in both directions, since a gate that always refused would pass
the refusal test and block every window forever.

## Exact authorization statement needed for CONTINUOUS-4

> I authorize ONE bounded live Gemini BTC5m continuous-inventory window using
> authorization ID `OWNER-2026-09-18-GEMINI-CONTINUOUS-4`, executable SHA
> `842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4`, envelope
> SHA `b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17`, and
> bundle SHA `7e31d5c305f3b0c1695e3b2dbe38162ca63acf60efa6bfbc7dfb979c0ff6e0b4`.
> No second window, rollover, scaling, SELL, IOC/FOK, taker crossing, forced
> flatten, strategy/controller changes, or risk-envelope changes without new
> approval.

Envelope unchanged: 12 actions, 0.01 size, $0.15 max spend, 0.04 residual and
imbalance, 0.10 per leg, no new open/extend inside 60s TTE, withdraw-all at 30s,
`MOC` maker-only.

**Budget note for the next window: 3 of 12 slots are already spent** by
CONTINUOUS-2 and the ledger is durable, so CONTINUOUS-4 starts with **9**. That
is the mechanism working, not a defect — but it is worth knowing before the
window, not after.

One known cosmetic gap: on an unauthorized run the `bundle` detail block is not
populated, because the authorization gate returns before it. `bundle_sha` is in
the report header either way. Not fixed, because fixing it would move the very
hash being authorized.
