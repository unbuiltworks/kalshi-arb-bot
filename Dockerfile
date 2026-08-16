FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py

# Continuous read-only rollover prewarm benchmark. It subscribes to deterministic
# next-window identities before each boundary and never imports an order-entry path.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_prewarm_probe", "--assets", "BTC", "ETH", "SOL", "XRP", "--lead-s", "1.0", "--timeout-after-s", "8.0", "--rollovers", "0"]
