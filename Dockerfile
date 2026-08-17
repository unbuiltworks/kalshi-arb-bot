FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on rollover, no-lookahead basis, durable settlement-map, and
# retrospective payout-accounting regressions before the research service can start.
RUN PYTHONPATH=/app python -m pytest -q \
    tests/test_gemini_kalshi_predictive_hot.py \
    tests/test_gemini_kalshi_basis_annotated_shadow.py \
    tests/test_gemini_kalshi_basis_settlement_map.py \
    tests/test_gemini_kalshi_basis_autonomous.py

# Research only. This image has no order-authorization or order-entry surface.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_basis_autonomous", "--assets", "BTC", "ETH", "SOL", "XRP", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--hot-output-dir", "/tmp/crypto-hot-basis", "--data-dir", "/data", "--settlement-refresh-seconds", "300", "--initial-history-hours", "168", "--refresh-history-hours", "48", "--gemini-limit", "5000"]
