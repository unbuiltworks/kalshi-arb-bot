#!/bin/sh
# Collector + maker-engine shadow, one container, one volume.
#
# THE COLLECTOR IS THE FOREGROUND PROCESS. That is the whole safety design:
#   * the shadow runs in a background supervision loop, so if it dies,
#     collection is untouched and it comes back by itself within
#     MAKER_SHADOW_RESTART_S;
#   * a shadow crash-loop is rate-bounded by that same sleep;
#   * the collector is exec'd, so it keeps PID 1's signal handling and
#     Railway's restartPolicy (ALWAYS) still governs the container;
#   * nothing the shadow does can block, slow or stop the collector: it
#     issues NO network request and holds NO credential, it only tails files
#     the collector has already written.
set -u
ROOT="${BOOK_SHADOW_ROOT:-/state/l5-book-shadow}"

(
  while :; do
    python3 -m src.l5_maker_shadow_runner --root "$ROOT" || true
    sleep "${MAKER_SHADOW_RESTART_S:-30}"
  done
) &

exec python3 -m scripts.l5_book_shadow_run --minutes 0
