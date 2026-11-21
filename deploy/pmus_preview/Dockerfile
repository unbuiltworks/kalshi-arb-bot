# One-shot bounded PREVIEW session.
#
# This image exists so the 24/7 L5 observer never gains a POST path. It
# carries the preview transport, the shared order serializer, the signing
# module and the runner -- and deliberately NOT l5_pmus_transport,
# pmus_q1_executor or anything else that can submit, cancel, modify or
# close a position. There is no order path in this image to be talked into.
#
# It runs once and exits. It is not a service that stays up.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir cryptography==42.0.5

COPY src/__init__.py src/__init__.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/l5_preview_session.py src/l5_preview_session.py
COPY src/l5_order_body.py src/l5_order_body.py
COPY src/l5_probe_manifest.py src/l5_probe_manifest.py
COPY src/l5_live_execution.py src/l5_live_execution.py
# imported at module level by l5_live_execution (order/position origin)
COPY src/l5_series_registry.py src/l5_series_registry.py
COPY src/l5_origin.py src/l5_origin.py
# the frozen engine, needed only because the probe envelope imports through
# it. Pure decision code: no transport, no endpoint, no credential.
COPY src/l5_faithful_translation.py src/l5_faithful_translation.py
COPY src/l5_execution_translations.py src/l5_execution_translations.py
COPY scripts/__init__.py scripts/__init__.py
COPY scripts/pmus_preview_oneshot.py scripts/pmus_preview_oneshot.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.pmus_preview_oneshot"]
