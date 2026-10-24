"""The watchdog's side of the owner mandate: publish, serve, and refuse.

THE ASYMMETRY THIS PRESERVES is the one `l5_halt_gate` already states for
the latch: the executor must never be able to change what governs it. So the
replay floor and the revocation list live on the WATCHDOG's volume, the
watchdog is the only writer, and the executor's only verb is GET.

WHAT MUST HOLD: the served documents are an exact allowlist and nothing on
the volume outside it is reachable; a document nobody refreshes goes STALE
and refuses, so a dead watchdog cannot keep authorizing; absence refuses
rather than reading as "nothing revoked"; and the floor the executor reads is
the one the watchdog published, never one the executor could write.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_halt_gate as HG
from src import l5_halt_server as SRV
from src import l5_mandate_signature as MS


@pytest.fixture
def vol(tmp_path):
    return tmp_path / "data"


def test_the_publisher_writes_both_documents_where_the_reader_looks(vol):
    """THE BUG THIS CLOSES. `WatchdogSeqWriter` keeps the authoritative floor
    in a `mandate_seq/` subdirectory, but the executor's HttpSeqFloorStore
    reads `mandate_floor.json` at the SERVED root -- so the floor existing on
    disk did not make it readable, and the server answered 404 to a floor
    that was sitting right there."""
    MS.WatchdogSeqWriter(str(vol)).observe("owner-k", 4)
    out = MS.WatchdogPublisher(str(vol), str(vol)).publish(revoked=[])
    assert out["floors"] == {"owner-k": 4}
    for key in (MS.FLOOR_KEY, MS.REVOCATION_KEY):
        doc = json.loads((vol / key).read_text())
        assert doc["asof_utc"]
    assert json.loads((vol / MS.FLOOR_KEY).read_text())["floors"] == {
        "owner-k": 4}


def test_the_served_keys_are_an_exact_allowlist():
    """`FileStore._path` maps a key onto a filesystem path, so an
    unconstrained key would be a traversal and the volume also holds the
    watchdog's own working state."""
    assert SRV.SERVED_KEYS == (HG.HALT_KEY, MS.FLOOR_KEY,
                               MS.REVOCATION_KEY)
    assert len(set(SRV.SERVED_KEYS)) == 3


def _opener_for(vol):
    """Serve the volume's bytes to the REAL HTTP client, so the real
    freshness, parse and shape checks run."""
    def _open(url, _timeout):
        key = url.rsplit("/", 1)[-1]
        return (vol / key).read_bytes()
    return _open


def test_a_fresh_published_floor_is_READ_through_the_real_client(vol):
    MS.WatchdogSeqWriter(str(vol)).observe("owner-k", 4)
    MS.WatchdogPublisher(str(vol), str(vol)).publish(revoked=[])
    st = MS.HttpSeqFloorStore("http://wd.internal",
                              opener=_opener_for(vol))
    assert st.floor("owner-k") == 4
    # a key the watchdog has never seen is a REAL zero, not a guess
    assert st.floor("never-seen") == 0
    rv = MS.HttpRevocationStore("http://wd.internal",
                                opener=_opener_for(vol))
    assert rv.revoked() == frozenset()


def test_a_stale_document_refuses_so_a_dead_watchdog_cannot_authorize(vol):
    """THE LIVENESS PROPERTY, driven through the real client rather than
    asserted on arithmetic. Production refreshes every 20s against a 900s
    bound; here the stamp is backdated instead."""
    MS.WatchdogPublisher(str(vol), str(vol)).publish(revoked=[])
    for key in (MS.FLOOR_KEY, MS.REVOCATION_KEY):
        doc = json.loads((vol / key).read_text())
        doc["asof_utc"] = (
            dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(seconds=MS.DEFAULT_MAX_AGE_S + 60)).isoformat()
        (vol / key).write_text(json.dumps(doc))
    with pytest.raises(MS.MandateUnavailable) as e:
        MS.HttpSeqFloorStore("http://wd.internal",
                             opener=_opener_for(vol)).floor("owner-k")
    assert "stale" in str(e.value)
    with pytest.raises(MS.MandateUnavailable):
        MS.HttpRevocationStore("http://wd.internal",
                               opener=_opener_for(vol)).revoked()


def test_an_absent_document_refuses_rather_than_reading_as_empty(vol):
    """Absence is NOT "nothing revoked" -- unlike the halt latch, where
    absence genuinely means no halt."""
    vol.mkdir(parents=True, exist_ok=True)
    with pytest.raises(MS.MandateUnavailable):
        MS.HttpRevocationStore("http://wd.internal",
                               opener=_opener_for(vol)).revoked()


def test_an_empty_revocation_list_is_a_POSITIVE_statement(vol):
    """Published-and-empty means "nothing revoked" and is NOT the same as
    the document being absent, which refuses."""
    out = MS.WatchdogPublisher(str(vol), str(vol)).publish(revoked=[])
    assert out["revoked"] == []
    doc = json.loads((vol / MS.REVOCATION_KEY).read_text())
    assert doc["revoked"] == [] and doc["asof_utc"]


