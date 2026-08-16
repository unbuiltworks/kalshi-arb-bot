FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on deterministic rollover or no-lookahead basis regressions.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_basis_annotated_shadow.py

# Research-only: quote locks are annotated with a Gemini-spot proxy to Kaiko strike.
# This image contains no authorization path for live orders.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_basis_annotated_shadow", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--output-dir", "/tmp/crypto-hot-basis"]
