#!/usr/bin/env bash
# Durable multi-day supervisor for the information-edge collector.
# Relaunches in bounded segments so a single failure cannot end collection,
# and each relaunch re-reads the egress proxy port (which rotates).
#
# ARG 3 is the series regex, so a SECOND, ISOLATED instance can collect a
# different contract series without touching the first. That isolation is
# deliberate: the BTC05M tapes feed the sealed 100-window statistical gate,
# and adding subscriptions to that process would put the gate's own data
# quality at risk to answer an unrelated question. A separate process with a
# separate outdir cannot.
set -u
OUT="${1:-data/gemini_infoedge}"
SEG="${2:-3300}"
# NOTE: a '$' inside a ${x:-default} expansion is ambiguous to bash and
# mangled the regex to '...$$}' on the first attempt, so the default is
# assigned in two steps instead.
RE="${3:-}"
if [ -z "$RE" ]; then RE='^BTC05M\d{10}$'; fi
LOG="${4:-logs/ie_supervisor.log}"
cd "$(dirname "$0")/../../.." || exit 1
mkdir -p "$OUT" logs
while true; do
  echo "$(date -u +%FT%TZ) starting segment (${SEG}s) series=${RE}" >> "$LOG"
  python3 analysis/gemini/info_edge/ie_collector.py \
      --outdir "$OUT" --duration-s "$SEG" --series-regex "$RE" >> "$LOG" 2>&1
  echo "$(date -u +%FT%TZ) segment ended rc=$? ; restarting in 5s" >> "$LOG"
  sleep 5
done
