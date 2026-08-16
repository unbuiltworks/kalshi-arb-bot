FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on identity, activation/event-order safety, basis/no-lookahead logic,
# whole-episode gates, exact original-limit/depth math, and original-episode binding.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py tests/test_gemini_kalshi_prewarmed_basis_shadow.py tests/test_gemini_kalshi_basis_execution_revalidation.py tests/test_gemini_kalshi_basis_execution_revalidation_safe.py

# Read-only execution research. No preview/order/cancel path exists.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_basis_execution_revalidation_safe", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--prewarm-lead-s", "1.0", "--output-dir", "/tmp/crypto-hot-exact-revalidation"]
