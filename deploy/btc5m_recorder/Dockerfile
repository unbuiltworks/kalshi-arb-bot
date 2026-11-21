# READ-ONLY recorder of public BTC 5-minute venue quotes.
#
# It carries ONE file. It has no credentials, imports nothing from src/,
# places no order and reads no account. It is a separate service from
# l5-pmus-live and shares no state, volume or variable with it.
FROM python:3.12-slim
WORKDIR /app
COPY analysis/l5/btc5m_public_recorder.py btc5m_public_recorder.py
ENV PYTHONUNBUFFERED=1
CMD ["python3", "btc5m_public_recorder.py", "-", "0"]
