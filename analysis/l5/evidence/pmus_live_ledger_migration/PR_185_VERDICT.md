# Is PR #185 still necessary?

**Not for the phantom. Partly, on its own merits. Do not merge it as written,
because its stated premise is wrong.**

Owner instruction was "Do not merge it automatically if the recovered ledger is
coherent." The recovered ledger IS coherent: drift `{}`, `uncertainty_cleared
true`, zero `SETTLE_REFUSED`, before and after a restart. So it is not merged.

## Part 1, the phantom-inventory guard: premise falsified, and moot anyway

PR #185 states a cancelled, never-filled order left the `0.1300 DOWN`. The
venue's own `POSITION_RESOLUTION` record says the account really held 0.13
short-YES at `avgPx 0.3500`, `cost 0.0455`, and resolved it for `-0.0455`. The
defect was a missing cost basis, not a fictitious quantity. See
`THE_0_13_IS_NOT_A_PHANTOM.md`.

Its own body also concedes "This does **not** clear the 1345z phantom. It
instruments it." So merging it would never have fixed the production symptom.
The migration did, by reconstructing from venue evidence.

**What survives as genuinely valuable:** Invariant 1, recording the venue row
behind every cost-less adoption. Had it existed, the 0.13's real cost basis
would have been in the log and this would have been a ten-minute fix rather
than a day of mis-diagnosis. That is worth keeping -- with the commit message
and PR body corrected first, so the repository does not carry "a cancelled,
never-filled order" as the settled explanation.

**What should not be merged on this evidence:** anything that routes 1345z-like
inventory through `adopt_flat_account`. It posts no P&L, so it would delete a
venue-confirmed loss. The second cost-less quantity the engine itself cites --
`2.25 DOWN shares at 2045z with no cost` -- is now the case to re-check the
same way, against POSITION_RESOLUTION, before anything deletes it either.

## Part 2, the flat-anchor guard: keep, on independent merit

This is unrelated to the phantom diagnosis and stands on its own.
`adopt_flat_account` deletes `positions`, `leg_cost`, `fill_count`, `committed`
and `live_orders` and posts no P&L, its docstring said the anchor "must say so,"
and **nothing enforced it** -- the more destructive of the two sanctioned exits
was the less guarded one. Making it refuse unless the anchor positively attests
flat is correct regardless of what caused 1345z, and is *more* important now
that we know a cost-less quantity can be a real position: an unguarded
`adopt_flat_account` is exactly how a real loss gets deleted silently.

The PR also reports that a first version of the guard was wrong in a way worth
noting -- it validated field spellings rather than flatness and refused the
anchor this codebase ships. That it was caught by existing tests, and pinned by
a new one, is a point in the PR's favour.

## Part 3, image closure: keep

A module-scope import was in no image that copies `l5_live_execution`, so the
service would have failed to import and never started;
`tests/test_l5_live_probe_image_closure.py` caught it. That is a real latent
outage, unrelated to everything above.

## Recommendation

1. Do not merge as written. Merging redeploys `l5-pmus-live`, and the service is
   healthy right now on a clean ledger -- there is no symptom left to chase.
2. Correct the PR body and the commit message: the 0.13 was real and settled.
3. Then merge Parts 2 and 3 on their own merits, at a moment of the owner's
   choosing, since the deploy is the cost.
4. Re-examine the 2045z `2.25 DOWN` cost-less quantity against the venue's
   POSITION_RESOLUTION rows before any path deletes it.
5. `adopt_flat_account` still has not been run, and on this evidence should not
   be run for 1345z at all. It is settled, not flat-by-adoption.

Nothing in this file was pushed to PR #185; it is not a PR this session owns.
