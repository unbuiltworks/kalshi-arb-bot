FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on deterministic identity, activation/event-order safety, no-lookahead
# basis math, and whole-episode basis-survival regressions.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py tests/test_gemini_kalshi_prewarmed_basis_shadow.py

# Continuous read-only scanner. 2bp/5bp fields are research cohorts only; there is
# no order authorization path and Gemini spot is explicitly not the Kaiko index.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_prewarmed_basis_shadow", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--prewarm-lead-s", "1.0", "--output-dir", "/tmp/crypto-hot-prewarmed-basis"]
