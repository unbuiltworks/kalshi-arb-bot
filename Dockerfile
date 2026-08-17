FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on nested payoff math, aggressive quote validity, trade-through
# direction, nearest-expiry selection, dynamic cancel/reprice economics, the
# one-lot live-probe envelope, V2 transport parsing, candidate orientation,
# strict <=2c maker improvement, <=1c seed GO quality, bounded-risk seed-first
# execution, ambiguous/rejected submission handling, one-shot auto latch, and
# the continuous paper supervisor/risk-governor safety surface.
RUN PYTHONPATH=/app python -m pytest -q \
    tests/test_kalshi_nested_strike_shadow.py \
    tests/test_kalshi_nested_less_shadow.py \
    tests/test_kalshi_nested_maker_shadow.py \
    tests/test_kalshi_nested_maker_aggressive_shadow.py \
    tests/test_kalshi_nested_trade_through_shadow.py \
    tests/test_kalshi_nested_rolling_shadow.py \
    tests/test_kalshi_nested_live_probe_guard.py \
    tests/test_kalshi_nested_probe_transport.py \
    tests/test_kalshi_nested_live_candidate.py \
    tests/test_kalshi_nested_live_candidate_gate.py \
    tests/test_kalshi_nested_probe_quality_watch.py \
    tests/test_kalshi_nested_seed_first_probe.py \
    tests/test_kalshi_nested_seed_first_failures.py \
    tests/test_kalshi_nested_auto_once.py \
    tests/test_kalshi_nested_auto_governor.py \
    tests/test_kalshi_nested_auto_supervisor.py

ENV BOT_MODE=paper

# Default image behavior remains read-only. Railway services override this
# command explicitly; this fallback never exposes an order-entry path.
CMD ["sh", "-c", "python -m src.kalshi_nested_rolling_shadow --seconds 7200 --refresh-seconds 300 --series KXETHD KXSOLD --target-profit 0.03 --max-candidates 40; sleep 3600"]
