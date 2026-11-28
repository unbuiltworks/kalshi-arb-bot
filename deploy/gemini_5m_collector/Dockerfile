# Gemini BTC/ETH 5m READ-ONLY collector. Public endpoints only: no
# credentials, no order path, no PMUS module. It subscribes to each 5-minute
# window on the window's own clock (expiry-330s .. expiry+30s), records BBO at
# ~1 Hz and EVERY print unconditionally, and writes the canonical trajectory
# schema beside the raw tape.
#
# WHY IT EXISTS: the previous bounded-session collection produced a tape whose
# median in-window coverage was ZERO SECONDS per 5m market (170 two-sided
# observations each, almost all pre-open placeholder quotes) and 69.3% of
# windows had no usable in-window quote. That is a collection-window defect,
# not a venue one -- measured live at 91.9% in-window after this change.
#
# Nothing here can be armed: the module has no order verb and no key.
FROM python:3.11-slim
WORKDIR /app
COPY src/policy_d_port/__init__.py src/policy_d_port/__init__.py
COPY src/policy_d_port/collector.py src/policy_d_port/collector.py
RUN touch src/__init__.py && pip install --no-cache-dir httpx websockets
ENV PYTHONUNBUFFERED=1
# --hours 0 == continuous. --gemini-only skips the Kalshi tasks: this service
# exists for the Gemini 5m lane and nothing else.
CMD ["python", "-m", "src.policy_d_port.collector", \
     "--gemini-only", "--hours", "0", "--out-dir", "/state/gemini-5m"]
