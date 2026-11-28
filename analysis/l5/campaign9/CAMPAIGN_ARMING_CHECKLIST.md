# Eight-window campaign: the owner's signing action, and the arming checks

## The signing action — one command, already verified

Run this on a machine you control, then paste the FILE'S CONTENTS into the
Railway variable `L5_CAMPAIGN_APPROVAL` on service `l5-pmus-live`:

```
ASK_WINDOWS=8 ASK_EXPOSURE_USD=20 ASK_DAILY_LOSS_USD=5 \
ASK_DURATION_S=14400 ASK_OPEN=24 ASK_COMPLETE=32 \
ASK_REPLACE=16 ASK_TOTAL=72 \
python3 tools/make_campaign_approval.py /secure/path/approval.json
```

**Verified, not assumed.** I ran exactly this to a temporary path and the
real `ApprovalGate` returned `gate_says_approved: true`, `gate_why: null`,
and **`clamped: false` on every field** — `max_windows 8`,
`max_total_exposure_usd 20`, `max_daily_loss_usd 5`,
`max_total_submissions 72`. The scratch document was shredded; its nonce was
never printed and never entered the transcript.

**Mint it when you are ready to run, not before.** `MAX_CAMPAIGN_S` is a
code constant of **4 hours**. Eight 15-minute windows is two hours of clock,
so the document must be installed and the windows run inside that four-hour
validity. The previous document expired unused for exactly this reason.

The tool prints the `document_sha256` on stdout without the nonce. Tell me
that digest and I will confirm the installed document is the one you signed.

## What the document does and does not control

| limit | enforced by | value |
|---|---|---|
| windows | the document | 8 |
| total exposure | the document + `SafetyLimits` | $20 |
| daily realized loss | the document + gates | $5 |
| committed cash | deployed gate | **$12.00** verified |
| residual, contracts | deployed gate | **5** verified |
| residual, dollars | deployed gate | **$3.00** verified |
| L5 cumulative loss | deployed gate | **$20** verified |
| account-wide cumulative | deployed gate | **$18.6325** verified, stricter |
| cash recycling | deployed gate | `NO_RECYCLING` verified |
| maker-only | `L5_DEFENSIVE_COMPLETION` absent | **to confirm at arming** |
| one live order | `SafetyLimits.max_live_orders` | **to confirm at arming** |

**The $18.6325 question is resolved.** There are two cumulative ceilings and
both are present: `max_l5_cumulative_loss_usd` is exactly the authorized
**$20**, and `max_cumulative_loss_usd` is a separate ACCOUNT-WIDE backstop at
**$18.6325**. The stricter one is not a substitute for the authorized one —
it sits beside it and refuses first. Read from `L5_LIVE_GATES` at
2026-10-29T17:00:06Z, with `journal_errors: 0`.

**Two limits I have NOT verified and will not claim.** `max_total_exposure_usd`
and `max_live_orders` live on `SafetyLimits`, set through environment
variables whose values my Railway role cannot read (`valuesRedacted: true`,
names only). They are printed in the `L5_LIVE_ARMED` scope digest, so they
are verifiable at arming and nowhere earlier. **Arming halts if either is not
exactly $20 and 1.**

## Pre-order checklist, read from the heartbeat

Every line is a hard stop, not a preference:

1. deployed commit is **`c39183a3`** and the deployment status is SUCCESS
2. `preflight: PASSED`
3. `submission_gate.halt_latch: CLEAR` **and** `halt_why: null`
4. `halt_latch_telemetry.record_t` within 900 s of now
5. `submission_gate.shadow_lock: false`
6. reconciled; the book is not UNCERTAIN; `unresolved_accounting` empty
7. `armed: true`, and the campaign `nonce_sid` matches the digest you signed
8. the `L5_LIVE_ARMED` scope shows `max_total_exposure_usd: 20` and
   `max_live_orders: 1`
9. `L5_DEFENSIVE_COMPLETION` and `L5_TS_TWO_ORDER_PAIR` both absent
10. `journal_errors: 0`

Then, and only then, `L5_BOOK_COMPLETION=1`. After the first window:
`book_completion:*` counters must be non-empty and
`L5_BOOK_COMPLETION_TRIGGER` must carry `triggered_by: BOOK_NOT_L5_ACTION`.
**An empty counter set means the hook never ran, which is a defect to report,
not a quiet pass.**

## How Route B will be measured against C6-C8

Reported separately, never netted:

| metric | C6-C8 baseline |
|---|---|
| submissions | 32 in C8 over 6 windows |
| fills | 53/87 = 60.9 % |
| completed pairs | 23 across 15 reconstructable windows |
| matched surplus | **+$0.1933/window**, sd 0.2400, t=3.320 |
| residual settlement | +$0.0353/window, t=0.225 |
| settled realized net | **+$3.8861** over 17 windows |

Route B's claim is **more completed pairs per window at an unchanged pair
cost**. The honest prior is that it adds little: the reachable rate on the
only recorded sequence was 5 of 74, and the settled break-even of 0.9532
against an achieved 0.9530 says adverse selection consumes the half-spread
that makes the rest look profitable. A flat result is the expected outcome
and will be reported as one.
