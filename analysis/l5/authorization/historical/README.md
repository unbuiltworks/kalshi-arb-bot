# Historical authorization records — NON-EXECUTABLE

Nothing in this directory is an authorization. These are audit records of
authorizations that were granted in the past and are now spent or expired.

## Why they were moved here (owner, 2026-09-28)

`analysis/l5/authorization/live_mandate.json` was **git-tracked** and held the
expired `OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT` authorization — $5 per
market, $15 total, 10 markets, service `l5-r1-probe`, expired 2026-09-18T00:41:20Z.

`pmus_live_mandate.load_mandate()` falls back to that exact path when
`PMUS_LIVE_MANDATE` is unset, and `parse_mandate` accepted it **without error**,
because expiry and service are checked at `admit()` time rather than at load. A
probe started with no authorization in its environment would therefore load $15 of
authority from the repository, arm, and only fail at its first order.

`admit()` would have refused that order, so no money could move. But
"a mandate loaded" is not "authority exists", and this is exactly the
*"we could not read today's authorization, so we used yesterday's"* failure that
`pmus_live_mandate`'s own docstring names as the whole 2026-09-16 incident — in
which an authorized $0.05 became a committed $19.3295.

## The rule now

**No explicit `PMUS_LIVE_MANDATE` environment value = no live authority. Period.**

For the Rung-A executor there is no fallback to a repository file, a historical
mandate, a previous or cached authorization, or a default monetary envelope.
`scripts/l5_pmus_live_oneshot.rung_a_mandate()` requires the environment variable
and validates expiry, service and strategy at load;
`tests/test_mandate_no_fallback.py` proves it holds even with an expired mandate
file present in the repository.

## The file extension is deliberate

`.record` is not `.json`, and the filename is not `live_mandate.json`. Nothing
resolves it as authority by path or by glob. Keep it that way: restoring either
the name or the location re-creates the fallback.
