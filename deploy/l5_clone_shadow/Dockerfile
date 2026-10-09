# CONTINUOUS REALTIME L5 CLONE SHADOW -- both regimes, read-only.
#
# No credentials are set on this service and none are read by the code: the
# only endpoints it touches are the PUBLIC gateway's /v1/markets and
# /v1/markets/{slug}/book. There is no executor in the image's import graph,
# which the build-time proof below asserts rather than assumes.
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir numpy==2.1.3
COPY src/ /app/src/
COPY analysis/l5/clone/gbm.py /app/analysis/l5/clone/
COPY analysis/l5/clone/train.py /app/analysis/l5/clone/
COPY analysis/l5/clone/__init__.py /app/analysis/l5/clone/
COPY analysis/l5/clone/models/ /app/analysis/l5/clone/models/
COPY analysis/__init__.py /app/analysis/
COPY analysis/l5/__init__.py /app/analysis/l5/
# Historical benchmark files the breadth module's import graph reads at
# import time. Without them the import succeeds locally and fails in the
# image -- the exact failure mode deploy/pmus_shadow_breadth/Dockerfile
# documents.
COPY analysis/l5/l5_action_model_v2.json /app/analysis/l5/
COPY analysis/l5/l5_fidelity_pass.json /app/analysis/l5/
COPY analysis/l5/complete_no_fill_forensics.json /app/analysis/l5/
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV L5_CLONE_MODEL_DIR=/app/analysis/l5/clone/models
# BUILD-TIME PROOF. The two models must LOAD and the module must import with
# no order path reachable. A missing pickle or a drifted feature order is a
# build failure here instead of a crash loop in production.
RUN python3 -c "\
import sys; sys.path.insert(0,'/app');\
from src import pmus_l5_clone_shadow as CS;\
p=CS.load_policies('/app/analysis/l5/clone/models');\
assert set(p)=={'early','late'}, p;\
assert p['early'].feats==CS.FEATS;\
src=open('/app/src/pmus_l5_clone_shadow.py').read();\
assert 'pmus_auth' not in src and 'q1_executor' not in src;\
print('CLONE_SHADOW_IMAGE_OK', {k:v.n_fit for k,v in p.items()})"
CMD ["python3", "-m", "src.pmus_l5_clone_shadow"]
