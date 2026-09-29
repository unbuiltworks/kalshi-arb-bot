# NO-POST shadow and signature extraction. This image deliberately does NOT
# copy the money path, the mandate, the authorization ledger or the transport:
# nothing it contains can construct an order.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app

COPY src/__init__.py src/__init__.py
COPY src/l5_persistence_signature.py src/l5_persistence_signature.py
COPY src/l5_session_journal.py src/l5_session_journal.py
COPY src/pmus_l5_native_engine.py src/pmus_l5_native_engine.py
COPY src/pmus_native_parity.py src/pmus_native_parity.py
COPY src/pmus_completion_shadow.py src/pmus_completion_shadow.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/l5_window_actions.py src/l5_window_actions.py
COPY src/l5_live_book_feed.py src/l5_live_book_feed.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/pmus_auth.py src/pmus_auth.py

COPY scripts/__init__.py scripts/__init__.py
COPY scripts/r5_signature_extract.py scripts/r5_signature_extract.py
COPY scripts/l5_r7_shadow_runner.py scripts/l5_r7_shadow_runner.py

CMD ["python3", "-m", "scripts.r5_signature_extract"]
