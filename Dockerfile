FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# Targeted branch gate: core identity + structured Gemini + exact three-venue MLB.
RUN pytest -q \
    tests/test_universal_sports_identity.py \
    tests/test_gemini_sports_adapter.py \
    tests/test_universal_three_venue_sports.py

# Discovery is public/read-only. There are no credentials or order-entry paths.
ENV BOT_MODE=paper
CMD ["python", "-m", "src.universal_three_venue_sports"]
