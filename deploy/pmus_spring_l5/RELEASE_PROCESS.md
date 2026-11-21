# Sports bot release process (pmus-spring-l5-probe)

Owner 2026-11-21: production deploys an explicitly selected, tested SHA; development can
never reach production by accident.

## Why this exists

2026-11-21 23:29Z: a variable change on the sports service (combo study off) restarted it.
Railway rebuilt the **tip of the deployed branch** (`567ecf01e`), not the pinned commit
(`5c7384ff7`), so two unapproved dev commits ran in production (lock on, no orders). The
service config still showed the pinned SHA. Cause: production followed a branch that
development also pushed to.

## Branches

| Ref | Purpose | Who moves it |
|---|---|---|
| `dev/pmus-rn1-replica` | all development | any dev session |
| `release/pmus-sports-rn1-<sha9>` | one per approved release; created at the approved SHA, **never pushed to again** | production coordinator, after owner approval |

A release branch holds exactly one approved commit at its tip. So a variable-triggered
rebuild of "the branch tip" can only ever rebuild the approved commit. New releases get a
**new** branch name; nothing is force-pushed or rewritten.

## Releasing

1. A tested commit on `dev/...` is named to the owner with its exact settings.
2. On approval: `git push origin <sha>:refs/heads/release/pmus-sports-rn1-<sha9>` (new ref).
3. Railway `connect-service-source` with that branch **and** `commitSha=<sha>`.
4. If the build is SKIPPED (watch pattern), redeploy that deployment id.
5. Verify (below) before calling the release live.

## Verification after every deploy AND every variable change

- `list-deployments`: the newest deployment's `meta.commitHash` == approved SHA and
  `meta.branch` == the release branch. The service config's `commitSha` is not enough.
- Boot log: `boot` line settings match the approved config; `intl_guard_enabled.require`;
  no `combo_study_enabled`; combo maker `mode: shadow`.
- Orders: `place_refused_submission_lock` while locked; watchdog `open_orders: 0`.
- Heartbeat: `venue_requests` present (shared budget active).

Any mismatch: report to the owner; do not restart again without authorization.

## Not in place (would need the owner / GitHub settings)

- GitHub branch protection on `release/*` (block pushes, deletion). Not set from this
  session; release branches are protected by convention only.
- Railway has no "deploy pinned SHA only, never branch tip" setting visible through the
  tools; the one-commit release branch makes the tip and the pin the same.
