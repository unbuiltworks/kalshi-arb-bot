FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# This branch is intentionally read-only. It cannot start the live/paper trading bot.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kalshi_multi_ws_hot", "--assets", "BTC", "ETH", "SOL", "XRP", "--seconds", "0", "--max-quote-skew-ms", "100", "--capital-limit", "5", "--min-profit", "0.03", "--output-dir", "/tmp/crypto-hot"]
