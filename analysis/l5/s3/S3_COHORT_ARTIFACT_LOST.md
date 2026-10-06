# S3 cohort run 37469519509 — ARTIFACT LOST IN CI. EVIDENCE NOT RECOVERABLE.

**The collection and the measurement both succeeded. The output was then
destroyed by a failed push, and the runner was torn down with it.**

I cannot report the metrics that were asked for, because they no longer
exist anywhere. What follows is everything that survives, and nothing is
reconstructed or estimated.

## What survives

From the job log, the only trace of the instrument's output:

```
written S3_COHORT.json: 5 markets, halt=True
```

And the collector's own final line:

```
PMUS_BOOK_TAPE_DONE {"rows": 16160, "cycles": 8071,
  "file": "data/s3_cohort/pmus_btcupdown15m_20261006T131748Z.jsonl"}
-rw-r--r-- 1 runner runner 15460137 Oct 6 18:17 pmus_btcupdown15m_...jsonl
```

So: 5 hours of collection (13:17:48 → 18:17:49Z), 8,071 cycles, 16,160 book
rows, a 15.46 MB tape, the frozen instrument ran over it, and it wrote an
artifact describing **5 markets with `halt=True`**.

## What is gone, and therefore not reported

`markets_COMPLETE` / `PARTIAL` counts, qualifying opportunities, orders,
M1 evidenced fills, pair economics, residual, fees, reservation stand-downs,
ledger counts, per-market `span_s` and `max_observation_gap_s`, and — the one
that matters most — **the classification of the halt as
`INSTRUMENTATION_FAILURE` versus `VALID_ZERO_FILL_EVIDENCE`**. The
classifier exists and ran; its verdict was inside the lost file. I will not
guess it from `halt=True`, because distinguishing those two is precisely what
the classifier was built for.

The tape itself is also gone, so the measurement cannot be re-derived. This
is an acquisition loss, not a zero-fill measurement, and the cohort is not
advanced.

## Root cause — two faults, one of them mine

**1. The workflow cannot survive a concurrent push.** Step 9 is:

```sh
git commit -F /tmp/m.txt
for i in 1 2 3 4; do
  git push origin claude/august-l5-forensic-replay-788cml && break
  sleep $((2**i))
done
```

There is no `git pull --rebase` anywhere. All four pushes were rejected
non-fast-forward:

```
! [rejected] claude/august-l5-forensic-replay-788cml -> ... (fetch first)
hint: Updates were rejected because the remote contains work that you do not
hint: have locally.
```

**2. I was the concurrent writer.** I spent 14:00–18:18Z pushing the EXEC-1 /
XR / XR2 work to the same branch the cohort was committing to. The cohort's
commit `32b6846` was created locally on the runner and never left it.

**3. The step reported success anyway.** The last command in the retry loop
is `sleep`, which exits 0, so `bash -e` never saw a failure. GitHub recorded
step 9 "Commit artifacts" as **success** while the artifact was being lost.
That is why the run shows `conclusion: success` with nothing to show for it.

## What I am not doing

Per instruction, S3 is not mutated: no change to the 0.96 reservation, the
frozen fingerprint `8b20a259…87df`, the instrument, the cohort runner or the
protocol. The cohort is **not** advanced and **not** evaluated — there is
nothing to evaluate.

## Recommended fixes, for your decision (not applied)

1. **Commit cohort artifacts to a dedicated branch**
   (`data/s3-cohort-<runid>`), never the shared working branch. Removes the
   race entirely rather than retrying through it.
2. **Make the retry loop able to fail**: `git pull --rebase` between
   attempts, and `exit 1` after the last one, so a lost artifact fails the
   step instead of passing it.
3. **Upload the tape as a workflow artifact as well**, so a push failure
   costs a commit and not the data.
4. Only then re-fire the sentinel for a fresh accumulation run.

Fix 2 is the important one: the run that destroyed this evidence is still
recorded as a success, and until that is true no future cohort result can be
trusted to have survived.
