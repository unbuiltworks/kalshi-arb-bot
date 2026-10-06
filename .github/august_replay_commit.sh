#!/usr/bin/env bash
# Commit ONLY this study's artifacts, under a two-directory allow-list, and
# only ever to the research branch. A path outside the allow-list aborts the
# job rather than widening the commit -- the mistake that once swept shadow
# output onto an authorized lineage.
set -uo pipefail
MSG="${1:?commit message required}"
ALLOW='^(analysis/l5/august_replay/|data/zenodo_22084977/)'

if [ "${GITHUB_REF_NAME:-}" != "claude/august-l5-forensic-replay-788cml" ]; then
  echo "::error::refusing to commit on ${GITHUB_REF_NAME:-<none>}"; exit 1
fi

# NO -f. An earlier version used it, reasoning that
# analysis/l5/historical_l2/{l5_backfill,block_times}.json are gitignored as
# regenerable caches. But those ignore entries name those exact PATHS, not a
# pattern, so the study's own copies were never ignored and the -f bought
# nothing -- while sweeping __pycache__/*.pyc into the archive commit, which
# IS ignored and has no business in an evidence commit.
git add -- analysis/l5/august_replay data/zenodo_22084977 2>/dev/null || true
git rm -r -q --cached --ignore-unmatch \
  analysis/l5/august_replay/__pycache__ 2>/dev/null || true

if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
if git diff --cached --name-only | grep -qvE "$ALLOW"; then
  echo "::error::refusing to commit outside the study's directories"
  git diff --cached --name-only | grep -vE "$ALLOW"
  exit 1
fi
git diff --cached --name-only | sed 's/^/  + /'
git commit -q -m "$MSG

Diagnostic historical replay only. NOT validation, NOT part of the
prospective 25-market Shadow S1 cohort, and must not be used to modify
Candidate S1 while that cohort is running. Candidate fingerprint asserted
unchanged: 441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UzBdAD7nfP8SBPk1bB3uDs"
for k in 1 2 3 4; do
  git pull --rebase -q origin "${GITHUB_REF_NAME}" \
    && git push -q origin "HEAD:${GITHUB_REF_NAME}" && exit 0
  sleep $((2**k))
done
echo "::error::push failed after 4 attempts"; exit 1
