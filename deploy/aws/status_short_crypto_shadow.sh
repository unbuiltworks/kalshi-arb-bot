#!/usr/bin/env bash
set -euo pipefail

containers=(
  arb-short-poly-maker-ws
  arb-short-gemini-maker
  arb-short-gemini-fair-value
  arb-short-kalshi-temporal
  arb-short-kalshi-incentive
)

printf '=== SHORT CRYPTO SHADOW STATUS ===\n'
for c in "${containers[@]}"; do
  printf '\n--- %s ---\n' "$c"
  docker ps -a --filter "name=^/${c}$" --format 'status={{.Status}} image={{.Image}} names={{.Names}}' || true
  docker logs --tail 20 "$c" 2>&1 || true
done
