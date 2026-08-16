FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on deterministic identity, prewarm gating, or event-order regressions.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py

# Temporary 30-minute 1-cent threshold study. Still strictly read-only.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_predictive_prewarmed_safe", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "1800", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.01", "--prewarm-lead-s", "1.0", "--output-dir", "/tmp/crypto-hot-1c"]
