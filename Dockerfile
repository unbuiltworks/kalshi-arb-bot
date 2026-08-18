FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# BOT_MODE controls paper vs live: set in Railway env vars
# Default to paper for safety
ENV BOT_MODE=paper

CMD ["sh", "-c", "if [ \"$BOT_MODE\" = \"live\" ]; then python -m src; else python -m src --paper; fi"]
