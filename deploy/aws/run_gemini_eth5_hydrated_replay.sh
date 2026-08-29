#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE=${1:?image required}
INPUT=${2:?retained capture required}
OUTPUT_DIR=${3:?output dir required}

[[ -f "$INPUT" ]]
mkdir -p "$OUTPUT_DIR"
rm -f "$OUTPUT_DIR/hydrated.jsonl" "$OUTPUT_DIR/hydrate-summary.json" "$OUTPUT_DIR/touch.json" "$OUTPUT_DIR/strict.json"

# Network is enabled ONLY for public Gemini market-data hydration. No env file,
# API key, secret, balance endpoint, or account mount is supplied to the container.
docker run --rm \
  -v "$INPUT:/input/capture.jsonl:ro" \
  -v "$OUTPUT_DIR:/output" \
  "$IMAGE" \
  python -m src.gemini_retained_eth5_trade_hydrate \
    --input /input/capture.jsonl \
    --output /output/hydrated.jsonl \
    --summary-output /output/hydrate-summary.json

# Replays are forced offline after the public trades have been written.
docker run --rm --network none \
  -v "$OUTPUT_DIR:/input:ro" \
  "$IMAGE" \
  python -m src.gemini_dynamic_initial_entry_replay_q1 \
    --input /input/hydrated.jsonl \
    --evidence-mode TAPE_TOUCH_OPTIMISTIC \
    --asset ETH --horizon 5m \
    --output /tmp/touch.json --compact | tee "$OUTPUT_DIR/touch.stdout"

docker run --rm --network none \
  -v "$OUTPUT_DIR:/input:ro" \
  "$IMAGE" \
  python -m src.gemini_dynamic_initial_entry_replay_q1 \
    --input /input/hydrated.jsonl \
    --evidence-mode STRICT_TRADE_THROUGH \
    --asset ETH --horizon 5m \
    --output /tmp/strict.json --compact | tee "$OUTPUT_DIR/strict.stdout"

python3 - "$OUTPUT_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
hydrate = json.loads((root / "hydrate-summary.json").read_text())
touch = json.loads((root / "touch.stdout").read_text())
strict = json.loads((root / "strict.stdout").read_text())
result = {
    "type": "GEMINI_ETH5_HYDRATED_INITIAL_ENTRY_REPLAY_V1",
    "classification": "SHADOW_REPLAY_NOT_REALIZED",
    "order_entry": False,
    "credentials_loaded": False,
    "realized_pnl": None,
    "hydrate": {k: v for k, v in hydrate.items() if k != "markets"},
    "tape_touch_optimistic": touch,
    "strict_trade_through": strict,
}
print(json.dumps(result, indent=2, sort_keys=True))
PY

echo 'GEMINI_ETH5_HYDRATED_REPLAY_COMPLETE classification=SHADOW_REPLAY_NOT_REALIZED order_entry=false credentials_loaded=false'
