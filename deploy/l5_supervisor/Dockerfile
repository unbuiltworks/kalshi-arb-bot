# Always-on L5 transfer supervisor image (issue #167).
# SHADOW/DISARMED by default: with no extra env this container runs the
# mutation-free shadow loop forever. LIVE requires BOTH
# GEMINI_L5_SUPERVISOR_MODE=live AND the operator-held dual-key arming
# triple — none of which is baked in here.
#
# Build (repo root of the pinned probe branch):
#   docker build -f deploy/l5_supervisor/Dockerfile -t l5-supervisor:<sha> .
# Shadow run (persistent state on the host):
#   docker run -d --restart unless-stopped --name l5-supervisor \
#     -e GEMINI_L5_V1_POLICY=1 -e GEMINI_L5_FORCED_COMPLETION=1 \
#     -v $HOME/l5probe/supervisor:/state/l5-supervisor \
#     l5-supervisor:<sha>
# Status:
#   docker exec l5-supervisor python3 -m src.gemini_l5_transfer_supervisor status

FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/

CMD ["python3", "-m", "src.gemini_l5_transfer_supervisor", "run"]
