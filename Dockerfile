FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on both monotonic payout proofs plus ask/depth/fee math.
RUN PYTHONPATH=/app python -m pytest -q tests/test_kalshi_nested_strike_shadow.py tests/test_kalshi_nested_less_shadow.py

# One-shot same-event nested-strike research only. Public market data; no credentials
# or order-entry path. Container stays observable after both scans complete.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.kalshi_nested_strike_shadow --series KXBTCD KXETHD KXSOLD --q-max 5; python -m src.kalshi_nested_less_shadow --series KXDOGED KXSHIBAD --q-max 5; sleep 3600"]
