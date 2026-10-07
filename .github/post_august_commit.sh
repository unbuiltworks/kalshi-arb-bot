#!/usr/bin/env bash
# Commit ONLY the post-August harvest's own artifacts, under a two-directory
# allow-list, and only ever to the research branch. A path outside the
# allow-list ABORTS the job rather than widening the commit.
#
# The allow-list deliberately cannot reach:
#   analysis/l5/s3/**, data/l5/shadow/**, analysis/l5/shadow/**,
#   src/**/live_exec*, deploy/**, .github/workflows/polymarket-shadow-s1.yml
# so this job cannot disturb the running shadow cohort, the S3 cohort, the
# l5-book-shadow collector, or any executor module.
set -uo pipefail
MSG="${1:?commit message required}"
ALLOW='^(data/l5/raw/post_august/|analysis/l5/post_august/)'

if [ "${GITHUB_REF_NAME:-}" != "claude/august-l5-forensic-replay-788cml" ]; then
  echo "::error::refusing to commit on ${GITHUB_REF_NAME:-<none>}"; exit 1
fi

git add -- data/l5/raw/post_august analysis/l5/post_august 2>/dev/null || true

if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
if git diff --cached --name-only | grep -qvE "$ALLOW"; then
  echo "::error::refusing to commit outside this study's directories"
  git diff --cached --name-only | grep -vE "$ALLOW"
  exit 1
fi
git diff --cached --name-only | sed 's/^/  + /'
git commit -q -m "$MSG

Read-only observational harvest of another trader's PUBLIC activity.
No figure derived from it is submitted, filled, settled or realized profit
for our account, and L5's own REWARD/MAKER_REBATE/TAKER_REBATE rows are HIS
incentive cashflows -- not evidence that any equivalent program applies to us.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UzBdAD7nfP8SBPk1bB3uDs"

for k in 1 2 3 4; do
  git pull --rebase -q origin "${GITHUB_REF_NAME}" \
    && git push -q origin "HEAD:${GITHUB_REF_NAME}" && exit 0
  sleep $((2**k))
done
echo "::error::push failed after 4 attempts"; exit 1
