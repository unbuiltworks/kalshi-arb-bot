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
# conservative 20 bp readiness/executor behavior, the isolated SOL 10bp/50ms
# canary override, and the persistent/idempotent v2 one-shot state machine.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kalshi_predictive_hot.py tests/test_gemini_kalshi_predictive_prewarmed.py tests/test_gemini_kalshi_prewarmed_basis_shadow.py tests/test_gemini_kalshi_basis_execution_revalidation.py tests/test_gemini_kalshi_basis_execution_revalidation_safe.py tests/test_gemini_kalshi_micro_live_readiness_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp.py tests/test_gemini_kalshi_micro_live_executor_20bp_safe.py tests/test_gemini_kalshi_sol_live_canary_10bp_50ms.py tests/test_gemini_kalshi_sol_live_canary_v2.py

# Keep the image default disarmed on the proven v1 scanner until v2 has a Railway
# persistent volume and is explicitly selected as the service start command.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_sol_live_canary_10bp_50ms", "--assets", "SOL", "--seconds", "0", "--max-quote-skew-ms", "50", "--capital-limit", "1", "--min-profit", "0.03", "--prewarm-lead-s", "1.0", "--output-dir", "/tmp/sol-live-canary"]
