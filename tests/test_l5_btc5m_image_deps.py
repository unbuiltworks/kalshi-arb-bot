"""The BTC5m image installs every third-party module its shipped code imports.

Regression for 2026-11-12 03:45Z: deploy/l5_btc5m_live/Dockerfile copied the
right src files but installed no packages. `cryptography` (request signing)
and `websockets` are imported lazily inside functions, so the existing
import-closure test (top-level imports only) passed while every
authenticated venue call in the container raised ModuleNotFoundError.

This walks EVERY import statement in every shipped file, lazy ones included,
with the AST, and requires each non-stdlib, non-src module to be installed
by a `pip install` line in that Dockerfile.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DF = ROOT / "deploy" / "l5_btc5m_live" / "Dockerfile"
#: import name -> the pip distribution that provides it
DIST = {"cryptography": "cryptography", "websockets": "websockets"}


def shipped():
    return re.findall(r"^COPY (src/[\w/]+\.py) ", DF.read_text(), re.M)


def third_party_imports():
    out = {}
    for rel in shipped():
        tree = ast.parse((ROOT / rel).read_text(), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for n in names:
                top = n.split(".")[0]
                if top in sys.stdlib_module_names or top in ("src", "__future__"):
                    continue
                out.setdefault(top, set()).add(rel)
    return out


def installed():
    got = set()
    for line in re.findall(r"^RUN .*pip install (.+)$", DF.read_text(), re.M):
        for tok in line.split():
            tok = tok.strip('"\'')
            if tok.startswith("-"):
                continue
            got.add(re.split(r"[<>=!~\[]", tok)[0])
    return got


def test_every_third_party_import_in_the_image_is_installed():
    need = third_party_imports()
    assert set(need) >= {"cryptography", "websockets"}     # the 03:45Z pair
    unknown = sorted(set(need) - set(DIST))
    assert not unknown, "map these imports to a pip distribution: %s" % unknown
    missing = sorted(DIST[m] for m in need if DIST[m] not in installed())
    assert not missing, "not installed in the BTC5m image: %s" % missing


def test_the_signing_path_really_needs_cryptography():
    src = (ROOT / "src" / "pmus_auth.py").read_text()
    assert "from cryptography.hazmat.primitives.asymmetric import ed25519" in src


#: functions on the BTC5m live path whose LAZY src imports must be shipped:
#: (file, function name or None for every function in the file)
LIVE_PATH = (("src/l5_live_authorization.py", "authorize_arming"),
             ("src/l5_live_authorization.py", "claim_btc5m_submission"),
             ("src/l5_pmus_transport.py", None),
             ("src/pmus_auth.py", None),
             ("src/l5_btc5m_runner.py", None),
             ("src/l5_btc5m_execution.py", None),
             ("src/l5_btc5m_controller.py", None),
             ("src/l5_campaign_approval.py", None),
             ("src/l5_halt_gate.py", None))


def lazy_src_imports(rel, name):
    tree = ast.parse((ROOT / rel).read_text(), filename=rel)
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if name is not None and fn.name != name:
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.ImportFrom) and n.module == "src":
                out.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and (n.module or "").startswith("src."):
                out.add(n.module[4:])
            elif isinstance(n, ast.Import):
                out.update(a.name[4:] for a in n.names if a.name.startswith("src."))
    return out


def test_lazy_src_imports_on_the_live_path_are_shipped():
    have = set(shipped())
    missing = sorted("%s:%s -> %s" % (rel, name or "*", m)
                     for rel, name in LIVE_PATH
                     for m in lazy_src_imports(rel, name)
                     if "src/%s.py" % m not in have)
    assert not missing, missing


def test_arming_imports_the_mandate_gate_unconditionally():
    # the reason the two modules are shipped: this import is not guarded
    assert "l5_mandate_signature" in lazy_src_imports(
        "src/l5_live_authorization.py", "authorize_arming")
