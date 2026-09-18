"""Terms acceptance is a precondition for an order, and must never be guessed.

Gemini documents GET /v1/prediction-markets/terms/status returning
hasAcceptedLatest, and documents the rejection for trading without it as
TERMS_NOT_ACCEPTED -- an HTTP 422. That matters for reading canary #1's HTTP
400 correctly: 400 is request validation, so the rejection we received was not
this gate.

UNREADABLE IS NOT "NOT ACCEPTED". Both block an order, but only one of them is
a fact about the account. Reporting a transport failure as a terms refusal
would send the next investigation somewhere false -- and would invite someone
to "fix" it by accepting terms on the account's behalf, which is a legal act
no preflight may perform.
"""
import ast
import asyncio
import json
import pathlib

import pytest

from src.gemini_execution import metadata as MD

ACCEPT_PATH = "/v1/prediction-markets/terms" + "/accept"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _R:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._b is None:
            raise ValueError("not json")
        return self._b


def _answers(mapping, seen):
    async def fake(method, path, **kw):
        seen.append((method, path))
        r = mapping.get(method)
        if isinstance(r, Exception):
            raise r
        return r
    return fake


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "account-test-key")
    monkeypatch.setenv("GEMINI_API_SECRET", "test-secret")


def _patch(monkeypatch, mapping):
    seen = []
    import src.gemini_prediction_auth as A
    monkeypatch.setattr(A, "private_rest_request", _answers(mapping, seen))
    return seen


def test_the_documented_method_is_tried_first(monkeypatch, creds):
    seen = _patch(monkeypatch, {
        "GET": _R(200, {"hasAcceptedLatest": True, "acceptedVersion": 3,
                        "latestVersion": 3})})
    out = _run(MD.terms_status())
    assert seen[0][0] == "GET"
    assert seen[0][1] == MD.TERMS_STATUS_PATH
    assert out["verdict"] == MD.ACCEPTED
    assert out["method"] == "GET"
    assert out["hasAcceptedLatest"] is True
    assert out["acceptedVersion"] == 3 and out["latestVersion"] == 3
    assert out["blocks_orders"] is False


def test_a_get_that_does_not_authenticate_falls_back_to_post(monkeypatch,
                                                             creds):
    """This API's other reads are POST. A method mismatch must not be
    reported as a trading refusal."""
    seen = _patch(monkeypatch, {
        "GET": _R(405, {"reason": "MethodNotAllowed"}),
        "POST": _R(200, {"hasAcceptedLatest": True})})
    out = _run(MD.terms_status())
    assert [m for m, _ in seen] == ["GET", "POST"]
    assert out["verdict"] == MD.ACCEPTED and out["method"] == "POST"


def test_an_explicit_false_is_not_accepted_and_blocks(monkeypatch, creds):
    _patch(monkeypatch, {"GET": _R(200, {"hasAcceptedLatest": False,
                                         "acceptedVersion": None,
                                         "latestVersion": 4})})
    out = _run(MD.terms_status())
    assert out["verdict"] == MD.NOT_ACCEPTED
    assert out["hasAcceptedLatest"] is False
    assert out["blocks_orders"] is True
    assert "422" in out["why"]


def test_unreadable_is_not_reported_as_unaccepted(monkeypatch, creds):
    _patch(monkeypatch, {"GET": _R(401, {"reason": "InvalidSignature"}),
                         "POST": _R(401, {"reason": "InvalidSignature"})})
    out = _run(MD.terms_status())
    assert out["verdict"] == MD.UNREADABLE
    assert out["verdict"] != MD.NOT_ACCEPTED
    assert out["hasAcceptedLatest"] is None      # never False by default
    assert out["blocks_orders"] is True          # still fails closed
    assert [a["status"] for a in out["attempts"]] == [401, 401]


def test_a_transport_exception_is_unreadable_not_unaccepted(monkeypatch,
                                                            creds):
    _patch(monkeypatch, {"GET": RuntimeError("boom"),
                         "POST": RuntimeError("boom")})
    out = _run(MD.terms_status())
    assert out["verdict"] == MD.UNREADABLE
    assert out["hasAcceptedLatest"] is None


def test_absent_credentials_are_their_own_answer(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_SECRET", "")
    out = _run(MD.terms_status())
    assert out["verdict"] == "NO_CREDENTIALS"
    assert out["blocks_orders"] is True


def test_reading_the_status_never_touches_the_accept_path(monkeypatch, creds):
    seen = _patch(monkeypatch, {"GET": _R(200, {"hasAcceptedLatest": True})})
    _run(MD.terms_status())
    assert all(p != ACCEPT_PATH for _, p in seen)


# ------------------------------------------------------------ structural

def _string_constants(tree):
    """String constants EXCLUDING docstrings.

    A plain text scan cannot tell a documented path from a called one -- this
    module's own docstring names the accept path in order to say it must never
    be called, and a substring check would flag exactly the sentence that
    forbids it.
    """
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docs.add(d)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docs]


def test_no_module_anywhere_holds_the_terms_accept_path_as_a_value():
    offenders = []
    for root in ("src", "analysis", "scripts"):
        base = pathlib.Path(root)
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            try:
                tree = ast.parse(p.read_text())
            except SyntaxError:
                continue
            for s in _string_constants(tree):
                if ACCEPT_PATH in s or s.endswith("terms/accept"):
                    offenders.append("%s holds %r" % (p, s))
    assert not offenders, offenders


