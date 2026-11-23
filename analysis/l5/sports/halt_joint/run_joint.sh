#!/bin/sh
# Joint regression: the sports bot and the watchdog's emergency-halt path,
# run together from the two source trees as deployed.
#   run_joint.sh <sports-tree> <watchdog-tree>
# Builds a throwaway tree: the sports tree's src/ and tests/ plus the four
# watchdog modules the sports tree does not carry. Nothing is deployed and
# no network is used.
set -e
SPORTS="$1"; WD="$2"; HERE="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
cp -r "$SPORTS/src" "$T/src"; cp -r "$SPORTS/tests" "$T/tests"
cmp -s "$SPORTS/src/l5_halt_gate.py" "$WD/src/l5_halt_gate.py" || {
  echo "l5_halt_gate.py differs between the two trees"; exit 2; }
for f in l5_watchdog l5_emergency_halt_marker l5_managed_lanes \
         l5_order_registry_server; do
  test ! -e "$T/src/$f.py"; cp "$WD/src/$f.py" "$T/src/$f.py"
done
cp "$HERE/test_joint_halt_cancel.py" "$T/tests/"
cd "$T" && python -m pytest tests/test_joint_halt_cancel.py -q -p no:cacheprovider
