"""The WS probe must be structurally incapable of moving money."""
import ast
import pathlib

SRC = pathlib.Path("scripts/pmus_ws_private_probe.py")


def test_only_subscribe_and_unsubscribe_may_be_sent():
    import importlib.util
    spec = importlib.util.spec_from_file_location("probe", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.ALLOWED_KEYS == {"subscribe", "unsubscribe"}
    for bad in ({"order": {}}, {"post": {}}, {"subscribe": {}, "order": {}}):
        try:
            m.guard(bad)
        except RuntimeError:
            continue
        raise AssertionError("guard admitted %r" % (bad,))
    assert m.guard({"subscribe": {"x": 1}}) == {"subscribe": {"x": 1}}


def test_it_constructs_nothing_that_can_post():
    src = SRC.read_text()
    for forbidden in ("submit_body", "submit(", "/v1/orders", "GuardedTransport",
                      "PmusTransport", "l5_order_body", "requests.post",
                      "urlopen"):
        assert forbidden not in src, forbidden


def test_it_does_not_import_the_money_path():
    tree = ast.parse(SRC.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods |= {"%s.%s" % (node.module or "", a.name)
                     for a in node.names}
    banned = ("money_path", "live_mandate", "native_venue", "q1_executor",
              "l5_pmus_transport")
    for m in mods:
        assert not any(b in m for b in banned), m


def test_the_known_types_are_read_from_the_recorder_not_retyped():
    src = SRC.read_text()
    assert "R.SUB_MARKET_DATA" in src and "R.SUB_TRADE" in src
    # 2 and everything above 3 is the unknown space this probe exists for
    assert "range(0, 13)" in src