def test_the_guard_would_actually_catch_it(tmp_path):
    """A structural guard that cannot fail is not a guard."""
    f = tmp_path / "bad.py"
    f.write_text('"""Docstring naming %s is fine."""\nP = "%s"\n'
                 % (ACCEPT_PATH, ACCEPT_PATH))
    consts = _string_constants(ast.parse(f.read_text()))
    assert ACCEPT_PATH in consts
    assert not any(c.startswith("Docstring") for c in consts)


# ------------------------------------------------ clock-derived tickers
#
# The transport diagnostic must not depend on a window being ELIGIBLE to
# trade. Eligibility is a narrow band inside each 5-minute cycle, so tying the
# diagnostic to it means the question goes unanswered on most runs -- which is
# what happened on the first attempt (run 35293857313: "no eligible
# instrument", transport UNRESOLVED, nothing learned).

import datetime as _dt


def _at(*a):
    return _dt.datetime(*a, tzinfo=_dt.timezone.utc).timestamp()


@pytest.mark.parametrize("when,expected", [
    ((2026, 9, 18, 1, 4, 21), "BTC05M2609180105"),
    ((2026, 9, 18, 1, 0, 1), "BTC05M2609180105"),
    ((2026, 9, 18, 0, 47, 0), "BTC05M2609180050"),
    # the rollover: a window that ends after midnight belongs to the next day
    ((2026, 9, 17, 23, 58, 30), "BTC05M2609180000"),
])
def test_the_current_window_is_derived_from_the_clock(when, expected):
    assert MD.current_btc5m_ticker(_at(*when)) == expected


@pytest.mark.parametrize("ticker,expected", [
    ("BTC05M2609180100", "BTC05M2609180055"),
    ("BTC05M2609180000", "BTC05M2609172355"),   # back across midnight
])
def test_the_sibling_is_the_adjacent_window(ticker, expected):
    assert MD.sibling_ticker(ticker) == expected


@pytest.mark.parametrize("bad", ["", "SHORT", "FED260318", None])
def test_an_unparseable_ticker_has_no_sibling(bad):
    assert MD.sibling_ticker(bad) is None


def test_the_two_probe_tickers_are_never_the_same():
    """Two identical tickers would make the probe compare a payload with
    itself and report INCONCLUSIVE forever."""
    for mins in (0, 4, 5, 59):
        tk = MD.current_btc5m_ticker(_at(2026, 9, 18, 1, mins, 0))
        assert MD.sibling_ticker(tk) != tk


# ------------------------------------------------ forensic order history
#
# Whether any previous Gemini live work left a venue-side record on the
# credentials we hold. /v1/mytrades returning zero rows does not answer it:
# mytrades is the classic SPOT trades endpoint, and zero rows there is equally
# consistent with "no trades" and with "wrong endpoint for this product".

class _HR:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._b is None:
            raise ValueError("not json")
        return self._b


def _history(monkeypatch, by_status):
    seen = []

    async def fake(method, path, *, params=None, timeout=20.0):
        seen.append((method, path, (params or {}).get("status")))
        return by_status[(params or {}).get("status")]

    import src.gemini_prediction_auth as A
    monkeypatch.setattr(A, "private_rest_json", fake)
    return seen


def test_an_order_on_record_is_reported_with_its_fields(monkeypatch, creds):
    row = {"orderId": "abc", "clientOrderId": "x-1", "symbol": "GEMI-X-UP",
           "side": "buy", "outcome": "yes", "price": "0.42", "quantity": "1",
           "status": "filled", "filledQuantity": "1"}
    _history(monkeypatch, {
        "filled": _HR(200, {"orders": [row]}),
        "cancelled": _HR(200, {"orders": []}),
        "open": _HR(200, {"orders": []})})
    out = _run(MD.order_history())
    assert out["verdict"] == "ORDERS_FOUND"
    assert out["total_rows"] == 1
    sample = [e for e in out["per_status"] if e["status_filter"] == "filled"
              ][0]["sample"]
    assert sample["orderId"] == "abc" and sample["price"] == "0.42"


def test_an_empty_history_is_scoped_to_these_credentials(monkeypatch, creds):
    _history(monkeypatch, {s: _HR(200, {"orders": []})
                           for s in ("filled", "cancelled", "open")})
    out = _run(MD.order_history())
    assert out["verdict"] == "NO_ORDERS_ON_RECORD"
    assert "never about other accounts" in out["labels"]


def test_an_unreadable_history_is_never_an_empty_one(monkeypatch, creds):
    _history(monkeypatch, {"filled": _HR(400, {"reason": "InvalidRequest"}),
                           "cancelled": _HR(200, {"orders": []}),
                           "open": _HR(200, {"orders": []})})
    out = _run(MD.order_history())
    assert out["verdict"] == "UNREADABLE"
    assert out["verdict"] != "NO_ORDERS_ON_RECORD"
    assert out["unreadable_statuses"] == ["filled"]


def test_every_status_is_asked_separately(monkeypatch, creds):
    """`status` is a scalar in the documented body, not a list, so a single
    call could only ever see one third of the history."""
    seen = _history(monkeypatch, {s: _HR(200, {"orders": []})
                                  for s in ("filled", "cancelled", "open")})
    _run(MD.order_history())
    assert sorted(s for _, _, s in seen) == ["cancelled", "filled", "open"]
    assert {p for _, p, _ in seen} == {MD.ORDER_HISTORY_PATH}


def test_the_history_read_never_touches_a_mutation_path(monkeypatch, creds):
    from src.gemini_execution import spec
    seen = _history(monkeypatch, {s: _HR(200, {"orders": []})
                                  for s in ("filled", "cancelled", "open")})
    _run(MD.order_history())
    assert not ({p for _, p, _ in seen} & set(spec.MUTATION_PATHS))
