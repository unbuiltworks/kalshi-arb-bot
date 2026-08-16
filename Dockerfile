FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# Fail closed if historical proxy selection/statistics regress.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kaiko_historical_proxy.py

# One-shot historical prospective-proxy analysis. Read-only; four paced Kalshi
# settlement-series queries plus public Gemini settlement/trade data only.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.gemini_kaiko_historical_proxy --hours 48 --gemini-limit 5000 --pace-s 0.12; sleep 3600"]
