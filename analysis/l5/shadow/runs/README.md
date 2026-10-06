# Run-scoped Shadow S1 reports

One file per CI run, named by its GitHub run id, written by
`scripts/shadow_cohort_report.py --out`.

CI commits **only** uniquely-named files: the tapes it just wrote and its
own `<run_id>.json`. It no longer touches the shared
`COHORT_PROGRESS.json` or `RUN_MANIFEST.json`.

## Why

Run 3 (`37403272240`) collected 12 COMPLETE markets and 157 evidenced
fills and **lost all of it**. Each segment committed locally and then
tried `git pull --rebase` before pushing. A local commit of mine had
rewritten `analysis/l5/shadow/COHORT_PROGRESS.json` at 02:31Z while the
run was about to commit its own version of that same file. The rebase hit
a content conflict on it at 02:37:19Z, left `.git/rebase-merge` behind,
and every push for the rest of the job died with "there is already a
rebase-merge directory" — four retries per segment, all fatal. The job
kept collecting and committing to a detached HEAD that never reached
origin, and the evidence died with the runner.

Two independent defects, both mine:

1. **A shared mutable file in the CI commit set.** Any concurrent edit to
   it guarantees a rebase conflict. Fixed by scoping CI's writes to
   per-run filenames that cannot collide.
2. **No rebase hygiene and a silent failure.** The retry loop re-ran
   `git pull --rebase` into a wedged repo and `|| sleep` swallowed every
   error, so "could not push" looked identical to "nothing to commit".
   Fixed by clearing any half-finished rebase before each pull, using
   `--autostash`, and emitting a `::error::` when a committed segment
   cannot be pushed.

A third mistake compounded them: I read "no tape on origin" as "no data
collected" and cancelled the run, which destroyed the only copy. The log
was the evidence that collection was working, and job logs are not
readable while a job runs — so cancelling to read the log is what cost the
data. Future diagnosis must assume unpushed work exists on the runner.

## Standing rule

Do not commit anything under `analysis/l5/shadow/` or `data/l5/shadow/`
from a workstation while a collection run is live.
