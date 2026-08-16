FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# One-shot public metadata diagnostic only; no credentials or order-entry path.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.gemini_kalshi_fast_overlap --max-expiry-skew-s 30; sleep 3600"]
