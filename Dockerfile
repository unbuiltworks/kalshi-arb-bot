FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on nested payoff math, aggressive quote validity, and trade-through
# direction/hedge reconstruction before the shadow watcher may start.
RUN PYTHONPATH=/app python -m pytest -q \
    tests/test_kalshi_nested_strike_shadow.py \
    tests/test_kalshi_nested_less_shadow.py \
    tests/test_kalshi_nested_maker_shadow.py \
    tests/test_kalshi_nested_maker_aggressive_shadow.py \
    tests/test_kalshi_nested_trade_through_shadow.py

# Read-only watcher for today's 6 PM ET ETH/SOL daily ladders. No order path exists.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.kalshi_nested_trade_through_shadow", "--seconds", "600", "--series", "KXETHD", "KXSOLD", "--target-profit", "0.03", "--event-suffix", "26AUG1618", "--max-quotes", "30"]
