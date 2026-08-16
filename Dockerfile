FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# Fail the image build if predictive identity or discovery backoff regressions break.
# Explicit PYTHONPATH is required because this image is not installed as a package.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_crypto_hot_catalog_backoff.py

# This branch is intentionally read-only. It cannot start the live/paper trading bot.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_predictive_hot", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--output-dir", "/tmp/crypto-hot"]
