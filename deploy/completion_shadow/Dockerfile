FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
# stdlib only. This image deliberately contains NO order transport and NO
# credentials: the modules copied in issue unauthenticated GETs against the
# public gateway and nothing else. The executor, auth and session modules are
# not copied, so this container physically cannot place an order.
RUN mkdir -p src analysis/l5
COPY src/__init__.py src/__init__.py
COPY src/pmus_completion_shadow.py src/pmus_completion_shadow.py
COPY src/pmus_persistence_window.py src/pmus_persistence_window.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
CMD ["python3", "-m", "src.pmus_completion_shadow"]
