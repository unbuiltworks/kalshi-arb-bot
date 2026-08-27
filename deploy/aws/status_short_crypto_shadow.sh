#!/usr/bin/env bash
set -euo pipefail

# Compact health/status check for the isolated AWS short-crypto shadow stack.
# This script is read-only. It never starts/stops containers or changes config.

containers=(
  arb-short-poly-maker-ws
  arb-short-gemini-maker
  arb-short-gemini-fair-value
  arb-short-kalshi-temporal
  arb-short-kalshi-incentive
)

declare -A summary_patterns=(
  [arb-short-poly-maker-ws]='POLY_MAKER_WS_SHADOW_SUMMARY'
  [arb-short-gemini-maker]='GEMINI_MAKER_SHADOW_SUMMARY'
  [arb-short-gemini-fair-value]='GEMINI_FAIR_VALUE_SHADOW_SUMMARY'
  [arb-short-kalshi-temporal]='KALSHI_TEMPORAL_SHADOW_SUMMARY|KALSHI_TEMPORAL_LIVE_SHADOW_SUMMARY'
  [arb-short-kalshi-incentive]='KALSHI_INCENTIVE_SHADOW_SUMMARY'
)

printf '=== SHORT CRYPTO AWS HEALTH ===\n'
printf 'checked_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

healthy=0
unhealthy=0
for c in "${containers[@]}"; do
  printf '\n--- %s ---\n' "$c"

  state="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
  restart_count="$(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || echo n/a)"
  started_at="$(docker inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null || echo n/a)"
  printf 'state=%s restart_count=%s started_at=%s\n' "$state" "$restart_count" "$started_at"

  recent="$(docker logs --since 2m "$c" 2>&1 || true)"
  pattern="${summary_patterns[$c]}"
  last_summary="$(printf '%s\n' "$recent" | grep -E "$pattern" | tail -n 1 || true)"

  if [[ "$state" == "running" && -n "$last_summary" ]]; then
    printf 'feed_health=LIVE\n'
    printf 'last_summary=%s\n' "$last_summary"
    healthy=$((healthy + 1))
  else
    printf 'feed_health=STALE_OR_UNKNOWN\n'
    printf 'recent_tail:\n'
    printf '%s\n' "$recent" | tail -n 8
    unhealthy=$((unhealthy + 1))
  fi
done

printf '\n=== RESULT ===\n'
printf 'healthy=%d unhealthy=%d total=%d\n' "$healthy" "$unhealthy" "${#containers[@]}"

if (( unhealthy > 0 )); then
  exit 2
fi
