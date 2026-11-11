"""OWNER 5556354536: no Railway service in this repo may fall through to
the generic ``src.__main__`` / order-capable Kalshi startup."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _manifests():
    for d in sorted(p for p in DEPLOY.iterdir() if p.is_dir()):
        if (d / "Dockerfile").exists():
            yield d


def test_root_railway_config_declares_no_start_command():
    """The repo-wide config must never define a deploy for every service
    (that is how a config-less service ran the Kalshi bot)."""
    root = json.loads((ROOT / "railway.json").read_text())
    assert "deploy" not in root and "build" not in root


def test_every_deploy_dir_has_an_explicit_non_bot_manifest():
    for d in _manifests():
        manifest = d / "railway.json"
        assert manifest.exists(), f"{d.name}: no railway.json (fall-through risk)"
        m = json.loads(manifest.read_text())
        start = m["deploy"]["startCommand"]
        assert "-m src " not in start + " " and not start.endswith("-m src")
        assert "src.main" not in start and "src.__main__" not in start
        assert m["build"]["dockerfilePath"] == f"deploy/{d.name}/Dockerfile"
        cmd = [l for l in (d / "Dockerfile").read_text().splitlines()
               if l.startswith("CMD ")]
        assert cmd and "src.main" not in cmd[0] and '"src"' not in cmd[0]


def test_book_diag_manifest_is_disabled_and_read_only():
    m = json.loads((DEPLOY / "pmus_ws_book_diag" / "railway.json").read_text())
    assert m["deploy"]["startCommand"] == "python3 scripts/pmus_ws_book_diag.py"
    assert m["deploy"]["restartPolicyType"] == "NEVER"
    pats = m["build"]["watchPatterns"]
    assert pats == ["deploy/pmus_ws_book_diag/ENABLE_REDEPLOY_BY_OWNER_ONLY/**"]
    assert not (DEPLOY / "pmus_ws_book_diag" / "ENABLE_REDEPLOY_BY_OWNER_ONLY").exists()
    assert "OBSOLETE" in m["_status"]


def test_generic_entrypoint_refuses_under_a_managed_runtime():
    from src import __main__ as entry
    assert entry.refuse_reason({}) is None                      # local run
    why = entry.refuse_reason({"RAILWAY_SERVICE_NAME": "x"})
    assert why and "never starts the order-capable Kalshi bot" in why
    assert entry.refuse_reason({"RAILWAY_SERVICE_NAME": "x",
                                "KALSHI_ARB_BOT_SERVICE": "1"}) is None
    # end to end: python -m src under a fake Railway env exits 0 without
    # touching the Kalshi client / private key
    out = subprocess.run([sys.executable, "-m", "src"], cwd=ROOT,
                         env={"PATH": "/usr/bin:/bin",
                              "RAILWAY_SERVICE_NAME": "pmus-ws-book-diagnostic"},
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    assert "KALSHI_BOT_REFUSED" in out.stdout
    assert "private_key" not in out.stderr


def test_src_main_also_refuses_under_a_managed_runtime():
    """LIVE 2026-09-14T13:42Z: a Railway service created with no config was
    auto-built and ran src/main.py directly -- run_bot() -> KalshiClient() ->
    FileNotFoundError: private_key.pem. It did not trade only because no key
    was present, and a missing key is not a safety control. The refusal that
    guarded `python -m src` must guard this entrypoint too."""
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env["RAILWAY_SERVICE_NAME"] = "x"
    env.pop("KALSHI_ARB_BOT_SERVICE", None)
    out = subprocess.run([sys.executable, "-c",
                          "import src.main as m; m.main()"],
                         cwd=ROOT, env=env, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "KALSHI_BOT_REFUSED" in out.stdout
    assert "KalshiClient" not in out.stderr


def test_src_main_still_runs_when_explicitly_opted_in():
    """The refusal must be escapable exactly the way __main__'s is, or a
    legitimate bot service could no longer start."""
    import inspect
    from src import main as m
    src = inspect.getsource(m.main)
    assert "refuse_reason" in src and "KALSHI_BOT_REFUSED" in src


def test_preview_manifest_is_one_shot_and_owner_gated():
    """A preview session must never loop and never redeploy itself: it is a
    deliberate, bounded act. Redeploy is gated on a directory only the owner
    creates, exactly as the book-diag one-shot is."""
    d = DEPLOY / "pmus_preview"
    m = json.loads((d / "railway.json").read_text())
    assert m["deploy"]["startCommand"] == "python3 -m scripts.pmus_preview_oneshot"
    assert m["deploy"]["restartPolicyType"] == "NEVER"
    assert m["build"]["watchPatterns"] == [
        "deploy/pmus_preview/ENABLE_RUN_BY_OWNER_ONLY/**"]
    assert not (d / "ENABLE_RUN_BY_OWNER_ONLY").exists()


def test_the_preview_image_carries_no_order_capable_module():
    import re
    df = (DEPLOY / "pmus_preview" / "Dockerfile").read_text()
    copied = re.findall(r"^COPY (\S+)", df, re.M)   # comments explain the
    for banned in ("l5_pmus_transport", "pmus_q1_executor",   # exclusions,
                   "pmus_micro_executor", "pmus_migration_close"):
        assert not any(banned in c for c in copied), \
            "%s must not be COPY'd into the preview image" % banned


def test_the_observer_image_can_actually_import_what_its_preflight_needs():
    """A near-miss worth pinning: the corrected outcome gate reads the
    published contract, and that data briefly lived in the module carrying
    the POST-capable preview transport. Shipping it would have meant either
    an ImportError in production or a POST path in the GET-only image."""
    import ast
    import re
    from pathlib import Path
    df = (DEPLOY / "pmus_ws_recorder" / "Dockerfile").read_text()
    copied = set(re.findall(r"^COPY (\S+)", df, re.M))

    # every src module the preflight and probe import must be in the image
    for entry in ("scripts/pmus_auth_preflight_readonly.py",
                  "scripts/l5_pmus_realtime_probe.py",
                  "src/l5_pmus_transport.py"):
        assert entry in copied, "%s not in the observer image" % entry
        tree = ast.parse(Path(entry).read_text())
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("src"):
                mods = ([a.name for a in n.names] if n.module == "src"
                        else [n.module.split(".")[-1]])
            for m in mods:
                f = "src/%s.py" % m
                if Path(f).exists():
                    assert f in copied, \
                        "%s imports %s, which is not in the observer image" % (entry, f)


def test_the_observer_image_still_has_no_post_capable_module():
    import re
    df = (DEPLOY / "pmus_ws_recorder" / "Dockerfile").read_text()
    copied = re.findall(r"^COPY (\S+)", df, re.M)
    for banned in ("l5_preview_session", "pmus_q1_executor",
                   "pmus_micro_executor", "pmus_migration_close"):
        assert not any(banned in c for c in copied), \
            "%s can POST and must not be in the observer image" % banned
