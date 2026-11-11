FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY scripts/pmus_ws_book_diag.py scripts/pmus_ws_book_diag.py
RUN touch src/__init__.py
ENV PYTHONUNBUFFERED=1
CMD ["python3", "scripts/pmus_ws_book_diag.py"]
