FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# Branch gate: canonical identity, structured Gemini normalization, exact three-venue
# matching, the hard Polymarket full-game scope guard, and Kalshi nested-book pricing.
# Compile the actual runtime modules so a broken supervisor cannot deploy successfully.
RUN pytest -q \
    tests/test_universal_sports_identity.py \
    tests/test_gemini_sports_adapter.py \
    tests/test_universal_three_venue_sports.py \
    tests/test_polymarket_scope_guard.py \
    tests/test_three_venue_mlb_snapshot_pricing.py \
 && python -m py_compile \
    src/three_venue_mlb_snapshot_pricing.py \
    src/three_venue_mlb_safe_scan.py \
    src/three_venue_mlb_supervisor.py

# Public/read-only continuous scanner. No credentials or order-entry paths.
ENV BOT_MODE=paper
CMD ["python", "-m", "src.three_venue_mlb_supervisor", "--interval-seconds", "5", "--journal-path", "/data/three-venue-mlb.jsonl"]
