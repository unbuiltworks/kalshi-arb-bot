FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/
COPY pyproject.toml .

# Live-probe image must prove both the weather normalization/backtest safety
# invariants and the one-shot executor invariants before it can be deployed.
RUN pytest -q \
    tests/test_weather_shadow.py \
    tests/test_weather_backtest.py \
    tests/test_weather_live_probe.py

# Safe defaults. Railway overrides the command for the isolated probe service;
# live order entry still additionally requires explicit environment arming.
ENV BOT_MODE=paper
ENV WEATHER_LIVE_DRY_RUN=1
CMD ["python", "-u", "-m", "src.weather_live_probe"]
