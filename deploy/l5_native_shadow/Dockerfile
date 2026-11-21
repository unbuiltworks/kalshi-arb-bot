FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
# stdlib only: this service makes unauthenticated GETs and holds no client
RUN mkdir -p src/polymarket_l5 analysis/l5
COPY src/__init__.py src/__init__.py
COPY src/l5_native_shadow.py src/l5_native_shadow.py
COPY src/l5_barrier_inventory.py src/l5_barrier_inventory.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
CMD ["python3", "-m", "src.l5_native_shadow"]
