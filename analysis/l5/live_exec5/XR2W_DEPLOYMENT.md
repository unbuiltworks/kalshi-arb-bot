# LIVE-L5-EXEC-1-XR2W — DISARMED deployment

**Fingerprint `ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562`**

Service `l5-live-probe-oneshot`, project `arb-ev-wallet-shadow`, restart
policy **NEVER**, volume `156032e0` mounted at `/state/pmus-ws`.

## The first attempt FAILED, and why that is worth recording

Deployment `a487d54d` (SHA `7a206e0b2`) failed at **build**:

```
couldn't locate the dockerfile at path deploy/l5_exec1xr2/Dockerfile
  -  not found at deploy/l5_exec1xr2/Dockerfile
```

The Dockerfile path and the start command are **service settings**, not
only the `RAILWAY_DOCKERFILE_PATH` variable, and the service still held
the pre-rename values:

| setting | was | now |
|---|---|---|
| `build.dockerfilePath` | `deploy/l5_exec1xr2/Dockerfile` | `deploy/l5_exec1xr2w/Dockerfile` |
| `deploy.startCommand` | `python3 -m scripts.l5_exec1xr2_run` | `python3 -m scripts.l5_exec1xr2w_run` |
| `deploy.restartPolicyType` | `NEVER` | `NEVER` (re-asserted) |

`startCommand` **overrides the Dockerfile CMD**, so correcting the
Dockerfile alone would have built the new image and then run the old
entrypoint module — which no longer exists. Both were corrected together.

This failure is the good kind: it happened at build time, DISARMED, with
no window at stake. It is the same class as the missing
`COPY src/l5_order_body.py` — a rename that the deployment surface did not
follow — and it is exactly what a disarmed deployment is for. Had this
been discovered during an armed attempt, it would have cost a window and
told us nothing.

## What is NOT claimed here

Until the boot log of a SUCCESS deployment is read, the seven proof items
are **unproven**. The local disarmed run proves the code; it does not
prove the image. Those items are recorded below only from container
evidence.

---

## The DISARMED deployment: SUCCESS

| field | value |
|---|---|
| deployment | `991cd357-8270-45fa-a8c0-8af0c0d18633` |
| status | **SUCCESS** |
| SHA | `d61235fb0c86f79b47c450125125ee9dc2c57fc9` |
| booted | 2026-10-07T00:10:02Z |
| image | `deploy/l5_exec1xr2w/Dockerfile` |
| entrypoint | `python3 -m scripts.l5_exec1xr2w_run` |
| restart policy | NEVER |

The deployed SHA is `d61235fb0`, not `7a206e0b2`. The two differ only by
this document; no code, spec or economic value changed between them, and
the container's own `assert_frozen()` recomputed the same fingerprint.

## The seven proof items, from container evidence

**1. Exact SHA and fingerprint.** `RAILWAY_GIT_COMMIT_SHA`
`d61235fb0c86f79b47c450125125ee9dc2c57fc9`, `RAILWAY_DEPLOYMENT_ID`
`991cd357-8270-45fa-a8c0-8af0c0d18633`, and

```
"xr2w":            "ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562"
"xr2w_authorized": "ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562"
```

computed and literal agree **inside the container**. The four parents are
printed under `SUPERSEDED_parents` with `superseded_must_not_be_rearmed`
naming EXEC-1-X, XR and XR2. **PROVEN.**

**2. The container boots with `src/l5_order_body.py`.**

```
"serializer": {"module": "src.l5_order_body", "importable": true,
               "a5_fixture_body_matches": true,
               "preview_envelope_matches": true,
               "quantity_type": "float",
               "price": {"value": "0.51", "currency": "USD"},
               "semantic_down_0_49_to_wire": {
                   "outcomeSide": "OUTCOME_SIDE_NO",
                   "action": "ORDER_ACTION_BUY",
                   "price_value": "0.51"}}
```

The A5 fixture body and the preview envelope match byte for byte, in the
image, and `quantity` is a float rather than a string. This is the gate
that the missing `COPY` would have failed. **PROVEN.**

**3. `armed = false`.**

```
"keys_present": {"PMUS_EXEC1_LIVE": false, "PMUS_EXEC1_SESSION_ID": false,
                 "PMUS_EXEC1_ARM": false, "PMUS_EXEC1_GATE_EVIDENCE": false}
"armed": false,  "claude_set_any": false
```

All four owner keys absent. **PROVEN.**

**4. The fingerprint self-check passes and the pin is inert.**
`"xr2w_frozen": true` — `assert_frozen()` passed in the container, which
also re-asserted every inherited invariant. The pin reads
`{"set": false, "matches_this_build": false, "required":
"ac6318d5…2562"}`, and the policy block states
`runner_verifies: ITS_OWN_FINAL_XR2W_FINGERPRINT`,
`xr2_fingerprint_is_armable: false`. **PROVEN.**

**5. Credentials — present only. BOOK NOT EXERCISED.**
`"credentials_present_only": true`: presence by boolean, no value read or
printed. `"legacy_executors_importable": []` — all three order-capable
legacy modules are absent from this image, as designed, and
`"other_order_capable_processes": []`.

**The authenticated book was NOT opened in this deployment, so its
availability is NOT proven here.** The run refused at `NO_FROZEN_SLUG`,
which precedes the book wait, so no AuthBook connection was attempted.
The most recent evidence that AuthBook works from this host is Attempt
5's own run (two-sided, age 0.062 s against the 2.0 s bound) — but that
was the previous image, and it is not restated as evidence for this one.
**PARTIAL: credentials proven, book untested.**

**6. No order endpoint reached.**

```
"stopped_at": "NO_FROZEN_SLUG",  "reached_final_gate": false,
"orders_submitted": 0,
"market": {"ok": false, "slug": null,
           "why": "PMUS_EXEC1_GATE_EVIDENCE is empty. ... the slug must be
                   frozen before the window is observed."}
```

Zero POSTs to `/v1/order/preview` and zero to `/v1/orders`. The refusal
precedes transport construction, so no order route was opened at all.
The deadman correctly reports `skipped: "no submission was attempted, so
there is nothing that could be resting"`. **PROVEN.**

**7. The durable submission latch is unspent.**

```
"durable_latch": {"available": false, "spent": null, "path": null,
                  "why": "PMUS_EXEC1_SESSION_ID is not set; the latch has
                          no session to scope itself to"}
```

The latch is session-scoped and no session was set, so no latch file was
created and none could be consumed. No prior attempt's latch record was
read, written or removed. **PROVEN: nothing was spent.**

## Verdict

`NO ORDER / NO_FROZEN_SLUG`, `orders_submitted: 0`,
`matched_pair_economics: "N/A"` (not zero — not applicable).

Six of the seven items are proven from container evidence. Item 5 is
partial: credentials are proven present, the book is untested because the
disarmed refusal comes first. A disarmed run cannot prove the book
without being given a frozen slug, which is an arming input.

## Awaiting owner authorization

```
LIVE-L5-EXEC-1-XR2W
ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562
deployed SHA d61235fb0c86f79b47c450125125ee9dc2c57fc9
```

Nothing is armed. No owner value is set. XR3 is undeployed. S3 untouched.
