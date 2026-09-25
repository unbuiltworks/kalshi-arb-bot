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
