FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY main.py .

# Fail closed if historical proxy selection/statistics regress before launch.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kaiko_historical_proxy.py

# One-shot historical prospective-proxy analysis. The scoped launcher queries only
# KXBTC15M/KXETH15M/KXSOL15M/KXXRP15M on Kalshi, then public Gemini data.
ENV BOT_MODE=paper

CMD ["sh", "-c", "python -m src.gemini_kaiko_historical_proxy_scoped --hours 48 --gemini-limit 5000 --pace-s 0.12; sleep 3600"]
