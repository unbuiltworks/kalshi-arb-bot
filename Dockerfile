FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on direct nested payout/depth math, custom-orientation rejection,
# and maker-first/taker-hedge lock calculations.
RUN PYTHONPATH=/app python -m pytest -q \
    tests/test_kalshi_nested_strike_shadow.py \
    tests/test_kalshi_nested_less_shadow.py \
    tests/test_kalshi_nested_maker_shadow.py

# One-shot same-event research only. Public market data; no credentials or order-entry.
# Direct two-taker scan runs first, then maker-join/taker-hedge feasibility.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.kalshi_nested_strike_shadow --series KXBTCD KXETHD KXSOLD --q-max 5; python -m src.kalshi_nested_less_shadow --series KXDOGED KXSHIBAD --q-max 5; python -m src.kalshi_nested_maker_shadow --series KXBTCD KXETHD KXSOLD --target-profit 0.01; sleep 3600"]
