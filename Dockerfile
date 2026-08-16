FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on identity, activation/event-order safety, no-lookahead basis math,
# whole-episode basis survival, and cumulative cohort-accounting regressions.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py tests/test_gemini_kalshi_prewarmed_basis_shadow.py tests/test_gemini_kalshi_prewarmed_basis_summary.py

# Read-only research. Cumulative profit is explicitly apparent quote-lock profit,
# never realized P&L. No order authorization path is added.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_prewarmed_basis_summary", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--prewarm-lead-s", "1.0", "--output-dir", "/tmp/crypto-hot-prewarmed-basis-summary"]
