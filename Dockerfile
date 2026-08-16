FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY tests/ tests/

# Fail closed if proxy averaging math regresses before the research container starts.
RUN PYTHONPATH=/app python -m pytest -q tests/test_gemini_kaiko_proxy_calibration.py

# Continuous Gemini-only calibration research with paced strike discovery.
ENV BOT_MODE=paper

CMD ["python", "-m", "src.gemini_kaiko_proxy_calibration_paced"]
