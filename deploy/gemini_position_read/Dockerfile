# ONE-SHOT, READ-ONLY. It answers one question and exits.
#
# WHAT IT IS FOR. On 2026-09-18 the account filled BOTH outcomes of
# GEMI-BTC05M2609181730-UP, the YES leg placed 6.4s after the NO leg had
# already filled and accepted by the venue. What the venue then HELD has never
# been observed: /v1/prediction-markets/positions has answered 200 with zero
# rows in every capture we have, and no position read was taken between the
# second fill and expiry. POST /v1/prediction-markets/positions/settled is the
# endpoint that answers it and the one endpoint in this area the repository has
# never called.
#
# WHY A SEPARATE SERVICE. gemini-l5-faithful is a frozen prospective
# experiment; nothing may be added to its image, its command or its restart
# behaviour. This runs beside it, owns no volume, shares no state, and is
# deleted once it has answered.
#
# IT CANNOT PLACE AN ORDER. The image carries the probe and the auth module and
# nothing else -- no src/gemini_execution, so no transport, no Driver and no
# order path exists in the container at all. The repo-wide write-path isolation
# guard covers the probe module itself and is not narrowed for it: the probe
# does not even read order history, because that path has the placement path as
# a prefix and the guard matches on substring.
#
# restartPolicyType is NEVER. A read that failed should be looked at, not
# retried in a loop against the venue.
FROM python:3.11-slim
WORKDIR /app
COPY src/__init__.py src/__init__.py
COPY src/gemini_prediction_auth.py src/gemini_prediction_auth.py
COPY src/gemini_position_semantics_probe.py src/gemini_position_semantics_probe.py
RUN touch src/__init__.py && pip install --no-cache-dir httpx websockets python-dotenv
ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "src.gemini_position_semantics_probe"]
