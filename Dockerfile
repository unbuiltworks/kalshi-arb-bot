FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Preserve the crypto safety suite and validate both Crypto.com read-only research
# collectors. This branch has no authenticated or order-entry path.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py tests/test_gemini_kalshi_prewarmed_basis_shadow.py tests/test_gemini_kalshi_basis_execution_revalidation.py tests/test_gemini_kalshi_basis_execution_revalidation_safe.py tests/test_gemini_kalshi_micro_live_readiness_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp_safe.py tests/test_gemini_kalshi_opportunity_matrix_shadow.py tests/test_cryptocom_prediction_discovery_shadow.py tests/test_cryptocom_dcm_book_probe_shadow.py

ENV BOT_MODE=paper

CMD ["python", "-m", "src.cryptocom_dcm_book_probe_shadow", "--seconds", "0", "--interval-s", "30", "--events-per-asset", "2", "--depth", "10"]
