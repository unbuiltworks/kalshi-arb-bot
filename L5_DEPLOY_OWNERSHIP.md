# Deployment ownership of `l5-pmus-live`

**Designated owner: session `01EZpEjFttFNon6KS2sB33Sy` (this session), from 2026-10-26.**
Set at the owner's instruction. This file is the coordination point; it is not an
enforcement mechanism, and nothing in Railway prevents another session deploying.

## Why this exists

`497b1152` already recorded the failure it is meant to prevent, in its own words:

> *"two Claude sessions are deploying to the same money-capable service. An arming
> attempt and an unrelated deploy can interleave so neither session knows the
> in-process state its gates depend on; a restart mid-window silently voids entry
> runway; and worst case one session's deploy can make another's armed configuration
> live at a moment nobody chose -- which is close to what happened, harmless only by
> timing. Recommend one session owns l5-pmus-live at a time."*

The documented sequence, 2026-10-25: one session wrote 11 variables at ~18:37 with no
restart, so the running container kept `L5_SHADOW_ONLY=1` from its 18:28:50 boot. At
19:00:47 **a different session** (`session_01255E3fhWg7BWTviGCMRknt`) deployed `3b3e1583`,
which is what first made the other session's `shadow=0` effective, about one minute into
the 1900z window. At 19:03:03 the first session's deployment was REMOVED. At 19:24:46
that session deployed again mid-window. Two correct readings of the arming state, taken
21 s apart, disagreed because they straddled the container swap.

That campaign ended 0 submissions, 0 fills, 0 settlements, $0.00 realized, 0 of 3 windows
consumed. The failure was in the safe direction. It did not have to be.

## The live hazard right now

**Production runs `497b1152`. The branch head `claude/l5-defensive-8window-prod` is 46
commits ahead of it, and none of them is deployed.**

Deployment `482cb9e3` (2026-10-26T02:47:23Z, SUCCESS, 1 replica) was built from
`497b1152`. Deployment `d713062a` carried the branch head `f22761ae` and is **SKIPPED** —
the service's only build `watchPatterns` entry is `deploy/l5_pmus_live/DEPLOY_TRIGGER`,
and not one of the 46 commits touches that file, so every one of them was skipped.

Those 46 commits include changes to **`src/l5_live_validation_gates.py` and
`src/l5_live_execution.py`** — the money path and its refusal gates. They have never run
in production.

So: **touching `deploy/l5_pmus_live/DEPLOY_TRIGGER` ships all 46 at once to a
money-capable service.** That is not a deploy, it is a 46-commit cutover wearing the
costume of a one-line change. `src/l5_pmus_live_runner.py` happens to be byte-identical
between `497b1152` and the branch head, which is why the wallet-ingestion audit on the
head applies to production — but that is luck about one file, not a property of the set.

## Procedure

1. **Before any deploy, restart, variable write intended to change in-process state, or
   arming action on `l5-pmus-live`:** say so here or to the owner first, and name the
   commit that will actually become live — not the branch head, the commit Railway will
   build.
2. **After a variable write, verify in-process effect** by a fresh boot preflight line,
   never by the write succeeding. `497b1152` is the record of asserting an outcome from an
   action rather than from evidence.
3. **Never restart mid-window.** Entry is confined to roughly the first 300 s of a window
   by `ts_entry_cutoff_s` 600 and `ts_min_entry_runway_s` 300; a restart past that
   silently spends the window.
4. **Before the next trigger touch**, decide the 46-commit cutover deliberately: review
   the validation-gate and execution deltas, or deploy a narrower branch.

## Not done by this session

No deploy, restart, variable write, arming action or live order. The wallet-ingestion fix
sits on `claude/l5-wallet-ingestion-fix-kqcqoa`; both `l5-pmus-live` and `l5-pmus-observer`
track other branches, both verified unchanged, so pushing it deployed nothing.
