# Review of candidate artifact 5deaf006 — packaging and 3b-style delta review

**Outcome: the baseline is NOT advanced. Two blockers found. 3e is closed as
irrelevant.** Nothing deployed, armed, created or activated.

## 1. Packaging pins

| field | pinned value |
|---|---|
| Dockerfile | `deploy/l5_pmus_lock5_probe/Dockerfile` (**proposed, not built**) |
| COPY list | the **computed** AST import closure of the entrypoint — 38 files — plus 6 `REQUIRED-BY-DESIGN` files and the authorization directory. 46 `COPY` lines, curated by nothing. |
| runtime deps | `websockets>=12`, `cryptography>=42` — identical to the sibling probe images, nothing added |
| service | **reuse `l5-live-probe-oneshot`, id `84aa9999-6d7a-4980-9a87-96694d8178dd`**. No new service. |
| start command | `python3 -m scripts.l5_pmus_live_oneshot` |
| restart policy | **NEVER**, set on the service — a one-shot the platform restarts is not a one-shot |
| candidate SHA | `5deaf006` |
| REVIEWED baseline | still **`04cf5295`** — see §3 |

## 2. 3e — CLOSED, irrelevant to this artifact
`test_deploy_fallthrough_guard` fails on `l5_pmus_live: no railway.json`. With the
image and service now pinned, that is **provably not this artifact**:

* the artifact's image is `deploy/l5_pmus_lock5_probe/`, not `deploy/l5_pmus_live/`;
* the artifact's service is `l5-live-probe-oneshot`, not `l5-pmus-live`;
* `deploy/l5_pmus_live/Dockerfile` runs `CMD python3 -m src.l5_pmus_live_runner`, a
  **different entrypoint**, and that runner was **permanently disarmed by 3a** — it
  has no `arm()` caller, so it cannot spend under any configuration, fall-through or
  otherwise.

The failure remains real for the `l5_pmus_live` service and is left on the unrelated
baseline. **3e no longer blocks.**

## 3. The 3b-style review — BLOCKED, and the baseline stays at 04cf5295

### BLOCKER 1 — the wiring is not reachable from the entrypoint
The computed closure of `scripts/l5_pmus_live_oneshot.py` contains **neither
`pmus_lock5_translation`, nor `pmus_live_mandate`, nor `pmus_probe_observability`, nor
`pmus_completion_shadow`.** The one-shot does not import the translation.

So an image built from the entrypoint closure alone would ship **no LOCK5, no mandate
boundary and no taker completion** — the probe would be the old one-shot wearing a new
name. The 39 wiring tests all pass, and all of them construct `Lock5Session` directly;
none of them proves the entrypoint uses it, which is exactly the gap a closure
computation catches and a green suite does not.

This is why the review does not advance HEAD. Making those modules reachable is an
execution-class change to the arming/submission path and needs its own review pass.

### BLOCKER 2 — the retired ramp ladder is in the transitive closure
`src/pmus_micro_executor.py` defines:

```python
def per_market_first_leg_cap() -> D:
    from src import pmus_l5_ramp_ladder as ladder
    return D(str(ladder.active()["per_market_cap_usd"]))
```

That is **the mechanism on the DO-NOT-USE-FOR-LIVE-AUTHORIZATION list**
(RAMP_LADDER_001): on 2026-09-16 persisted historical owner rungs were auto-loaded and
silently replaced live limits, $0.05 → $7.50. Composing the micro executor for its body
builder drags a live-cap function backed by that ladder into the artifact.

Mitigations, stated honestly rather than as reassurance: the translation calls only
`MICRO._body(...)`, the ladder import is lazy inside a function the translation never
calls, and the mandate is now authoritative over caps. **None of that is sufficient on
its own** — the 2026-09-16 lesson is precisely that a path which exists gets taken. The
Dockerfile therefore **does not COPY `pmus_l5_ramp_ladder`**, so the lazy import would
raise rather than resolve a stale rung: fail closed, not fail quiet. A narrower fix —
taking the body builder without the executor — is an execution-class change and is not
made here.

### What the review DOES clear
`src/pmus_lock5_translation.py` itself reviews clean against the 3b criteria:
floor **bound by identity** to the frozen implementation, `LOCK_USD` a constant with no
threshold parameter, exactly one `self.transport.request` with `MANDATE.admit` ahead of
it structurally, `ACK` moving no inventory, and `apply_authoritative_fill` refusing any
source but `VENUE_FILL`/`RECONCILIATION`. The +37-line one-shot change is the retry loop
only, and it keeps the single-arm-owner invariant green.

## 4. Test evidence against the packaged set
**344 passed**, focused suites: wiring/parity (39), arm-retry (18), one-shot (66),
lineage + lane-A (41), signing (43), status handoff (14), probe observability (11),
mandate adversarial + deployment + ledger invariant (58), body-delegation and
q1-conservatism equivalence (14), capacity manifest (9), accounting identity (20),
ledger order invariance (20).
