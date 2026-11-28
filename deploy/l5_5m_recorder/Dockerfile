# L5 BTC5m/ETH5m market-state recorder. READ ONLY, PUBLIC ENDPOINTS ONLY.
#
# WHY IT EXISTS: the BTC15m switch signal is usable (OOS AUC 0.674, n=446) and
# the 5m cohorts are not -- 1 and 4 OOS switches against a pre-fixed >=40-row
# threshold. Any 5m switch rule fitted today would be BTC15m behaviour wearing
# a 5m label. This records the states that let 5m answer on its own evidence.
#
# It captures T-120s through settlement at 1 Hz INCLUDING every state where L5
# does nothing: those are the CONTINUE examples, and a recorder that only wakes
# on a fill produces a dataset with no negatives in it.
#
# NOTHING HERE CAN BE ARMED. The image copies only the recorder package: no
# execution module, no credential, no signing path, no order verb. The recorder
# is stdlib-only and the runner imports nothing but stdlib and the recorder --
# asserted structurally by tests/test_l5_5m_coverage.py over the AST, so a
# future edit that adds an order call or an env-var credential read fails CI.
#
# The Gemini late-subscribe defect cannot recur here: 5m windows sit on fixed
# 300s epochs, so they are ENUMERATED from the clock rather than discovered,
# the roll runs every loop iteration rather than on a REST tick, and
# first_sample_lead_s is reported per window so a regression is visible.
FROM python:3.11-slim
WORKDIR /app
COPY src/l5_5m_coverage/__init__.py src/l5_5m_coverage/__init__.py
COPY src/l5_5m_coverage/recorder.py src/l5_5m_coverage/recorder.py
COPY src/l5_5m_coverage/run.py src/l5_5m_coverage/run.py
RUN touch src/__init__.py
ENV PYTHONUNBUFFERED=1
# --hours 0 == continuous. Samples are appended to /state as they are taken,
# so a restart loses at most the current second rather than the window.
CMD ["python", "-m", "src.l5_5m_coverage.run", "--hours", "0", \
     "--out-dir", "/state/l5-5m"]
