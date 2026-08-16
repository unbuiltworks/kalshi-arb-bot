FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on guaranteed-payout, ask reconstruction, depth, or fee math regressions.
RUN PYTHONPATH=/app python -m pytest -q tests/test_kalshi_nested_strike_shadow.py

# One-shot same-event nested-strike research only. Public market data; no credentials
# or order-entry path. Container stays observable after the scan completes.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.kalshi_nested_strike_shadow --series KXBTCD KXETHD KXSOLD KXDOGED KXSHIBAD --q-max 5; sleep 3600"]
