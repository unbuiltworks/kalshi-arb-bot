#!/usr/bin/env bash
# Restart any L5 research stream that is not running. Idempotent.
#
# WHY THIS EXISTS. These are read-only research services running inside an
# EPHEMERAL container. It was reclaimed and restarted at 14:27Z on 2026-09-15
# and took the depth probe and the chain validator with it; their durable
# JSONL survived on disk, but the series stopped and nothing noticed until a
# process listing happened to be run.
#
# This restores every stream in one command, appending to the SAME out-dirs so
# the series continues rather than restarting. It does not solve the real
# problem -- a container that dies while nobody is looking stays dead. The
# durable fix is the prepared Railway service (deploy/l5_exec_shadow/), which
# is deliberately not created because creating it costs money and is the
# owner's call.
set -u
ROOT="${L5_ROOT:-/home/user/kalshi-arb-bot}"
STATE="${L5_STATE:?set L5_STATE to the scratchpad dir}"
cd "$ROOT" || exit 1

alive() { ps -eo comm,args | awk -v p="$1" '$1=="python3" && index($0,p)>0 {n++} END{exit !n}'; }

start() {                      # name, module, out-dir, logfile
  if alive "$2"; then
    echo "ok       $1 (already running)"
    return
  fi
  mkdir -p "$3"
  nohup python3 -m "$2" --out-dir "$3" >> "$4" 2>&1 &
  sleep 2
  if alive "$2"; then echo "STARTED  $1"; else echo "FAILED   $1 -- see $4"; fi
}

start "executable shadow" src.l5_shadow.service \
      "$STATE/l5-shadow-state" "$STATE/l5-shadow.log"
start "gemini depth probe" analysis.l5.gemini_depth.probe \
      "$STATE/gemdepth" "$STATE/gemdepth.log"
start "chain validator"    analysis.l5.chain_observer.validate \
      "$STATE/chainval" "$STATE/chainval.log"
