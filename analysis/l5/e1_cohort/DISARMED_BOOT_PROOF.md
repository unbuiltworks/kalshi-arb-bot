# E1 DISARMED boot proof — Railway

Candidate fingerprint `481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486`
Commit `a465bdf59f2aebc73881c63ff993f3b9089845ff`
Service `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`)
Project `arb-ev-wallet-shadow` (`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`)
Environment `production` (`f3642d27-26dc-4e21-9170-bc76290c076f`)
Deployment `730c26b4-d0fa-4a83-a2ec-5bb9629038b6` — **SUCCESS**, booted 2026-10-07T14:49:12Z

STILL DISARMED. No `PMUS_E1_*` value is set on this service.

## The failure this replaced

Deployment `d05d30b4-2b14-430c-9581-c388fa975220` on commit `8e2d72aa0`
**FAILED at the build-time assertion**, which is what that assertion exists for:

    /app/analysis/l5/e1_cohort/selector.py line 29
        from src import l5_market_binding as MB
    ImportError: cannot import name 'l5_market_binding' from 'src'

`src/l5_market_binding.py` entered the import closure when `authorization.py`
began importing `selector.py`, after the Dockerfile's COPY list was computed by
hand. 153 tests were green and the image could not import its own entrypoint.
Fixed by COPYing it and by `test_dockerfile_closure.py`, which recomputes the
closure and fails the suite on the next such drift.

## What the boot proves

| Claim | Evidence |
|---|---|
| the image can import its entrypoint | the build-time `RUN` passed, which is the whole point of step 20/20 |
| the frozen spec loads and matches its literal | `assert_frozen()` ran inside the build and at boot |
| 139 invariants asserted at boot | `"invariants_checked": 139` |
| not armed | `"armed": false`, `"AUTHORIZATION": "NOT AUTHORIZED. NOT ARMED. NO OWNER VALUE SET."`, `"LABEL": "DISARMED. NO ORDER MAY BE PLACED."` |
| the armed path is no longer a dead end | `"armed_main_is_a_bare_raise": false`, `"armed_main_reaches_run_cohort": true` |
| XR2W untouched | `"xr2w_is_byte_identical": true`; four source hashes `dda1e2c50be81209`, `800078a852f14d84`, `add23515a5a8a201`, `50b9ff1376abea03` |
| five superseded fingerprints carried as must-not-arm | listed in the boot's `must_not_be_rearmed` |

## The strongest item, and why it is not a printed claim

The disarmed boot is supposed to reach no endpoint. A line saying so is just the
program asserting its own good behaviour. Railway's own network telemetry is
outside the process:

    dns:         []
    networkFlow: []

for the entire deployment — zero name lookups, zero connections.

POSITIVE CONTROL, because an empty stream could equally mean the stream is off:
`pmus-btc15m-tape` (`1f0c6aaf-657c-4c82-bb0d-592250c089b4`), same project, same
environment, same region, records `gateway.polymarket.us` A/AAAA lookups
(`104.18.38.40`, `172.64.149.216`). The stream works here; E1's emptiness is a
real negative.

Limit of that control: it is a different service in the same environment, not
the same service, so this is strong evidence rather than proof of instrumentation
parity.

## Known limitation of the Railway log as evidence

The boot's preflight JSON is large enough to hit Railway's replica limit:

    Railway rate limit of 500 logs/sec reached for replica. Messages dropped: 198

The `{"REFUSED": "DISARMED"}` block printed after the preflight is among the
dropped lines — `"armed": false` and the DISARMED label above survived, so the
disarmed state is established, but the refusal line itself was not retrieved.

This matters beyond this boot: under the owner's standing instruction the raw
volume tape is the system of record and Railway logs are secondary and may be
throttled. A live armed run prints more than this boot, so submission records
must be read from the tape, never from Railway.