def test_the_owner_revokes_through_the_watchdogs_own_environment(
        vol, monkeypatch):
    """Set on the WATCHDOG, which the executor cannot write."""
    monkeypatch.setenv(MS.REVOKED_ENV, "l5-standing-001, other#4 ,")
    assert MS.revoked_from_env() == ["l5-standing-001", "other#4"]
    out = MS.WatchdogPublisher(str(vol), str(vol)).publish()
    assert out["revoked"] == ["l5-standing-001", "other#4"]


def test_publishing_never_lowers_the_authoritative_floor(vol):
    w = MS.WatchdogSeqWriter(str(vol))
    w.observe("owner-k", 7)
    w.observe("owner-k", 2)                        # must NOT lower
    out = MS.WatchdogPublisher(str(vol), str(vol)).publish(revoked=[])
    assert out["floors"]["owner-k"] == 7


def test_the_watchdog_image_carries_the_module_it_now_imports():
    """A module missing from the image is an ImportError at runtime, which
    would leave the watchdog dead rather than merely unable to publish."""
    with open("deploy/l5_halt_watchdog/Dockerfile") as fh:
        copied = {ln.split()[1] for ln in fh if ln.startswith("COPY ")}
    assert "src/l5_mandate_signature.py" in copied
    assert "src/l5_halt_server.py" in copied


def test_the_watchdog_publishes_on_every_sweep_not_once():
    """A document written once authorizes for max_age_s and then correctly
    refuses forever, so the publish must be INSIDE the loop."""
    with open("scripts/l5_halt_watchdog.py") as fh:
        src = fh.read()
    body = src[src.index("    while True:"):]
    assert "_pub.publish()" in body, "publish must be inside the sweep loop"
    # and it must not be able to break the sweep
    assert "L5_MANDATE_PUBLISH_FAILED" in body


def test_nothing_in_the_watchdog_can_sign_a_mandate():
    """It publishes the floor and the revocations. It must not be able to
    produce a document that verifies."""
    import inspect
    src = inspect.getsource(MS)
    assert "Ed25519PrivateKey" not in src
    assert MS.MANDATE_PUBKEYS == {}, "shipped state is an empty registry"


# ---------------------------------------- the image must be able to BOOT
def test_the_watchdog_image_copies_every_module_its_entrypoint_reaches():
    """THE PRE-DEPLOY CHECK FOR THIS PATCH, and the executor has had one of
    these for a while while the watchdog never did.

    The entrypoint now imports `l5_mandate_signature`. A module missing from
    the COPY list is an ImportError at boot -- and a watchdog that cannot
    boot is a watchdog that is not watching, which is strictly worse than
    one that cannot publish. Transitive, because an import two deep fails
    exactly the same way.
    """
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def path_of(mod):
        p = os.path.join(root, mod.replace(".", "/") + ".py")
        if os.path.exists(p):
            return p
        p2 = os.path.join(root, mod.replace(".", "/"), "__init__.py")
        return p2 if os.path.exists(p2) else None

    seen, queue, needed = set(), ["scripts.l5_halt_watchdog"], []
    while queue:
        mod = queue.pop(0)
        if mod in seen:
            continue
        seen.add(mod)
        p = path_of(mod)
        if not p:
            continue
        needed.append(os.path.relpath(p, root))
        with open(p, encoding="utf-8", errors="replace") as fh:
            try:
                tree = ast.parse(fh.read())
            except SyntaxError:
                continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                queue += [a.name for a in n.names
                          if a.name.split(".")[0] in ("src", "scripts")]
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] in ("src", "scripts"):
                    queue.append(n.module)
                    queue += ["%s.%s" % (n.module, a.name)
                              for a in n.names]

    with open(os.path.join(root, "deploy", "l5_halt_watchdog",
                           "Dockerfile"), encoding="utf-8") as fh:
        copied = {ln.split()[1] for ln in fh if ln.startswith("COPY ")}
    # the entrypoint itself is copied as a directory or a file; allow both
    missing = {m for m in needed if m not in copied
               and not any(c.rstrip("/") == os.path.dirname(m)
                           or c == m for c in copied)}
    assert missing == set(), sorted(missing)


def test_the_cryptography_import_stays_lazy():
    """`cryptography` is imported INSIDE `_verify_ed25519`, not at module
    level, so importing this module never requires it. The watchdog image
    does install it, but a module-level import would also make every other
    consumer of this file depend on it -- and an ImportError at boot in the
    watchdog is the failure that matters."""
    import ast
    tree = ast.parse(open("src/l5_mandate_signature.py").read())
    for node in tree.body:                      # MODULE level only
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            name = (getattr(node, "module", "") or "") + "".join(
                a.name for a in node.names)
            assert "cryptography" not in name, (
                "cryptography must not be imported at module level")
