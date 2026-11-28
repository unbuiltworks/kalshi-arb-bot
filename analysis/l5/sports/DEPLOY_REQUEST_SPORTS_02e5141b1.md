# Deployment request 2 of 2 — sports bot, locked (no orders, no arming)

Prepared 23 Nov 2026. **Not approved, not deployed.** This request covers
deploying the sports bot's new build with the submission lock left on. It
places no order, arms nothing, permits no new competition and changes no
limit. It is separate from the watchdog request, from any drill with a live
order and from any campaign.

| Item | Value |
|---|---|
| Service | `pmus-spring-l5-probe` |
| Running now | `158685d3ab0b446ac31387bf3ba32bb7bc5659fd`, locked, zero resting orders |
| Candidate | branch `release/pmus-sports-rn1-02e5141b1`, commit `02e5141b13a41eca7224671a22bd4fec9ff26c6f` (code `0c554dd09` plus the build trigger line) |
| Supersedes | `release/pmus-sports-rn1-1b9f4325f`: same code; its note understated the default changes below |
| Size | 12 commits, 16 files since the running build |

## What is different with no setting changed

These take effect on deployment, with every variable left as it is:

1. The campaign marker follows the configured window, and a marker that
   does not cover the window blocks new exposure (the defect found on 22
   Nov when a window was moved earlier).
2. A bid needs a fair value that has stayed within 1c for 10 seconds, and
   each side is judged against the least favourable value over that time.
3. Order management runs between the hourly audit's requests (the cause of
   the 89-second late fill).
4. The account ledger's risk document is read on its own thread, and the
   ledger's drawdown figure is evaluated. Production has this check on
   observe, so it logs and blocks nothing.
5. Extra measurement in logs and in the registry the watchdog reads: phase,
   how late each fill was seen, and when orders were last served.

All five are either stricter or observational.

## What stays off until a setting is added

Pre-game slate and kickoff hold; order size by phase; price-move pause;
portfolio cap on resting orders plus unpaired inventory; ranking by net;
late-fill latch; audit latch; order-service stall limit; hysteresis (off by
the owner's decision); league mapping; the death-drill switch.

## Final checks done

- Full suite on the candidate's code: 5,287 passed, 24 failed. The same 24
  tests failed on the build before this work.
- The release branch differs from the tested development commit only by the
  build trigger line.
- Image check: the two entry modules and the engine import with only the 67
  paths the Dockerfile copies. One lazy import is absent from the image
  (`src/polymarket_l5/action_model_v2.py`, used by a persistence module);
  that file is unchanged since the running build, so it is not new.
- A no-credentials dry run of this code on live public data across four
  real kickoffs.

## Steps, each needing this approval

1. Confirm the lock is on (`PMUS_TWO_SIDED_SUBMISSION_LOCK=1`) and zero
   orders rest. No variable is changed.
2. Connect the service to the candidate branch and commit; it builds.
3. Verify from logs: boot line shows the lock, live slate, account risk on
   observe and the reader started; every decision line carries
   `submission_lock:locked`; zero orders; watchdog reads the registry OK.
4. Verify the first audit, about two minutes after boot: the order-service
   gap stays near the loop's five seconds instead of about 85.
5. Leave it locked.

## Rollback

Reconnect the service to `release/pmus-sports-rn1-158685d3a` at
`158685d3a`. The lock stays on throughout, so there is nothing to unwind.
