#!/usr/bin/env bash
set -euo pipefail

: "${GEMINI_L5_IMAGE:=gemini-l5-shadow-aws:latest}"
: "${GEMINI_L5_STATE_DIR:=/var/lib/gemini-l5}"

mkdir -p "${GEMINI_L5_STATE_DIR}"

# READ-ONLY research container. Intentionally do not pass Gemini API keys.
docker rm -f gemini-l5-shadow-aws >/dev/null 2>&1 || true

docker run -d \
  --name gemini-l5-shadow-aws \
  --restart unless-stopped \
  -e GEMINI_SHADOW_ROLE=gemini-l5-shadow-aws \
  -e GEMINI_SHADOW_STATE_DIR=/state/gemini-shadow \
  -v "${GEMINI_L5_STATE_DIR}:/state/gemini-shadow" \
  "${GEMINI_L5_IMAGE}"

docker ps --filter name=gemini-l5-shadow-aws
