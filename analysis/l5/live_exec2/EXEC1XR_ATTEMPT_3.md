# LIVE-L5-EXEC-1-XR ATTEMPT 3 — SKIP ON TIMING. NO SUBMISSION.

Classification: **SKIP (frozen placement cutoff). NOT a trading result, not a
no-fill, not an ambiguous POST.** Zero observations of execution.

No order, no preview, no signing, no venue mutation. **Durable latch UNSPENT**
— the one lifetime submission attempt for this session was not used. Capital
at risk **$0.0000**. matched-pair economics **N/A**.

**The cause was my scheduling, not the venue, the code or the envelope.**

## What ran

| | |
|---|---|
| Fingerprint | `f3e54d82…c5e9`, verified in-container, `xr_frozen: true` |
| SHA | `6b569d8294f5f6db13221a4f32624dfc8e8312c4` |
| Deployment | `20ebc04a-da4a-47c4-b876-448c3e592ad4`, SUCCESS |
| Boot | 2026-10-06T16:43:48Z |
| Sibling fingerprints | `d2f32276…6c79`, `b1dbe7f1…2e06` — unchanged |
| Arming | all four keys present, `armed: true` |
| Fingerprint pin | set, **matches this build** |
| Credentials | present (presence only) |
| Durable latch | `available: true`, **`spent: false`**, path `…/xr-a3-2026-10-06-03997f04.efc07ec0b654b29f.submitted` |
| Legacy executors importable | none |
| Other order-capable processes | none |

Frozen inputs, fixed before the window opened and never changed:

* session id `xr-a3-2026-10-06-03997f04` (new, never used)
* slug `cpc-btc-updown-15m-2026-10-06-1630z`
* hash-selected side **UP** (`OUTCOME_SIDE_YES`), digest `5e658435…`

## The refusal

```
stopped_at: "TIMING"
refusals: ["PAST_PLACEMENT_CUTOFF: TTE 71.934s <= 120.0s"]
withdraw: {"withdraw": true,
           "reason": "AT_OR_PAST_WITHDRAWAL_BOUNDARY: TTE 71.934s <= 120.0s"}
VERDICT: "NO ORDER / TIMING"
```

The window had 71.9 s left. The frozen rule allows placement only while
TTE > 120 s, so the runner declined before reading the book, before any
network call, and before the latch. The deadman recorded `"no submission was
attempted, so there is nothing that could be resting"`. This is the gate
working.

## Why the margin was gone — my error, stated plainly

I set the variables at 16:11Z against the 16:30Z window and slept until
~16:31. The sleep returned at 16:42:40 (a worker restart delayed the
wake-up), leaving 139 s. The redeploy then took **65 s** to boot
(16:42:43 → 16:43:48), landing at 71.9 s.

I underestimated two things: that my own wake-up could slip by eleven
minutes, and that a redeploy of an already-built image still costs about a
minute of container start. Both are my planning error. A correct schedule
arms before the window and boots in the first third of it, with minutes of
slack rather than seconds.

I did **not** re-select the slug to rescue the attempt. The slug feeds the
side hash, so re-selecting would have re-rolled the side — exactly the
freedom the design exists to remove — and the authorization forbids
selecting another market under it.

### A real gap this near-miss exposed, reported not patched

The placement gate is evaluated **once**, at the start of the run. A run that
starts at TTE 125 s and then waits for a fresh book could POST below the
120 s cutoff. It did not happen here (the run never passed the gate), and I
did not change the code — the fingerprint is authorized and the
authorization says not to modify it. But launching deliberately into the
boundary zone would have been exploiting that gap, which is the other reason
not to have forced this run. A fix for a later fingerprint: re-check
`placement_allowed` immediately before the POST, inside
`preflight_submission`.

## State left behind

The five owner values are still set on `l5-live-probe-oneshot`, as
authorized for this Attempt-3 session. They are now **inert**:
`PMUS_EXEC1_GATE_EVIDENCE` names the expired 1630z slug, and the runner
selects that slug, so any further boot can only refuse on `TIMING` again.
The durable latch for `xr-a3-2026-10-06-03997f04` remains **unspent**.

I did not unset them and did not launch Attempt 4. Two things need your
decision:

1. whether to clear the five values, or leave them for a re-run of this same
   session id against a *new* slug — which would re-roll the side, since the
   side is `sha256(slug | session_id)`; and
2. whether to take the once-only placement check as a defect worth a new
   fingerprint before any further live attempt.

S3 untouched.
