#!/usr/bin/env bash
# Install ONE bounded-campaign approval into L5_CAMPAIGN_APPROVAL without the
# document's contents ever passing through a transcript, an argv list, a log,
# or an MCP tool parameter.
#
# WHY THIS SCRIPT EXISTS. `mcp__Railway__set-variables` takes the value as a
# tool parameter, which puts the document in the conversation. This path keeps
# the value inside the shell: minted to a mode-700 directory, read by `jq
# --rawfile` (never argv), POSTed from a mode-600 payload file, and verified by
# comparing sha256 digests rather than values. stdout carries digests and
# non-secret scope fields only.
#
# CREDENTIAL. Railway project tokens authenticate with the `Project-Access-Token`
# header, NOT `Authorization: Bearer`. That matters here: this session's egress
# proxy replaces `Authorization`, so account tokens (RAILWAY_API_TOKEN) cannot
# reach Railway from this container at all, while `Project-Access-Token` passes
# through untouched. Export the project token as RAILWAY_PROJECT_TOKEN.
set -euo pipefail

: "${RAILWAY_PROJECT_TOKEN:?export RAILWAY_PROJECT_TOKEN (a Railway *project* token, not an account token)}"

PROJECT_ID="50fb7357-e2bc-4d04-a49e-71eb5d73b0d1"      # arb-ev-wallet-shadow
ENVIRONMENT_ID="f3642d27-26dc-4e21-9170-bc76290c076f"  # production
SERVICE_ID="87f1834e-530e-4fc4-a310-91c8d3add196"      # l5-pmus-live
VAR_NAME="L5_CAMPAIGN_APPROVAL"
API="https://backboard.railway.com/graphql/v2"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"; chmod 700 "$WORK"
DOC="$WORK/approval.json"; PAYLOAD="$WORK/payload.json"
cleanup() { shred -u "$DOC" "$PAYLOAD" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT

echo "== 1. mint + grade against the DEPLOYED gate =="
# The mint tool ALREADY verifies the document against the real
# `CA.ApprovalGate` and exits non-zero if it is refused, printing the
# effective scope with the nonce masked. Re-grading it here would be a
# second, unreviewed copy of that check, so we use its exit code and its
# stdout instead.
MINT="$( cd "$REPO" && python3 tools/make_campaign_approval.py "$DOC" )" || {
  echo "mint/grade FAILED -- nothing installed"; exit 1; }
chmod 600 "$DOC"
echo "$MINT" | jq '{document_sha256, gate_says_approved, gate_why, effective_scope}'
echo "$MINT" | jq -e '.gate_says_approved == true' >/dev/null || {
  echo "REFUSING TO INSTALL: the deployed gate does not approve this document"; exit 1; }
LOCAL_SHA="$(sha256sum "$DOC" | cut -d' ' -f1)"
echo "$MINT" | jq -e --arg s "$LOCAL_SHA" '.document_sha256 == $s' >/dev/null || {
  echo "REFUSING TO INSTALL: on-disk document does not match the graded digest"; exit 1; }

echo "== 3. build payload (value via --rawfile, never argv) =="
jq -n --rawfile v "$DOC" \
  --arg p "$PROJECT_ID" --arg e "$ENVIRONMENT_ID" --arg s "$SERVICE_ID" --arg n "$VAR_NAME" \
  '{query:"mutation variableUpsert($input: VariableUpsertInput!){variableUpsert(input:$input)}",
    variables:{input:{projectId:$p,environmentId:$e,serviceId:$s,name:$n,value:$v}}}' > "$PAYLOAD"
chmod 600 "$PAYLOAD"

echo "== 4. install =="
RESP="$(curl -sS -X POST "$API" -H "Content-Type: application/json" \
  -H "Project-Access-Token: $RAILWAY_PROJECT_TOKEN" --data @"$PAYLOAD")"
echo "$RESP" | jq -e '.errors == null' >/dev/null || { echo "install FAILED: $RESP"; exit 1; }
echo "variableUpsert: ok"

echo "== 5. verify by DIGEST (read back, hash, never print) =="
REMOTE_SHA="$(curl -sS -X POST "$API" -H "Content-Type: application/json" \
  -H "Project-Access-Token: $RAILWAY_PROJECT_TOKEN" \
  --data "$(jq -n --arg p "$PROJECT_ID" --arg e "$ENVIRONMENT_ID" --arg s "$SERVICE_ID" \
    '{query:"query v($projectId:String!,$environmentId:String!,$serviceId:String){variables(projectId:$projectId,environmentId:$environmentId,serviceId:$serviceId)}",
      variables:{projectId:$p,environmentId:$e,serviceId:$s}}')" \
  | jq -j --arg n "$VAR_NAME" '.data.variables[$n]' | sha256sum | cut -d' ' -f1)"

echo "local_sha256 : $LOCAL_SHA"
echo "remote_sha256: $REMOTE_SHA"
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] && echo "FINGERPRINT MATCH -- installed intact" \
  || { echo "FINGERPRINT MISMATCH -- do not arm"; exit 1; }
