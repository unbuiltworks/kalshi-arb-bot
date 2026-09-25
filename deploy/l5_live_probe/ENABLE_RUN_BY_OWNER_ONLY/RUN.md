# The promote marker

`railway.json` points the service's `watchPatterns` at this directory and
nothing else, so a commit anywhere else in the repository lands as SKIPPED
and cannot start a run. That is what protects a window in flight while
other work is pushed. Touching a file here is the deliberate act that
deploys and starts the one-shot, and it is an owner-authorized act.

This file records each run. The authorization itself lives in the owner's
message and, for a money-moving run, in the consume-once ledger; nothing
written here grants anything.

## Runs

### 2026-09-25 — OBSERVE-ONLY, corrective

Authorized: "fix the demonstrated observe-only packaging defect and verify
it... If green, make one corrective observe-only deployment."

- artifact: the commit that adds the four import-closure COPY lines and the
  packaging regression test
- `L5_OBSERVE_ONLY=YES` remains set, so the runner takes `observe()`: it
  never calls `auth.claim()`, never constructs `PmusTransport`, writes no
  ledger entry, and holds `NullTransport`, which has no HTTP client
- no `PMUS_Q1_*` variable is set on the service
- structurally unable to POST; `armed` false and `submitted_live` 0 by
  construction, not by flag
- purpose: prove in the container what could not be proven locally — that
  the L5 action feed now polls, that L5_NATIVE decisions are readable, and
  that the would-submit path runs end to end, against the continuous
  recorder over the same interval

### 2026-09-25 01:15z — OBSERVE-ONLY, final verification

Authorized: "fix the demonstrated observe-only reporting defect and make
one final observe-only verification deployment... Run long enough to obtain
meaningful live evidence."

- artifact: the commit fixing `would_submit` and the non-zero report
  regression tests
- `L5_OBSERVE_ONLY=YES` remains set; no `PMUS_Q1_*` variable exists on the
  service; the executor holds `NullTransport`
- timed so the container starts with under `OBSERVE_MIN_S` left in the
  0100z window, which rolls the run forward to **0115z** and gives a FULL
  15-minute window directly comparable to the recorder's own 0115z line

### 2026-09-25 0215z — PROBE 03, REAL MONEY

Owner-authorized: "AUTHORIZED: execute Probe 03 with real money", then
"AUTHORIZED: wire the existing account-fingerprint gate, then proceed
directly to Probe 03 if and only if the targeted tests pass."

- market `cpc-btc-updown-15m-2026-09-25-0215z`, bound in
  `L5_LIVE_PROBE_MARKET` before the authorization is claimed
- session `l5probe-20260925-btc15m-03`, consume-once
- account pinned: `PMUS_PREFLIGHT_ACCOUNT=d51b44a3fa83`. A credential
  resolving anywhere else fails preflight, so `arm()` raises and nothing
  that can POST is constructed
- `L5_OBSERVE_ONLY` disabled, so `main()` takes `run()`
- envelope unchanged: $5/market, $5 total potential exposure including
  outstanding orders, one live order, residual <= 10, one market, $5 daily
  realized-loss halt, modelled maximum cash bound $5.1738 including fee
- timed so the container is up before the window opens and the preflight
  runs at T+45s, inside the window it will trade

### 2026-09-25 ~1130z — OBSERVE-ONLY, to mount the volume and read the Probe 03 journal

Owner-authorized: "use the least-invasive read-only method available to
mount/read the existing persistent volume. Do not run another trading probe
merely to reproduce the error."

Railway cannot read a volume without a running container, and the recorder
mounts a DIFFERENT volume (18bd5702-...), so the only way to reach
156032e0-... is to start this service's own container. Observe-only does
that and cannot send an order: `L5_OBSERVE_ONLY=YES`, no `PMUS_Q1_*`
variable exists, `auth.claim()` is never called, no `PmusTransport` is
constructed, and the executor holds `NullTransport`.

The target is the Probe 03 `action` record carrying
`ORDER.reason == "SUBMIT_OUTCOME_UNKNOWN"` and its `detail`, in
`/state/pmus-ws/l5_session_journal.jsonl`. `Journal` opens that file in
append mode and never truncates, so the Probe 03 lines survive this run.

### 2026-09-25 1215z — OBSERVE-ONLY, the pre-money gate after changing order representation

Owner-approved: "deploy the quantization fix + b458618 observability
instrumentation and run one full observe-only production-path window. This
is not new research; it's the existing pre-money integration gate after
changing executable order representation."

- artifact: the commit that floors the order quantity onto the venue's own
  grid and records which call is in flight when a submit dies
- `L5_OBSERVE_ONLY=YES`, no `PMUS_Q1_*` on the service, `NullTransport` in
  the executor, `auth.claim()` never called: it cannot send an order
- `PMUS_PREFLIGHT_ACCOUNT=d51b44a3fa83` stays
- timed so the container starts with under `OBSERVE_MIN_S` left in 1200z,
  which rolls it forward to a FULL 1215z window
- what this run has to show: L5 actions, non-zero WOULD_SUBMIT, and no
  execution-path error. Observe-only is DISARMED, so it quantizes nothing --
  the grid is only known after a preflight, which observe-only never runs.
  What it proves is that the changed code path still reaches a would-submit.
