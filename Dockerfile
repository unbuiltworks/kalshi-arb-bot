FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Preserve the crypto safety/matrix tests and validate the continuous settlement ledger.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py tests/test_gemini_kalshi_prewarmed_basis_shadow.py tests/test_gemini_kalshi_basis_execution_revalidation.py tests/test_gemini_kalshi_basis_execution_revalidation_safe.py tests/test_gemini_kalshi_micro_live_readiness_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp_safe.py tests/test_gemini_kalshi_opportunity_matrix_shadow.py tests/test_gemini_kalshi_settlement_ledger_shadow.py

ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_settlement_ledger_shadow", "--seconds", "0", "--interval-s", "120", "--hours", "48", "--gemini-limit", "2500", "--max-expiry-skew-s", "30"]
