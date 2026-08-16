FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# One-shot settlement reconciliation only. Waits for Gemini target settlement, then
# queries exactly the four Kalshi 15m crypto series. No order-entry path.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_target_reconcile", "--target", "2026-08-16T21:00:00Z", "--timeout-s", "900", "--poll-s", "10"]
