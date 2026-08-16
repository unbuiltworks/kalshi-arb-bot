FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on deterministic ticker identity or activation-boundary regressions.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_prewarmed_hot.py

# Research-only: next-window sockets may prewarm, but evaluation is hard-gated to
# receive timestamps at/after the new 15-minute boundary. No order authorization.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_prewarmed_hot", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--output-dir", "/tmp/crypto-hot-prewarmed"]
