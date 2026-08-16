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
# Analyze whether disagreement disappears once final settlement is sufficiently far
# from strike; 15-minute cadence avoids needless control-plane load.
CMD ["sh", "-c", "while true; do python -m src.gemini_kalshi_settlement_margin_analysis --hours 48 --gemini-limit 2000 --max-expiry-skew-s 30; sleep 900; done"]
