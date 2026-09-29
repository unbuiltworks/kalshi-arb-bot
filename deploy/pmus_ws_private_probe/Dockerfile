# READ-ONLY WS capability probe. Establishes whether PMUS streams private
# order/fill lifecycle events. The image contains no money path, no transport,
# no mandate and nothing POST-capable; the probe may send only subscribe and
# unsubscribe frames, enforced at send time and by test.
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY scripts/pmus_ws_private_probe.py scripts/pmus_ws_private_probe.py
RUN touch src/__init__.py scripts/__init__.py
ENV PYTHONUNBUFFERED=1
CMD ["python3", "scripts/pmus_ws_private_probe.py"]
