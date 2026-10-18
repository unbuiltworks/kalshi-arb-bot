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

### 2026-10-18 — CANCEL_MECHANISM, disarmed precondition verification

Authorized: "I approve preparation of one explicitly labeled PMUS
cancellation-mechanism test... 1. Include l5_halt_gate.py in the Docker
image and verify watchdog connectivity... 3. Deploy and verify the hard
max_live_orders=1 change... No live submission is authorized yet."

- artifact: `5a500ffa`, carrying the per-order cap, the startup refusal, the
  reconciled one-order limit, `src/l5_halt_gate.py` in the image, and the
  `CANCEL_MECHANISM` mode
- `L5_CANCEL_MECHANISM=YES`, `L5_OBSERVE_ONLY=NO`; no `PMUS_Q1_*` variable
  exists on the service, so `owner_authorization` fails by construction
- the build contains **no mechanism submit path at all**: the mode refuses
  `NOT_IMPLEMENTED_WITHOUT_SEPARATE_AUTHORIZATION` even with every other
  precondition met, so this run cannot place an order whatever is set
- purpose: measure in the container what cannot be measured locally — that
  the halt gate imports and the watchdog answers over private networking,
  that `max_live_orders` resolves to 1, that the cap is declared at 0.01,
  and whether the authorization ledger is durable on this service
- expected to exit **65** (refused). A refused money-capable mode is not a
  success, and the exit code says so
