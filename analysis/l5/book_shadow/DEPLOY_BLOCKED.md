# Post-A6 queue: two hard blockers, neither overridden

2026-10-07T02:45Z. A6 is terminal, disarmed, no open order. The queued
work ran into two platform/safety blockers. **Neither was worked around**,
because the only available workarounds were worse than stopping.

## 1. Credentialed incentive re-audit — REFUSED BY ITS OWN SAFETY GATE

Deployed the read-only re-audit image to `l5-live-probe-oneshot`, the only
PMUS-credentialed service tracking this branch. It refused before issuing
a single request:

```
REFUSING: execution arming variables are set in this process
(PMUS_Q1_LIVE, PMUS_Q1_SESSION_ID, PMUS_Q1_GATE_EVIDENCE).
Run the read-only re-audit on a service that is NOT carrying an armed probe.
```

**The gate worked exactly as designed.** Those are the **Q1 executor's**
arming values — a different experiment's authorization state, unrelated to
EXEC-1. Clearing them would make the read run, and it is precisely the
move this programme has forbidden over and over: *do not modify something
to force execution*. They stay as they are.

The service's XR2W build config was **restored** (`dockerfilePath`
`deploy/l5_exec1xr2w/Dockerfile`, start command
`scripts.l5_exec1xr2w_run`, restart NEVER), so the probe service is back
to the state a future authorized attempt expects. Its latest deployment
reads CRASHED — that is the refused re-audit, not a probe failure.

**The other credentialed host** (`pmus-readonly-recovery`, which carries
`PMUS_API_KEY_ID`/`PMUS_API_SECRET_KEY` and **no** `PMUS_Q1_*` keys) tracks
branch `feature/l5-transfer-backtest`, where `scripts/pmus_incentive_reaudit`
does not exist. `connect-service-source` applies to **all environments** by
default, so repointing it is a wider change than I will make unasked at
02:45 to a service whose purpose is order recovery.

I also cannot provision a fresh credentialed service: variable **values**
are redacted to this session (names only), so the credential cannot be
copied anywhere.

### Options, owner's call
1. Clear the three `PMUS_Q1_*` values on `l5-live-probe-oneshot` — only if
   Q1 is genuinely dormant. One variable write, then redeploy the audit
   image. **Touches another experiment's arming state.**
2. Repoint `pmus-readonly-recovery` to this branch (+ Dockerfile path and
   start command), run, then revert. Touches no arming state; changes an
   unrelated service's source across environments.
3. Run it from wherever the credential already lives outside Railway.

## 2. Book-shadow collector — PROJECT VOLUME CAP

`create-volume` returned **"You can only have 20 volumes per project"**.
The project already lists **21** volumes, every one mounted by another
lane's collector or probe (`pmus-ws-evidence-recorder`, `l5-live-habitat`,
`gemini-5m-tape`, `l5-5m-tape`, `l5-research-state`, …).

The runbook forbids reusing an existing mount, and it is right to: a
second writer on a shared volume is what filled the recorder's 50 GB
volume before. Deleting any volume would destroy another lane's evidence,
so I did neither.

An **empty service `l5-book-shadow`** (`93d3aa51-9fb5-4334-90cc-95ccf74d49d6`)
was created and left with no source and no volume. It deploys nothing and
costs nothing; it is the placeholder the runbook asks for.

### Options, owner's call
1. Free a volume: archive one lane's tape off-volume, then delete it.
2. Raise the per-project volume cap with Railway, or
3. Run the collector in a different project with volume headroom — it
   needs **no credentials** (every feed is public and keyless), so
   cross-project placement is safe, though it breaks the isolation
   convention `deploy/RAILWAY_ISOLATION.md` exists to keep.
4. Run it without a volume and accept an ephemeral tape — **not
   recommended**: the whole point is a durable artifact, and a restart
   would silently lose it.

## 3. Tennis + soccer collection — BLOCKED BY (2)

It rides the same collector's `BOOK_SHADOW_SERIES` selector, so it starts
when the collector does.

## What is NOT blocked

Nothing about A6 or the analysis. All five owner values remain disarmed,
the latch is spent, there is no open order, and the five completed
research lanes are committed. The Attempt-6 fill still needs settlement
and cash confirmation, which is what the re-audit above would also read.
