FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY pyproject.toml .

# Dedicated read-only calibration worker. This branch intentionally differs
# from main so Railway source provenance is visible in the Docker build steps.
RUN pytest -q tests/test_weather_shadow.py tests/test_weather_backtest.py

ENV BOT_MODE=paper
ENV WEATHER_BACKTEST_WORKER=1

CMD ["python", "-u", "-m", "src.weather_backtest", "--series", "KXHIGHCHI", "--days", "14"]
