FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on identity, prewarm gating, event order, startup quarantine,
# delayed-entry telemetry, or both-book ready-gate regressions.
RUN PYTHONPATH=/app python -m pytest -q \
    tests/test_gemini_kalshi_predictive_hot.py \
    tests/test_gemini_kalshi_predictive_prewarmed.py \
    tests/test_gemini_kalshi_predictive_prewarmed_quarantine.py \
    tests/test_gemini_kalshi_hot_persistence_probe.py \
    tests/test_gemini_kalshi_hot_ready_gate.py

# Continuous read-only execution-quality shadow; no order-entry path.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_hot_ready_gate", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--prewarm-lead-s", "1.0", "--output-dir", "/tmp/crypto-hot"]
