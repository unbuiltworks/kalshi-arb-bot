FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed on identity, activation/event-order safety, basis/no-lookahead logic,
# whole-episode gates, exact original-limit/depth math, original-episode binding,
# conservative 20 bp double-boundary readiness, the strict disarmed executor, and
# the read-only multi-asset opportunity matrix.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py tests/test_gemini_kalshi_prewarmed_basis_shadow.py tests/test_gemini_kalshi_basis_execution_revalidation.py tests/test_gemini_kalshi_basis_execution_revalidation_safe.py tests/test_gemini_kalshi_micro_live_readiness_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp_safe.py tests/test_gemini_kalshi_opportunity_matrix_shadow.py

# This branch is research-only. It contains no live-order authorization path in its
# default process and runs all four supported 15-minute crypto assets at a 2c floor.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_opportunity_matrix_shadow", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "50", "--capital-limit", "1", "--min-profit", "0.02", "--prewarm-lead-s", "1.0", "--output-dir", "/tmp/crypto-opportunity-matrix"]
