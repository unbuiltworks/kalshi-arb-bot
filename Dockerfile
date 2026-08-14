FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# This branch is intentionally read-only. It cannot start the live/paper trading bot.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.execution_shadow_revalidation_fixed", "--bankroll", "50", "--initial-max-age-ms", "250", "--recheck-max-age-ms", "1500", "--strict-fresh-ms", "250", "--delays-ms", "50,100,250,500"]
