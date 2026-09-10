# Read-only shadow breadth: many virtual L5s over the live sports game
# universe. No credentials, no executor, no order path -- see the module
# docstring and tests/test_sports_shadow_breadth.py.
FROM python:3.11-slim
WORKDIR /app
COPY src/ /app/src/
COPY analysis/l5/l5_action_model_v2.json /app/analysis/l5/
COPY analysis/l5/l5_fidelity_pass.json /app/analysis/l5/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "src.pmus_sports_shadow_breadth"]
