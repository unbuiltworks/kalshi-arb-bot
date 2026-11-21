# Clean-boundary transition supervisor. Watches the live campaign for a flat,
# settled, unambiguous boundary; runs the seven-check preflight; writes ONE
# durable activation record. Reads only -- it cancels, completes, settles and
# reprices nothing, and holds no order path.
FROM python:3.11-slim
WORKDIR /app
COPY src/ /app/src/
COPY analysis/l5/ /app/analysis/l5/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "src.pmus_boundary_transition"]
