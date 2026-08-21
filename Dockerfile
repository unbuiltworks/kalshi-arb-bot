FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY pyproject.toml .

# This feature branch is intentionally weather-shadow-only. The image build
# fails if the read-only safety/normalization/backtest/readiness tests fail.
RUN pytest -q tests/test_weather_shadow.py tests/test_weather_backtest.py tests/test_weather_shadow_daemon_readiness.py

ENV BOT_MODE=paper
ENV WEATHER_SHADOW_INTERVAL_S=60

CMD ["python", "-u", "-m", "src.weather_shadow_daemon"]
