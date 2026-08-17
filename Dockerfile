FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

ENV PYTHONPATH=/app

# Preserve the existing MLB prop regression gate and add the isolated game-line
# identity/economics tests.  No order-entry tests or credentials are required.
RUN pytest -q \
    tests/test_strict_mlb_prop_adapters.py \
    tests/test_strict_mlb_prop_snapshot.py \
    tests/test_strict_mlb_prop_snapshot_v2.py \
    tests/test_strict_mlb_prop_schedule_guard.py \
    tests/test_mlb_prop_rule_equivalence.py \
    tests/test_mlb_prop_paper_microprobe.py \
    tests/test_mlb_prop_paper_settlement.py \
    tests/test_strict_mlb_game_adapters.py \
    tests/test_strict_mlb_game_snapshot.py

ENV BOT_MODE=paper

CMD ["python", "-m", "src.mlb_game_paper_supervisor", "--interval-seconds", "15", "--journal-path", "/data/mlb-game-paper.jsonl"]
