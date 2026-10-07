# E1 incident 2026-10-07T16:48:58Z — `VENUE_ERROR_STORM` against a venue that was never contacted

**Classification:** execution-path defect before trading. Not an E1 trading
result. **Realized P&L $0.00. Zero orders, zero fills, zero latch slots.**

## What the tape said, and why it was wrong

    WINDOW_OPENED    unix 1791391738.7699485   window_index 0
    COHORT_STOPPED   unix 1791391738.7907534   VENUE_ERROR_STORM

20.8 ms apart. `gateway.polymarket.us` was never resolved. The order
endpoint is in fact `api.polymarket.us` (`src.pmus_auth.API`), and that was
never contacted either: the five "venue errors" all occurred **before any
socket was opened**.

## Root cause — proven, not inferred

`cryptography` was **not installed in the image**. Hard evidence from the
deployed container (read-only probe, deployment `5ed29b36`, identical image,
commit `14e7d7477`):

    CRYPTOPROBE_PYTHON 3.11.17
    CRYPTOPROBE_CRYPTOGRAPHY ABSENT ModuleNotFoundError: No module named 'cryptography'
    CRYPTOPROBE_SITEPACKAGES /usr/local/lib/python3.11/site-packages
        installed=['packaging', 'pip', 'setuptools', 'wheel']

The Dockerfile ran no `pip install`. `python:3.11-slim` ships none of it.

The exact exception, thrown five times:

| | |
|---|---|
| type | `builtins.ModuleNotFoundError` |
| message | `No module named 'cryptography'` |
| origin | `src/pmus_auth.py:70` in `signed_headers()` |
| source line | `from cryptography.hazmat.primitives.asymmetric import ed25519` |
| operation | Ed25519 signing of `POST /v1/order/preview` |
| network | none — raised before `urlopen` |

### Why the build did not catch it

The import is **lazy**: it lives inside `signed_headers`, not at module
scope. Importing the entrypoint loads **zero** third-party modules — still
true today, asserted by
`test_the_lazy_import_is_why_the_build_assertion_missed_it`. So the
build-time "the entrypoint imports" assertion passed while the image could
not sign anything.

### Why it was classified as a venue error

`orders._submit_once` wrapped the preview call in a broad
`except Exception` and raised `PreviewRejected`; `run_cohort` caught
`PreviewRejected` and called `tracker.record_venue_error(...)`. The local
fault was laundered into the venue taxonomy.

### Why it retried five times essentially instantly

Nothing was waiting on anything. Each iteration was: snapshot (a dict),
preflight (arithmetic), `signed_headers` (raise). No backoff exists for
`PreviewRejected`, correctly — it is not a retry, it is the next attempt of
a 20-per-window loop. `CohortTracker.VENUE_ERROR_STORM = 5`, so the fifth
consecutive one tripped the safety stop. 20.8 ms is simply how long five
passes through pure Python take.

## The credential hypothesis — REFUTED

I had flagged, as a lead, that the service carries `PMUS_API_SECRET_KEY`
while my test harness set `PMUS_API_SECRET`. The service variables and the
code agree:

* `src.pmus_auth` reads `PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY`;
* both are present on `l5-live-probe-oneshot`, and both are well-formed
  (`PMUS_API_SECRET_KEY` base64-decodes to 64 bytes; the loader takes the
  first 32 as the Ed25519 seed);
* `PMUS_API_SECRET` exists nowhere on the service and is read by no code.

The wrong name existed **only in the test harness**, where it did not matter
because `FakeTransport` never signs. That is not the cause of the incident —
but it is why a 174-test green suite could not see the incident coming, and
it is fixed: `conftest.py` now satisfies the production credential contract
with a throwaway generated key, and `arm()` uses the real names.

## Reproduction

`run_cohort`, production `BoundedOrderTransport`, real variable names,
`cryptography` made unimportable, no network:

    wall clock ms    : 23.0          (tape: 20.8)
    venue_errors     : 5
    truncation_cause : VENUE_ERROR_STORM
    placements       : 0

After the fix, same conditions:

    wall clock ms    : 2.0
    venue_errors     : 0
    local faults     : 1
    truncation_cause : LOCAL_EXECUTION_FAULT
    iterations       : 0

## The fix

1. **The demonstrated defect.** `deploy/l5_e1_cohort/Dockerfile` installs
   `cryptography==49.0.0` — the version the suite runs against — and a new
   build step **signs with a throwaway generated key**, so the build fails
   if the signing stack is ever absent again. A lazy import cannot be caught
   by an import assertion, so the build proves the capability instead.

2. **The taxonomy.** A deterministic local fault is now its own thing:
   `local_faults.LocalExecutionFault`, deliberately not a subclass of any
   venue exception, counted by
   `CohortTracker.record_local_execution_fault`, named
   `LOCAL_EXECUTION_FAULT` in the truncation cause, and **failing closed on
   the first occurrence**. `verify_signing_contract()` runs once at
   `run_cohort` entry — before a window is opened, before a snapshot is even
   requested — and the fault is persisted to the tape with every string
   redacted at every depth.

   Genuine retryable venue/network errors keep the existing bounded storm
   protection, unchanged and asserted.

3. **Local vs venue is decided by reproduction, not by exception type.** The
   same `ValueError` is a local config fault from the key loader and a
   retryable post-network failure from `json.loads`. So
   `classify_preview_exception` **re-runs the signing contract**: if signing
   is still broken the fault is demonstrably local; if it works, the
   exception is left exactly where it was. Both directions are asserted.

## Unchanged

0.13 shares/order, $0.60 first-leg ceiling, $0.96 pair ceiling, 20
attempts/window, 12 windows / 240 maximum, $1.56/window, $5.00 cohort risk
ceiling, 13 complement tranches, maker-only, no chase/requote/taker rescue.
No `SPEC` key moved, so the frozen fingerprint is still
`481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486` and the
owner's authorized fingerprint does not change. Websocket readiness
behaviour and the immediate per-snapshot freshness verification are
untouched.

## Cohort state

The failed cohort's tape is preserved unchanged. `windows_used` remains
**1 of 12**. Whether a future arm reuses session
`E1-COHORT-20261007T1530Z` (resuming at 11 remaining windows) or starts a
fresh session id is an owner decision, not taken here.
