FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# One-shot public Gemini metadata diagnostic only; no Kalshi requests, credentials,
# or order-entry path.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.gemini_active_metadata; sleep 3600"]
