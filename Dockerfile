FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# Settlement agreement research is public/read-only and cannot enter orders.
ENV BOT_MODE=paper

# Use the corrected close_time matcher and only the four 15-minute crypto series.
# A 15-minute cadence is enough for settlement-basis monitoring and avoids needless
# control-plane load while the HOT scanner owns the latency-sensitive path.
CMD ["sh", "-c", "while true; do python -m src.gemini_kalshi_settlement_probe --hours 48 --gemini-limit 2000 --max-expiry-skew-s 30 --hold-seconds 0; sleep 900; done"]
