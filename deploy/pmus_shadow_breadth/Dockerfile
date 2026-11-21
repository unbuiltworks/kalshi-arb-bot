# Read-only shadow breadth: many virtual L5s over the live sports game
# universe. No credentials, no executor, no order path -- see the module
# docstring and tests/test_sports_shadow_breadth.py.
FROM python:3.11-slim
WORKDIR /app
COPY src/ /app/src/
COPY analysis/l5/l5_action_model_v2.json /app/analysis/l5/
COPY analysis/l5/l5_fidelity_pass.json /app/analysis/l5/
# The historical complement-distance benchmark. Without this file in the
# image, execution_vs_history returned FileNotFoundError in production while
# passing locally -- the comparison the whole reachability question turns on,
# silently unavailable on the only machine that matters.
COPY analysis/l5/complete_no_fill_forensics.json /app/analysis/l5/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "src.pmus_sports_shadow_breadth"]

# deploy trigger: build from feature/l5-transfer-backtest
#
# WATCH PATHS LIVE IN THE RAILWAY SERVICE CONFIG, NOT IN railway.json.
# Config-as-code is deprecated on this project, so adding a path to
# railway.json's watchPatterns does NOT make the service watch it. 9912e9a8
# touched ONLY src/pmus_execution_shadow.py and its tests, and the deployment
# read SKIPPED -- leaving the running collector on the BROKEN touch logic
# while the fix sat in the branch. Until the service config is updated, a
# change confined to a module outside its real watch set must be paired with
# a touch to a path it does watch (this file is under
# deploy/pmus_shadow_breadth/**, which is watched).
