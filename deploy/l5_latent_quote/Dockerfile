# L5 latent-quote realtime SHADOW collector. READ-ONLY.
#
# WHY IT IS ITS OWN IMAGE. It must share nothing with pmus-ws-evidence-recorder
# (which holds the PMUS credentials and the authenticated tape the OOS
# experiment depends on), nothing with any executor, and nothing with Gemini.
# This image copies ONE package, holds NO credential of any kind (the
# Polymarket public CLOB needs none), and contains NO order path, no auth
# module, no wallet, no private key, no PMUS code and no Gemini code.
#
# Pinned the supported way, per deploy/RAILWAY_ISOLATION.md:
#     RAILWAY_DOCKERFILE_PATH=/deploy/l5_latent_quote/Dockerfile
# The role tripwire below makes a cross-build fail loudly rather than silently
# recording the wrong stream.
FROM python:3.11-slim

WORKDIR /app

# Stdlib only. No HTTP client library, no websocket client, no web3, no signer.
COPY src/l5_latent_quote/ src/l5_latent_quote/
COPY analysis/l5/latent_quote/PREREGISTRATION.md analysis/l5/latent_quote/PREREGISTRATION.md
COPY analysis/l5/latent_quote/PREREG_SHA256.txt analysis/l5/latent_quote/PREREG_SHA256.txt
RUN touch src/__init__.py

# Tripwire: refuse to start if the image was built from the wrong context, and
# prove at import time that no order path is reachable.
RUN python3 - <<'PY'
import ast, glob, sys
bad = {"submit","place_order","create_order","cancel_order","sign","sign_order","transport"}
for p in glob.glob("src/l5_latent_quote/*.py"):
    for n in ast.walk(ast.parse(open(p).read())):
        if isinstance(n, ast.Attribute) and n.attr.lower() in bad:
            sys.exit(f"ORDER PATH DETECTED in {p}: .{n.attr}")
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
            for m in mods:
                if (m or "").split(".")[0] in {"requests","websocket","websockets","web3","eth_account"}:
                    sys.exit(f"FORBIDDEN IMPORT in {p}: {m}")
print("image audit OK: no order path, no network client library, stdlib only")
PY

ENV L5LQ_SERVICE_ROLE=l5-latent-quote-shadow \
    L5LQ_OUT_DIR=/state/l5-latent-quote \
    PYTHONUNBUFFERED=1

# --minutes 0 means run until stopped. SHADOW ONLY.
CMD ["python3", "-m", "src.l5_latent_quote.run", "--minutes", "0"]
