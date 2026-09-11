#!/usr/bin/env bash
set -euo pipefail

: "${GEMINI_L5_STATE_DIR:=/var/lib/gemini-l5}"
: "${GEMINI_L5_S3_URI:?set GEMINI_L5_S3_URI, e.g. s3://bucket/gemini-l5-shadow}"

# Archive research state only. No credentials are read from the repo; AWS CLI
# uses the EC2 instance role.
aws s3 sync "${GEMINI_L5_STATE_DIR}/" "${GEMINI_L5_S3_URI}/" \
  --only-show-errors \
  --exclude "*.tmp"
