# Fast HALT via the signed marker — setup, verification, rehearsal, and the gap

**Executor disarmed. Watchdog deployment `ee4fe9aa` / `17f06565` intact. No
new services, no live orders, no limit changes.**

## 1. Hosting: a GitHub secret Gist

You already have the GitHub account this repository lives in. A **secret
Gist** gives a raw URL, is free, is yours alone to edit, updates on save, and
needs no CLI and no Railway service.

Secrecy of the file does not matter: **integrity comes from the HMAC, not the
transport**, which is exactly why the watchdog can stay credential-free with
respect to the marker. A secret Gist is simply tidier than a public one.

**Caveat to validate in rehearsal, not assume:** `gist.githubusercontent.com`
sits behind a CDN, so a freshly saved marker may not be served immediately.
That could add tens of seconds to the figures below. The rehearsal in §5 is
what measures it; I have not measured it and will not quote a number I
haven't.

## 2. Setup — three variables, one command, no Railway CLI

**The secret never leaves your machine except into a sealed Railway field.**

1. Generate it locally (or use any password manager's 32-byte random):
   ```
   openssl rand -hex 32
   ```
   Keep it in your password manager.

2. In the **Railway dashboard**, on `l5-halt-watchdog` only:

   | variable | value |
   |---|---|
   | `L5_EMERGENCY_HALT_SECRET` | that value, **marked SEALED** so Railway never reads it back — to me or anyone |
   | `L5_EMERGENCY_HALT_URL` | the Gist's **raw** URL |
   | `L5_EMERGENCY_HALT_SERVICE` | `l5-pmus-live` |

   Create the Gist first with any placeholder (e.g. `{}`) so the URL exists.
   A placeholder latches nothing: it parses as a non-marker and is refused.

3. **To halt**, sign one marker locally and paste it into the Gist. This is a
   local command, not Railway CLI:
   ```
   python3 - <<'EOF'
   import datetime as dt, json, secrets, sys
   sys.path.insert(0, ".")
   from src import l5_emergency_halt_marker as E
   SECRET = input("secret: ").strip()
   d = {"command": "HALT", "service": "l5-pmus-live",
        "issued_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "nonce": secrets.token_hex(8), "reason": "owner emergency stop"}
   d["hmac"] = E.sign(d, SECRET)
   print(json.dumps(d, indent=2))
   EOF
   ```

**A marker is valid for 24 hours** (`MAX_MARKER_AGE_S = 86400`, a module
constant, not configurable by env). So a pre-signed marker kept ready in your
password manager goes stale daily. Two honest options:

- **Sign at the moment you halt** — one command, and you are already acting.
  This is what I recommend; nothing to maintain.
- Or ask me to make the bound env-configurable so one marker stays valid as
  long as you choose. **That is safe**, because replay is prevented by the
  **nonce** and not by the age bound: a nonce already acted on is refused
  `MARKER_ALREADY_ACTED`, so a long-lived marker can still only latch once.
  It needs one more small watchdog promotion.

## 3. Per-sweep polling — VERIFIED

Driving the deployed code locally with a counting source:

```
5 sweeps  ->  5 fetches, 0 asserts     (one HTTP GET per sweep, in-process)
publish between sweeps -> next sweep: acted=True,
                          reason=HALT_LATCHED_FROM_OWNER_MARKER
```

**Each marker change is picked up on the next sweep with no redeploy and no
restart.** That is the property the variable trigger lacks.

One precision: my stub returned `MARKER_MALFORMED` rather than
`MARKER_UNREACHABLE`, so I verified that an unfetchable marker **latches
nothing**, but not that specific reason string.

| | latency | redeploy? |
|---|---|---|
| marker published → latch asserted | ≤ 20s (`poll_s 20.0`) **+ CDN, unmeasured** | **no** |
| latch → executor acts | **8.45s measured** | no |
| **marker total** | **≈29s + CDN** | **no** |
| variable trigger, for comparison | ≈110s | yes |

## 4. The four-gate reset — documented and validated

**118 tests pass** across `test_l5_halt_reset.py` and
`test_l5_halt_reset_independent.py`.

**First, read the latch without touching it.** On `l5-halt-watchdog`, set
`L5_HALT_RESET_AT_BOOT=YES` and `L5_HALT_RESET_INSPECT=YES`. The next boot
prints `L5_HALT_RESET_INSPECT {latch, set_this_as, preconditions, labels}` and
**writes nothing**. `set_this_as` is the digest gate 2 will demand.

**Then the reset**, on the same service, all five required:

| variable | value |
|---|---|
| `L5_HALT_RESET_CONFIRM` | exactly **`RESET THE LATCHED HALT`** |
| `L5_HALT_RESET_WHO` | your name — a human, no default, no truthy shortcut |
| `L5_HALT_RESET_ID` | a fresh unique id; recorded in an append-only audit and refused if reused |
| `L5_HALT_RESET_EXPECT` | the `set_this_as` sha256 from the inspect |
| `L5_HALT_RESET_ATTEST` | **your attestation**, in words, that the watchdog is healthy and no alarm is unresolved, **naming the evidence** |

`L5_HALT_RESET_ATTEST` is gate 4(d) and is the one gate no machine can
satisfy: it is **recorded verbatim and labelled operator-attested**, not
dressed up as a check. Gates 4(a)–(c) *are* machine-verified — the venue
reports zero open orders, the venue reports no non-zero position, and the
ownership registry is readable, **fresh** and empty (readable-but-stale is
refused, because a report that stopped arriving is the wedged signal).

The audit entry is appended and **fsynced before** the latch is cleared, so a
crash between the two leaves the halt latched and a record that a reset was
attempted — the safe asymmetry.

## 5. Rehearsal — designed, NOT executed, awaiting your approval

I have asserted nothing. Proposed sequence, executor disarmed throughout:

1. **Pre-stage the reset.** Set `L5_HALT_RESET_AT_BOOT` + `INSPECT` and read
   the inspect output. Confirm `preconditions` already pass with zero orders
   and no position. *You hold the way out before using the way in.*
2. **Measure the publish path.** Put a deliberately invalid marker in the
   Gist (e.g. `{"command":"RESUME"}` unsigned). Watch for
   `L5_EMERGENCY_HALT_MARKER` with `MARKER_COMMAND_NOT_HALT` or
   `MARKER_SIGNATURE_INVALID`. **This measures the Gist-to-watchdog delay,
   including CDN, without latching anything** — the single most useful step,
   and it is entirely reversible.
3. **Only on your explicit approval:** publish a real signed marker. Expect
   `halted` non-null within a sweep, the executor's `halt_latch` flipping
   `CLEAR → HALT` with its `halt_latch_telemetry.sha256`, and — because there
   are zero open orders — `cancelled: []`, which is the safe way to rehearse
   cancellation.
4. **Reset** via §4, and confirm `halt_latch` returns to `CLEAR`.

Step 2 gives you the real latency number with nothing irreversible. I would
do 1 and 2 on your word and stop before 3.

## 6. Stopping the executor — the cancellation sequence, and a real gap

**The watchdog CAN cancel.** Its image carries `cryptography` and
`src/pmus_auth.py` for "the ONE cancel route… pmus_auth holds no order body",
so it can cancel and cannot place. Production confirms `cancel_enabled: true`.
*(A comment in `maybe_halt_deadman` still says the watchdog cannot cancel —
that comment is out of date.)*

**Sequence when an order is resting:**

| actor | trigger | action |
|---|---|---|
| **executor** | latch flips to HALT, **while armed** | `maybe_halt_deadman` → `deadman()` cancels **by exact venue order id** and proves it by re-reading `GET /v1/order/{id}`; an unconfirmed cancel marks the store **UNCERTAIN** rather than reporting success |
| **watchdog** | an order's age **> `max_order_age_s` = 300s**, or age **unknown** | asserts HALT naming the orders, then cancels them by `venue_order_id`; results split into `cancelled` / `still_open` / `unverified` |

**THE GAP, and it corrects what I told you last turn.** The executor only
cancels **while armed**, and the watchdog only acts on orders **older than
300 seconds**. So if you stop the executor service while a fresh order is
resting, **nothing manages it for up to 300 seconds** — and it can fill in
that window, creating or worsening a residual with no process watching.

**So the order of operations matters and I had it backwards.** Last turn I
said stop the executor first, then latch. That is wrong: stopping it first
removes the fast canceller.

**Correct order: latch HALT first**, which makes the still-running armed
executor cancel immediately by exact venue id and verify it. **Only once
cancellation is confirmed** should you stop the service. Stopping the executor
is the right move when it is *misbehaving* rather than merely trading, and
then the ≤300s window is the price — the watchdog still closes it, just not
quickly.

With the system as it stands — disarmed, `live_orders 0`, venue
`open_orders 0` — this gap is currently unreachable. It matters the moment you
rearm.

## The single simplest owner action

**Create a secret Gist and set three variables on `l5-halt-watchdog`:**
`L5_EMERGENCY_HALT_SECRET` (sealed), `L5_EMERGENCY_HALT_URL` (the raw Gist
URL), `L5_EMERGENCY_HALT_SERVICE` = `l5-pmus-live`.

**Expected latency: ≈29s measured (≤20s sweep + 8.45s propagation), plus an
unmeasured CDN delay** that step 2 of the rehearsal will pin down.

## Unresolved safety issues

1. **The ≤300s unmanaged window** above, if the executor is stopped before a
   latch while armed with a fresh resting order. Mitigation is procedural
   (latch first); a code fix would mean lowering `max_order_age_s`, which
   trades against cancelling legitimately-resting maker orders.
2. **CDN delay on the Gist raw URL is unmeasured.** The ≈29s figure is a
   floor, not a measurement of the whole path.
3. **24-hour marker validity** means sign-at-halt-time, or one small
   promotion to make the bound configurable — safe, because the nonce is the
   real replay guard.
4. **The stale comment** in `maybe_halt_deadman` asserting the watchdog
   cannot cancel. Harmless to behaviour, actively misleading to a reader
   reasoning about exactly this gap. Worth a one-line fix.
