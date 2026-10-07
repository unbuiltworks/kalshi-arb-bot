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
