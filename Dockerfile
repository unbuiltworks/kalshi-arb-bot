FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/

ENV PYTHONUNBUFFERED=1

# Isolated read-only public-wallet replay. No credentials or order-entry code.
CMD ["python", "-u", "-m", "src.polymarket_wallet_replay", "--start", "1787873400", "--end", "1787875200", "--max-rows", "5000"]
