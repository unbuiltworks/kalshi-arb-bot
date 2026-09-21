"""The l5-live-habitat image's safety rests on WHAT IS COPIED INTO IT.

Its Dockerfile states the property plainly: no executor, session, signer,
credential reader or order transport can enter the image, because none is
copied in. Adding the prospective recorder widened the copy list by one file,
so the property now needs a test rather than an assertion in a comment.

These fail if either module grows a non-stdlib import, an intra-repo import, or
any order/credential surface -- and if the Dockerfile stops copying exactly the
modules that were audited.
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DOCKERFILE = os.path.join(ROOT, "deploy", "l5_live_habitat", "Dockerfile")

# The arming and session variables that must never be readable from this image.
BANNED_ENV = {"PMUS_SPRING_LIVE", "PMUS_SPRING_ARM", "PMUS_SPRING_SESSION_ID",
              "PMUS_SPRING_GATE_EVIDENCE", "POLY_PRIVATE_KEY", "PK",
              "CLOB_API_SECRET", "CLOB_PASSPHRASE"}
COPIED = ("src/l5_live_habitat_service.py", "src/l5_prospective_recorder.py")


def imports_of(rel):
    t = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
    top, intra = set(), set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for a in n.names:
                top.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                intra.add("relative:%s" % (n.module or ""))
            elif n.module:
                head = n.module.split(".")[0]
                top.add(head)
                if head == "src":
                    # `from src import l5_prospective_recorder` puts the module
                    # name in `names`, NOT in `n.module`. Reading only
                    # `n.module` yields the string "src" and the copy-list
                    # check then compares "src/src.py" against the list --
                    # which is never in it, so the test failed on a correct
                    # import. Both spellings must be resolved.
                    if n.module == "src":
                        for a in n.names:
                            intra.add("src.%s" % a.name)
                    else:
                        intra.add(n.module)
    return top, intra


@pytest.mark.parametrize("rel", COPIED)
def test_every_copied_module_is_stdlib_only(rel):
    top, _ = imports_of(rel)
    extern = sorted(m for m in top
                    if m not in sys.stdlib_module_names and m != "src")
    assert extern == [], "%s imports non-stdlib %s" % (rel, extern)


@pytest.mark.parametrize("rel", COPIED)
def test_intra_repo_imports_stay_inside_the_copy_list(rel):
    _, intra = imports_of(rel)
    for m in intra:
        assert not m.startswith("relative:"), "%s uses a relative import" % rel
        mod = m.split(".")[-1]
        assert "src/%s.py" % mod in COPIED, (
            "%s imports src.%s, which the Dockerfile does not copy -- the "
            "image would fail at runtime, or the copy list must be widened "
            "and re-audited" % (rel, mod))


def test_dockerfile_copies_exactly_the_audited_modules():
    lines = [l.strip() for l in open(DOCKERFILE, encoding="utf-8")
             if l.strip().startswith("COPY ")]
    got = sorted(l.split()[1] for l in lines)
    assert got == sorted(COPIED), (
        "the image copy list changed to %s; widen COPIED here only after "
        "auditing the new file's imports" % got)


@pytest.mark.parametrize("rel", COPIED)
def test_no_order_or_credential_surface(rel):
    """AST-level, not substring: `POST_FILL_GRACE_S` contains 'POST' and a
    docstring saying 'no signer' contains 'signer'. Substring scans produced
    false positives on exactly those before."""
    src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    t = ast.parse(src)
    banned_calls = {"sign", "sign_order", "post_order", "submit", "place_order",
                    "create_order", "eth_sign", "sign_typed_data"}
    for n in ast.walk(t):
        if isinstance(n, ast.Call):
            f = n.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            assert name not in banned_calls, "%s calls %s()" % (rel, name)
        # No HTTP verb other than an implicit GET: urlopen with a `data=`
        # argument is a POST.
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", None)
            if nm == "urlopen":
                for kw in n.keywords:
                    assert kw.arg != "data", "%s issues a POST" % rel
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Request":
            for kw in n.keywords:
                assert kw.arg not in ("data", "method"), \
                    "%s builds a non-GET request" % rel


@pytest.mark.parametrize("rel", COPIED)
def test_no_private_key_or_authorization_constants(rel):
    t = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
    banned = {"PRIVATE_KEY", "REQUIRED_AUTH_ID", "AUTHORIZED_SHA",
              "AUTHORIZED_ENVELOPE", "API_SECRET", "PASSPHRASE"}
    for n in ast.walk(t):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            assert n.id not in banned, "%s defines %s" % (rel, n.id)
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", None)
            if nm in ("getenv", "environ"):
                for a in n.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        # EXACT names, not substrings. `_LIVE` as a substring
                        # matched `L5_LIVE_ROOT`, which is the durable volume
                        # path -- a scan that flags the collector's own root
                        # directory teaches us to ignore it.
                        assert a.value.upper() not in BANNED_ENV, \
                            "%s reads env %s" % (rel, a.value)
                        assert not any(b in a.value.upper() for b in
                                       ("PRIVKEY", "PRIVATE_KEY", "SECRET",
                                        "PASSPHRASE", "MNEMONIC")), \
                            "%s reads env %s" % (rel, a.value)


def test_recorder_writes_outside_the_collector_streams():
    """A shared path would let one writer's rotation truncate another's
    evidence."""
    from src import l5_live_habitat_service as S
    src = __import__("inspect").getsource(S.prospective_loop)
    assert 'os.path.join(ROOT, "prospective")' in src


def test_events_stream_is_never_rotated():
    from src import l5_prospective_recorder as P
    assert "events" in P.Recorder.NEVER_ROTATE
    assert "rotations" in P.Recorder.NEVER_ROTATE
    assert "gaps" in P.Recorder.NEVER_ROTATE
    assert "raw_books" not in P.Recorder.NEVER_ROTATE
