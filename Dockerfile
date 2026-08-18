FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

ENV PYTHONPATH=/app

# Focused regression gate for the MLB prop identity, schedule, economics,
# participation, fail-closed readiness semantics, and paper-only path used by
# this isolated service.
RUN pytest -q \
    tests/test_strict_mlb_prop_adapters.py \
    tests/test_strict_mlb_prop_snapshot.py \
    tests/test_strict_mlb_prop_snapshot_v2.py \
    tests/test_strict_mlb_prop_snapshot_v5_failclosed.py \
    tests/test_strict_mlb_prop_schedule_guard.py \
    tests/test_mlb_prop_rule_equivalence.py \
    tests/test_mlb_prop_paper_microprobe.py \
    tests/test_mlb_prop_paper_settlement.py

ENV BOT_MODE=paper

CMD ["python", "-m", "src.mlb_prop_paper_supervisor", "--interval-seconds", "30", "--journal-path", "/data/mlb-prop-paper.jsonl"]
