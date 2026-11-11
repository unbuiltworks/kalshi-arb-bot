# L5_FIDELITY 24/7 prospective runner. READ ONLY, PUBLIC ENDPOINTS ONLY.
#
# It freezes a prediction about L5's NEXT action before that action exists,
# seals it with a content hash, and appends the outcome as a separate row once
# the feed has demonstrably propagated past the prediction window. Predictions
# are never rewritten; a model change gets a new version and scores only
# forward from there.
#
# ESTIMAND A ONLY (fill-anchored). Arbitrary-state WAIT/ACT is NOT served: the
# activity feed's newest row is a median 28.5s old, so a wall-clock freeze
# cannot observe its own prediction window. Reported NOT READY rather than
# served badly.
#
# NOTHING HERE CAN BE ARMED: no credential is read, no order verb exists, and
# the image copies no execution module. Asserted structurally over the AST by
# tests/test_l5_fidelity.py, so a future edit adding one fails CI.
FROM python:3.11-slim
WORKDIR /app
COPY src/__init__.py src/__init__.py
COPY src/l5_fidelity/ src/l5_fidelity/
COPY artifacts/l5_fidelity/ artifacts/l5_fidelity/
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.l5_fidelity.service", "--hours", "0", \
     "--out-dir", "/state/l5-fidelity"]
