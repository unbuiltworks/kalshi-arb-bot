# LIVE-L5-EXEC-1-X — EXECUTION-CAPABLE, DEPLOYED, DISARMED

**NEW FROZEN FINGERPRINT**
`d2f322760699f22e50a16975179a484b48c1523e3a667a77579ed6c5dd3a6c79`

Derived from EXEC-1 `b1dbe7f1e628e6f9fc8e6459a2a5b95098ada2f16ae872f10081d5dc9bb82e06`,
which is unchanged and re-verified at every boot. No order submitted, no
arming keys set, no self-arming. matched-pair economics **N/A**.
**This fingerprint is not yet authorized. It needs its own authorization.**

## The exact diff from EXEC-1

The spec is not retyped. `analysis/l5/live_exec2/spec.py` deep-copies EXEC-1's
`SPEC` and applies a literal `DIFF`; `diff_report()` computes the change set,
and a test asserts every EXEC-1 key `DIFF` does not touch is byte-identical.

**4 keys changed:**

| key | EXEC-1 | EXEC-1-X |
|---|---|---|
| `probe` | `LIVE-L5-EXEC-1` | `LIVE-L5-EXEC-1-X` |
| `spec_version` | 1 | 2 |
| `behaviour.no_second_order` | `True` | `ENFORCED_BY_DURABLE_LATCH` |
| `deadman.mechanism` | market-scoped cancel-all in a finally block | …**via POST /v1/orders/open/cancel** |

**2 keys added (the two authorized changes)** — `latch`, `mutation_path` —
**plus 5 provenance keys**: `derived_from_probe`,
`derived_from_fingerprint`, `experiment_unchanged`,
`loss_envelope_unchanged`, `does_not_contaminate.exec1_preflight_artifact`.

**0 keys removed.** `max_possible_loss_usd_unchanged: "0.0099"`.

20 `INVARIANTS` are asserted at import and at every boot, each with its own
test: quantity `0.01`, max loss `$0.0099`, max entry price `0.99`, one
market, one resting order, one order total, post-only, taker refused,
`pair_cost_reservation: None`, matched economics N/A, 2.0 s staleness bound,
AuthBook source, TTE 120 s, daily stop −1.00, `armed_by_default: False`.
Two tests prove the guard is not decorative: changing the quantity to 1.00,
or `max_orders_this_experiment` to 2, raises `SystemExit`.

## (1) The durable latch

`analysis/l5/live_exec2/latch.py`, at `/state/pmus-ws/exec1x` on the mounted
volume, keyed by the owner's `PMUS_EXEC1_SESSION_ID`.

* **Consumed at the first ACTUAL submission attempt**, immediately before the
  POST — not on success.
* **Not released if the POST fails, times out, or the process dies.** That
  direction is deliberate: a submission with an unknown outcome may already
  have rested a real order, so retrying it could leave two live orders and
  double the authorized exposure. An unknown outcome is treated as spent, and
  the experiment stops and is reported rather than retried.
* **Atomic** (`O_CREAT|O_EXCL`), so two racing processes cannot both win.
* **Fails closed** if the store is unwritable — it never falls back to the
  in-memory latch it replaces.
* **No release, clear, reset or delete function exists**, and a test asserts
  the module contains no `os.unlink`/`os.remove`.

A fresh session needs the owner to choose a new session id. There is no other
way to get one.

EXEC-1's in-memory `OneOrderOnly` is kept as the inner bound; the durable
latch supersedes it as the *lifetime* guarantee, so exactly-one-order no
longer depends on `restartPolicyType = NEVER` to hold.

## (2) The minimum order path

`analysis/l5/live_exec2/orders.py`. Four POST routes, and only four:

```
/v1/order/preview   /v1/orders   /v1/order/{id}/cancel   /v1/orders/open/cancel
```

Anything else raises `MutationRefused` inside the transport, before a socket
opens. Eight foreign routes were tried at boot and all eight were refused.
There is no generic POST here to be reused, and a second `POST /v1/orders` in
one process is refused independently of the latch.

Submission additionally requires, all of them: owner arming; the gate naming
this exact slug; `qty == 0.01`; price on the venue's own tick grid and at or
under 0.99; a fresh two-sided AuthBook inside 2.0 s; a local would-cross
refusal (a buy at or above the live ask never leaves the process); and a
preview the venue echoed back unaltered.

Cancel is deliberately *not* gated on arming or the latch — refusing to
cancel because a flag is unset is the dangerous direction.

Still absent, with a blocking gate that fails if any becomes importable:
`pmus_q1_executor`, `pmus_micro_executor`, `l5_pmus_transport`.

## Deployment — 27/27 gates PASS, 0 blocking failures

| | |
|---|---|
| Service | `l5-live-probe-oneshot` |
| **SHA** | `536aa40732d7880195ac7d8eb47b0a29a47bf6d8` (pinned) |
| Dockerfile | `deploy/l5_exec2_execution/Dockerfile` |
| Start command | `python3 -m scripts.l5_exec2_boot_preflight` |
| Deployment | `a0fdce1c-654b-45f4-a5b6-b6661a47f837`, SUCCESS, 2026-10-06T15:32Z |
| restartPolicyType | **NEVER** |

Host-verified: both fingerprints; envelope preserved; credentials present
**by presence only**; `minimumTradeQty` 0.01; `orderPriceMinTickSize` 0.01;
AuthBook 8/8 two-sided snapshots at 0.016–0.094 s with the BBO moving
0.34/0.35 → 0.32/0.33 → 0.37/0.38 → 0.45/0.46 → 0.42/0.43; observed spread
exactly 0.0100; durable latch written, refused on second consume, and seen
spent by a fresh reader with no in-memory state; the owner's session latch
untouched; no other order-capable process; eight foreign routes refused.

**The strongest single result.** On the host, with a real market and a real
fresh book, the live submission preflight returned:

```
submission_path_refuses_while_disarmed  PASS  {'ok': False, 'refusals': ['DISARMED']}
```

Every other condition passed — size, grid, book freshness, two-sidedness.
`DISARMED` was the *only* refusal. So the proof is not that something else
happened to be wrong: the absence of the owner's four keys is, on its own,
what stops the order.

### On preserving SHA 882aa564

Deployment `163cf6a9` and its 22/22 preflight log are intact, rollback-able
in one redeploy, and its results are committed at
`analysis/l5/live_exec/EXEC1_DEPLOYMENT_PREP.md`. The *running process* on
that service is now EXEC-1-X, because that service is the only one holding
the PMUS credentials and the `/state/pmus-ws` volume the durable latch needs
— and copying credentials to a new service was not an option I would take.
If you want both running at once, a second credentialed service is needed and
only you can put the credentials on it.

## To arm (unchanged four keys; Claude has set none of them)

`PMUS_EXEC1_LIVE=YES`, `PMUS_EXEC1_SESSION_ID=<new id>`,
`PMUS_EXEC1_ARM=<same id>`, `PMUS_EXEC1_GATE_EVIDENCE=<exact slug>`.

Choose a session id not used before: a reused id whose latch is already spent
fails closed, which is the intended behaviour, not a bug.
