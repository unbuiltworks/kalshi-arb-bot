FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# Branch gate: canonical identity, structured Gemini normalization, exact three-venue
# matching, hard Polymarket scope guards, Kalshi nested-book pricing, discrete
# player-prop normalization, sportsbook identity alignment, the event-driven
# game-line hot layer, and read-only Pushover sports-alert qualification/dedupe.
RUN pytest -q \
    tests/test_universal_sports_identity.py \
    tests/test_gemini_sports_adapter.py \
    tests/test_universal_three_venue_sports.py \
    tests/test_polymarket_scope_guard.py \
    tests/test_three_venue_mlb_snapshot_pricing.py \
    tests/test_three_venue_mlb_prop_safe_scan.py \
    tests/test_three_venue_mlb_hot.py \
    tests/test_the_odds_api_mlb_shadow_identity.py \
    tests/test_sports_pushover_alerts.py \
 && python -m py_compile \
    src/eventing_books.py \
    src/three_venue_mlb_snapshot_pricing.py \
    src/three_venue_mlb_safe_scan.py \
    src/three_venue_mlb_supervisor.py \
    src/three_venue_mlb_prop_safe_scan.py \
    src/three_venue_mlb_prop_supervisor.py \
    src/three_venue_mlb_hot.py \
    src/the_odds_api_mlb_shadow.py \
    src/sports_pushover_alerts.py \
    src/sports_pushover_odds_api_mlb.py

# Default remains the public/read-only REST scanner. Railway overrides this per
# isolated service for prop and WebSocket hot shards.
ENV BOT_MODE=paper
CMD ["python", "-m", "src.three_venue_mlb_supervisor", "--interval-seconds", "5", "--journal-path", "/data/three-venue-mlb.jsonl"]