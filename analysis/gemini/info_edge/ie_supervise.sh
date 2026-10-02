#!/usr/bin/env bash
# Durable multi-day supervisor for the information-edge collector.
# Relaunches in bounded segments so a single failure cannot end collection,
# and each relaunch re-reads the egress proxy port (which rotates).
set -u
OUT="${1:-data/gemini_infoedge}"
SEG="${2:-3300}"
cd "$(dirname "$0")/../../.." || exit 1
mkdir -p "$OUT" logs
while true; do
  echo "$(date -u +%FT%TZ) starting segment (${SEG}s)" >> logs/ie_supervisor.log
  python3 analysis/gemini/info_edge/ie_collector.py \
      --outdir "$OUT" --duration-s "$SEG" >> logs/ie_supervisor.log 2>&1
  echo "$(date -u +%FT%TZ) segment ended rc=$? ; restarting in 5s" >> logs/ie_supervisor.log
  sleep 5
done
