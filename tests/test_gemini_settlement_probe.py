"""A probe that reads an account must be provably unable to trade with it."""
import ast
import asyncio
import inspect

import pytest

from src import gemini_settlement as S, gemini_settlement_probe as P


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro)


# ------------------------------------------------------------ fails closed

def test_without_credentials_it_refuses_rather_than_half_answering(monkeypatch):
    monkeypatch.setattr(P, "has_credentials", lambda: False)
    out = _run(P.probe(["GEMI-X"]))
    assert out["ok"] is False and out["verdict"] == "NO_CREDENTIALS"
    assert out["gate"] == "BLOCKED"
    assert "ok" in out and "per_symbol" not in out


def test_the_cli_exits_non_zero_when_it_refuses(monkeypatch):
    monkeypatch.setattr(P, "has_credentials", lambda: False)
    assert P.main(["--symbols", "GEMI-X"]) == 2


# --------------------------------------------------------- structurally safe

def test_no_order_path_is_reachable_from_this_module():
    tree = ast.parse(inspect.getsource(P))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for forbidden in ("place_limit_buy", "new_order", "cancel_order",
                      "submit", "Transport"):
        assert forbidden not in names, "%s is reachable" % forbidden


def test_it_imports_nothing_that_can_place_an_order():
    tree = ast.parse(inspect.getsource(P))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    for forbidden in ("src.gemini_kalshi_probe_transport", "src.pmus_q1_executor",
                      "src.l5_surface_exec.adapter"):
        assert forbidden not in mods


def test_every_path_it_touches_is_a_read():
    """POST is how Gemini authenticates a read; the paths are what matter."""
    for path in P.READ_PATHS + P.SETTLEMENT_CANDIDATES + ("/v1/mytrades",):
        assert "order" not in path and "cancel" not in path


def test_it_never_reads_a_credential_value():
    """It reports only WHETHER one was present.

    Scanned over AST CALL TARGETS, not as text: `has_credentials` contains
    `credentials` as a substring, so a naive scan fails on the very call that
    makes this module safe."""
    tree = ast.parse(inspect.getsource(P))
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    assert "credentials" not in called, "it reads the credential values"
    assert "has_credentials" in called, "it must still check for presence"
    # and it never takes one as an argument it could echo back
    args = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args |= {a.arg for a in n.args.args + n.args.kwonlyargs}
    assert not (args & {"api_key", "api_secret", "secret", "key"})


# ----------------------------------------------------- reads, and refusals

class _Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._b, self.text = code, body, text

    def json(self):
        if self._b is None:
            raise ValueError("not json")
        return self._b


def _fake(responses):
    async def _req(method, path, *, timeout=10.0, params=None):
        key = path
        if path not in responses:
            raise KeyError(path)
        r = responses[key]
        if isinstance(r, Exception):
            raise r
        return r
    return _req


def _all_ok(mytrades):
    base = {p: _Resp(200, []) for p in P.READ_PATHS}
    base.update({p: _Resp(404, None, "not found")
                 for p in P.SETTLEMENT_CANDIDATES})
    base["/v1/mytrades"] = _Resp(200, mytrades)
    return base


def test_a_404_on_a_settlement_candidate_is_reported_as_a_result(monkeypatch):
    """It says the credit does not live there, which nobody had established."""
    monkeypatch.setattr(P, "has_credentials", lambda: True)
    monkeypatch.setattr(P, "private_rest_request", _fake(_all_ok([])))
    out = _run(P.probe(["GEMI-X"]))
    assert out["settlement_endpoint_found"] == []
    assert all(c["status"] == 404 for c in out["settlement_candidates"])
    assert all(c["reachable"] for c in out["settlement_candidates"])


def test_a_found_settlement_endpoint_is_named(monkeypatch):
    r = _all_ok([])
    r["/v1/settlements"] = _Resp(200, [{"symbol": "GEMI-X"}])
    monkeypatch.setattr(P, "has_credentials", lambda: True)
    monkeypatch.setattr(P, "private_rest_request", _fake(r))
    out = _run(P.probe(["GEMI-X"]))
    assert out["settlement_endpoint_found"] == ["/v1/settlements"]


def test_authoritative_fills_become_legs_the_gate_accepts(monkeypatch):
    monkeypatch.setattr(P, "has_credentials", lambda: True)
    monkeypatch.setattr(P, "private_rest_request", _fake(_all_ok([
        {"type": "Buy", "amount": "1", "price": "0.40", "fee_amount": "0.01",
         "tid": 7},
        {"type": "Sell", "amount": "1", "price": "0.55", "fee_amount": "0.01",
         "tid": 8}])))
    out = _run(P.probe(["GEMI-X"]))
    assert out["per_symbol"][0]["legs_parsed"] == 2
    assert out["per_symbol"][0]["position"] == "0"
    inst = out["gate"]["instruments"][0]
    assert inst["satisfied"][S.AUTHORITATIVE_FILLS] is True
    assert inst["fees"] == "0.02"


def test_fills_alone_do_not_open_the_gate(monkeypatch):
    """Settlement value and confirmed cash are still missing, and must be."""
    monkeypatch.setattr(P, "has_credentials", lambda: True)
    monkeypatch.setattr(P, "private_rest_request", _fake(_all_ok([
        {"type": "Buy", "amount": "1", "price": "0.40", "fee_amount": "0"}])))
    out = _run(P.probe(["GEMI-X"]))
    assert out["gate"]["gate"] == "BLOCKED"
    assert S.SETTLEMENT_VALUE in out["gate"]["missing_union"]
    assert S.CASH_CONFIRMED in out["gate"]["missing_union"]


def test_an_unreadable_row_is_skipped_not_guessed(monkeypatch):
    monkeypatch.setattr(P, "has_credentials", lambda: True)
    monkeypatch.setattr(P, "private_rest_request", _fake(_all_ok([
        {"type": "Buy", "amount": "1", "price": "0.40", "fee_amount": "0"},
        {"type": "Nonsense", "amount": "1", "price": "0.40"},
        "not even a dict"])))
    out = _run(P.probe(["GEMI-X"]))
    assert out["per_symbol"][0]["legs_parsed"] == 1
    assert out["per_symbol"][0]["rows"] == 3


def test_an_unparseable_response_is_not_reported_as_empty(monkeypatch):
    """'The venue has nothing' is a different claim from 'we could not read it'."""
    r = _all_ok([])
    r["/v1/mytrades"] = _Resp(200, None, "<html>maintenance</html>")
    monkeypatch.setattr(P, "has_credentials", lambda: True)
    monkeypatch.setattr(P, "private_rest_request", _fake(r))
    out = _run(P.probe(["GEMI-X"]))
    assert out["per_symbol"][0]["unparseable"] is True
    assert out["per_symbol"][0]["rows"] is None


def test_an_endpoint_that_raises_is_a_result_not_a_crash(monkeypatch):
    r = _all_ok([])
    r["/v1/transfers"] = RuntimeError("boom")
    monkeypatch.setattr(P, "has_credentials", lambda: True)
    monkeypatch.setattr(P, "private_rest_request", _fake(r))
    out = _run(P.probe(["GEMI-X"]))
    t = [a for a in out["account_reads"] if a["path"] == "/v1/transfers"][0]
    assert t["reachable"] is False and t["error"] == "RuntimeError"
