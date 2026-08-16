FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on direct nested payout/depth math, maker/taker lock math, and each
# market's actual cent/subpenny price-level schedule.
RUN PYTHONPATH=/app python -m pytest -q \
    tests/test_kalshi_nested_strike_shadow.py \
    tests/test_kalshi_nested_less_shadow.py \
    tests/test_kalshi_nested_maker_shadow.py \
    tests/test_kalshi_nested_maker_aggressive_shadow.py

# One-shot same-event research only. Public market data; no credentials or order-entry.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.kalshi_nested_strike_shadow --series KXBTCD KXETHD KXSOLD --q-max 5; python -m src.kalshi_nested_maker_aggressive_shadow --series KXBTCD KXETHD KXSOLD --target-profit 0.01 0.03 --top-n 20; sleep 3600"]
