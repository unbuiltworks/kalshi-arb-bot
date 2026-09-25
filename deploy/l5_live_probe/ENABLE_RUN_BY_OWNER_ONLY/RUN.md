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
