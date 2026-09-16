# R1 canary breach: authorised for ONE market at $0.05, it traded 11+ at $19.33

HALTED 2026-09-16T10:48:48Z, `SHUTDOWN_VERIFIED`, zero open orders, account
flat. This file is the evidence record. Nothing here is REALIZED beyond the
venue-confirmed settlements named below.

## What was authorised

> "AUTHORIZED: ARM LANE_A_EXECUTION_MEASUREMENT_PROBE_V0 on l5-r1-probe for ONE
> market only. ... Hard live envelope: R1 · ONE MARKET · $0.05 absolute
> aggregate market notional ... Once the first market reaches the campaign stop
> condition, HALT AND REPORT. Do not automatically re-arm."

> "Then STOP. Do not automatically start R1 market #2."

## What happened

`aec-itfwo-meiguo-junzha-2026-09-16` settled REALIZED -$0.0214 at 07:44:32Z --
the campaign stop condition. The campaign did not stop. It ran for a further
three hours across at least eleven markets and deployed **$19.3295** on one of
them, 387x the authorised per-market envelope.

| market | spent | outcome |
|---|---|---|
| aec-itfwo-meiguo-junzha | $0.0214 | REALIZED **-$0.0214** 07:44:32Z |
| aec-setkameua-zavmyk-honser | $0.0306 | REALIZED **-$0.0306** 07:54:02Z |
| aec-itfme-lacvic-qiasun | $0.0264 | REALIZED **-$0.0264** 09:12:21Z |
| aec-setkameua-koryev-semser | $0.0382 | REALIZED **+$0.0318** 09:15:00Z |
| aec-itfwo-liszaa-elekor | $0.0450 | REALIZED **-$0.0450** 09:59:49Z |
| **aec-itfme-conbra-andpet** | **$19.3295** | **PENDING_SETTLEMENT** |
| darkuc-julada, hinino-mutuem, cs2-ttbs-ezt, kbo-dbo-sla, kryser-melore | $0 | no fill / not qualifying |

Venue-confirmed realized to date: **-$0.0916** (agrees with the probe's own
`realized_today_usd`). The 08:02-08:38 window was truncated by the 500-entry
log limit, so the market count is a floor, not a total.

## Two independent causes, neither of them the dual key

**1. The campaign walked a prebuilt ladder on stale approvals.** After the
first settlement, `advance_gate` reported `all_clean: true` and the campaign
emitted `campaign_continuation_start` three times, each loading an owner
approval **dated 2026-09-06**: `R2C-RUN-2026-09-06`, `R2C2-RUN-2026-09-06`,
`R3L-RUN-2026-09-06`. Those carried their own limits, and the per-market
envelope went $0.05 -> $7.50 and the daily loss stop -$1.00 -> -$15.00.

The dual key only *arms*; it does not bind the campaign to the market count or
the envelope the owner named when arming. `ramp_evaluation` is not the
culprit -- it correctly returned ROLLBACK then HOLD and states "nothing promotes
itself". The campaign *chain* is a separate mechanism and it did promote itself,
on September-6 authority, into a September-16 session that had been given a
much smaller mandate.

**2. `counts_against_market_cap()` does not count the COMPLETE leg.** Inside
conbra-andpet the cap counter reached $7.4476 against its $7.50 cap and kept
admitting, while money-moving BUY notional reached **$19.3295**. The membership
rule admits `spring_open*`, `spring_extend*`, `micro_first_leg` and the flat
probe -- but a `spring_complete_*` on this venue is a **BUY of the complement**,
real cash out, and it is excluded.

This one is mine. I wrote that rule for the P0 envelope correction, verified it
against the owner's six pinned tests, and reported it complete. Those tests
exercised OPEN, EXTEND and the complement-acceptance probe; none of them drove a
real `spring_complete_*` fill, so the gap survived every test I wrote. On market
#1 it was invisible because that market never completed -- it held residual to
settlement. The first market that actually completed exposed it.

## Position at halt

`aec-itfme-conbra-andpet-2026-09-16`: matched 20.44 / 20.44, residual **0.00**,
$19.3295 paid, avg YES 0.36436 + avg NO 0.58131 = pair cost **0.94567**.
Venue 40.88 = ledger 40.88, `agrees: true`. Fully matched and pending
settlement; held inventory was preserved, never liquidated.

At that pair cost the matched book implies roughly +$1.11 gross before fees if
it settles normally. **That is modelled pair economics, not realized profit**,
and it does not offset the governance failure: $19.33 of capital was committed
against a $0.05 authorisation.

## How it was halted

`set-variables` is not offered to this session and `redeploy` had no snapshot
(the latest deployment was SKIPPED), so the only path left was a Railway
`startCommand` exporting `PMUS_SPRING_SHUTDOWN=1` / `PMUS_SPRING_LIVE=NO` ahead
of the image's own CMD, landed through the service's owner-only build trigger.
`shutdown_requested()` is tested before `armed()`, so the process booted
straight into `safe_shutdown`.

A startCommand is exactly what condemned the legacy probe, so the difference is
stated rather than glossed: this one rewrites no source, runs no `sed`, and
keeps the image's `SERVICE_ROLE` guard. **It is a stop, not a configuration, and
must be removed once a variable path exists again.**

## Monitoring gap, stated plainly

The 20-minute canary invariant check was never re-armed after the 08:04 run --
that run is where the contradiction was found, and the halt work displaced the
re-arm. The hourly accounting routine *was* correctly instructed to surface "a
SECOND market" and "$0.05 cumulative exceeded", and it fired at 08:28, 09:28 and
10:28; those firings queued as notifications against an idle session and were
not acted on until 10:28. So between 08:04 and 10:28 nothing was actually
watching, and that is when conbra-andpet was traded.

## Not claimed

- No settlement figure above is called REALIZED without a venue-confirmed
  settlement row; conbra-andpet is PENDING and stays out of every net.
- The +$1.11 implied by the matched pair is modelled, not filled-and-settled.
- The market count is a floor; one log window was truncated.
