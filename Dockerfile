FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on nested payoff math, aggressive quote validity, trade-through
# direction, nearest-expiry selection, and dynamic cancel/reprice economics.
RUN PYTHONPATH=/app python -m pytest -q \
    tests/test_kalshi_nested_strike_shadow.py \
    tests/test_kalshi_nested_less_shadow.py \
    tests/test_kalshi_nested_maker_shadow.py \
    tests/test_kalshi_nested_maker_aggressive_shadow.py \
    tests/test_kalshi_nested_trade_through_shadow.py \
    tests/test_kalshi_nested_rolling_shadow.py

# Two-hour read-only research window. Candidate universe refreshes every 5 minutes;
# no order-manager / order-entry path is imported.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.kalshi_nested_rolling_shadow --seconds 7200 --refresh-seconds 300 --series KXETHD KXSOLD --target-profit 0.03 --max-candidates 40; sleep 3600"]
