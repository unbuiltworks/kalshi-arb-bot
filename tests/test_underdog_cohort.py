"""UNDERDOG COHORT 1 safety suite. Every gate, and every refusal."""
from decimal import Decimal as D

import asyncio

import pytest

from src.gemini_execution import arming, payload
from src.gemini_execution import underdog_cohort as U


def _ledger(tmp_path, monkeypatch, rows=()):
    import json
    d = tmp_path / "led"
    d.mkdir()
    monkeypatch.setattr(U, "LEDGER_DIR", str(d))
    monkeypatch.setattr(U, "LEDGER", str(d / "cohort.jsonl"))
    monkeypatch.setattr(U, "HALT", str(d / "HALT.json"))
    if rows:
        with open(d / "cohort.jsonl", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")


# ------------------------------------------------------------- the envelope
def test_the_envelope_is_the_owners():
    e = U.AUTHORIZED_ENVELOPE
    assert e["quantity"] == "1"
    assert e["max_acquisition_price"] == "0.60"
    assert e["max_entry_attempts"] == 5
    assert e["max_attempts_per_window"] == 1
    assert e["max_outstanding_orders"] == 1
    assert e["side_rule"] == "UNDERDOG_LOWER_EXECUTABLE_Q1_VWAP"
    assert e["depth_variant"] == "CUMULATIVE"
    assert e["sell_permitted"] is False
    assert e["averaging_down_permitted"] is False
    assert e["size_changes_permitted"] is False
    assert e["price_chasing_permitted"] is False
    assert e["hold_to_settlement"] is True


def test_the_exposure_is_derived_and_exact():
    assert str(U.max_principal()) == "3.0000"
    assert str(U.max_fees()) == "0.1000"
    assert str(U.cohort_cap()) == "3.1000"
    assert U.cohort_cap() == U.max_principal() + U.max_fees()


def test_the_worst_fee_is_searched_not_taken_at_the_ceiling():
    """The fee is p*(1-p)-shaped and peaks INSIDE the ceiling, so a bound
    evaluated only at 0.60 would understate it."""
    w = U.worst_case_fee()
    at_ceiling = U.fee_at(D("0.60"))
    assert D(w["worst_fee_usd"]) >= at_ceiling
    assert D(w["worst_fee_usd"]) >= U.fee_at(D("0.50"))


def test_fees_round_up_because_a_bound_that_rounds_down_is_not_a_bound():
    assert U.fee_at(D("0.18")) >= D("0.01")
    assert U.fee_at(D("0.18")) == D("0.02")


# ---------------------------------------------------------------- the rule
def _dec(up, down, *, age=0.5, elapsed=15.5):
    """A qualifying decision built from an explicit synthetic ladder."""
    return {"up": up, "down": down, "age": age, "elapsed": elapsed}


def test_the_underdog_is_the_cheaper_executable_side(monkeypatch):
    seen = {}

    def fake_depth(symbol, **kw):
        return {"readable": True, "venue_ts": "1791216316",
                "asks": {"levels": [{"price": "0.70", "qty": "25"}]},
                "bids": {"levels": [{"price": "0.65", "qty": "25"}]}}

    class Lad:
        crossed = False

    def fake_book(symbol):
        return {"readable": True, "bid": D("0.65"), "ask": D("0.70"),
                "bid_size": D("25"), "ask_size": D("25"),
                "snapshot_age_s": 0.4, "ladder": Lad()}

    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", fake_depth)
    monkeypatch.setattr(U.P, "book_from_venue", fake_book)
    d = U.evaluate("GEMI-X-UP", elapsed_since_open_s=15.5)
    # UP costs 0.70; DOWN costs 1 - 0.65 = 0.35, so DOWN is the underdog
    assert d["up_q1_vwap"] == "0.70"
    assert d["down_q1_vwap"] == "0.35"
    assert d["underdog"] == "DOWN"
    assert d["leader"] == "UP"
    assert d["requested_price"] == "0.35"
    assert d["outcome"] == payload.spec.OUTCOME_NO
    assert d["qualifies"] is True
    assert seen == {}


def test_a_cheaper_side_above_the_ceiling_is_no_trade(monkeypatch):
    def fake_depth(symbol, **kw):
        return {"readable": True, "venue_ts": "1791216316",
                "asks": {"levels": [{"price": "0.62", "qty": "25"}]},
                "bids": {"levels": [{"price": "0.30", "qty": "25"}]}}

    class Lad:
        crossed = False

    monkeypatch.setattr(U.P, "book_from_venue", lambda s: {
        "readable": True, "bid": D("0.30"), "ask": D("0.62"),
        "bid_size": D("25"), "ask_size": D("25"),
        "snapshot_age_s": 0.4, "ladder": Lad()})
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", fake_depth)
    d = U.evaluate("GEMI-X-UP", elapsed_since_open_s=20.0)
    # UP 0.62, DOWN 0.70 -> the cheaper side is 0.62, above the 0.60 ceiling
    assert d["underdog"] == "UP"
    assert d["above_ceiling_no_trade"] is True
    assert d["qualifies"] is False
    assert "within_acquisition_ceiling" in d["failed_checks"]


def test_a_tie_fails_closed(monkeypatch):
    """There is no underdog when both sides cost the same, and choosing one
    would be a coin flip the authorization does not describe."""
    class Lad:
        crossed = False

    monkeypatch.setattr(U.P, "book_from_venue", lambda s: {
        "readable": True, "bid": D("0.50"), "ask": D("0.50"),
        "bid_size": D("25"), "ask_size": D("25"),
        "snapshot_age_s": 0.4, "ladder": Lad()})
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: {
        "readable": True, "venue_ts": "1791216316",
        "asks": {"levels": [{"price": "0.50", "qty": "25"}]},
        "bids": {"levels": [{"price": "0.50", "qty": "25"}]}})
    d = U.evaluate("GEMI-X-UP", elapsed_since_open_s=16.0)
    assert d["side"] is None
    assert d["qualifies"] is False
    assert "underdog_is_unambiguous" in d["failed_checks"]


def test_a_touch_too_thin_for_q1_is_not_fillable(monkeypatch):
    """CUMULATIVE means cumulative: a 0.4-contract touch cannot fill one."""
    class Lad:
        crossed = False

    monkeypatch.setattr(U.P, "book_from_venue", lambda s: {
        "readable": True, "bid": D("0.30"), "ask": D("0.40"),
        "bid_size": D("0.4"), "ask_size": D("0.4"),
        "snapshot_age_s": 0.4, "ladder": Lad()})
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: {
        "readable": True, "venue_ts": "1791216316",
        "asks": {"levels": [{"price": "0.40", "qty": "0.4"}]},
        "bids": {"levels": [{"price": "0.30", "qty": "0.4"}]}})
    d = U.evaluate("GEMI-X-UP", elapsed_since_open_s=16.0)
    assert d["qualifies"] is False
    assert "economic_two_sided_q1_cumulative" in d["failed_checks"]


def test_integrity_is_derived_not_defaulted(monkeypatch):
    """An unstamped or crossed book must FAIL the integrity requirement.

    The first draft read a `book_integrity` key that book_from_venue never
    returns and defaulted it to OK, so an integrity requirement the owner
    asked for would have passed unconditionally.
    """
    class Crossed:
        crossed = True

    monkeypatch.setattr(U.P, "book_from_venue", lambda s: {
        "readable": True, "bid": D("0.30"), "ask": D("0.40"),
        "bid_size": D("25"), "ask_size": D("25"),
        "snapshot_age_s": 0.4, "ladder": Crossed()})
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: {
        "readable": True, "venue_ts": None,
        "asks": {"levels": [{"price": "0.40", "qty": "25"}]},
        "bids": {"levels": [{"price": "0.30", "qty": "25"}]}})
    d = U.evaluate("GEMI-X-UP", elapsed_since_open_s=16.0)
    assert d["checks"]["book_integrity_ok"] is False
    assert d["book_integrity_detail"]["not_crossed"] is False
    assert d["book_integrity_detail"]["venue_timestamped"] is False
    assert d["qualifies"] is False


def test_before_T15_never_qualifies(monkeypatch):
    class Lad:
        crossed = False

    monkeypatch.setattr(U.P, "book_from_venue", lambda s: {
        "readable": True, "bid": D("0.30"), "ask": D("0.40"),
        "bid_size": D("25"), "ask_size": D("25"),
        "snapshot_age_s": 0.4, "ladder": Lad()})
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: {
        "readable": True, "venue_ts": "1791216316",
        "asks": {"levels": [{"price": "0.40", "qty": "25"}]},
        "bids": {"levels": [{"price": "0.30", "qty": "25"}]}})
    d = U.evaluate("GEMI-X-UP", elapsed_since_open_s=14.9)
    assert d["qualifies"] is False
    assert "checkpoint_at_or_after_min" in d["failed_checks"]


def test_a_stale_quote_never_qualifies(monkeypatch):
    class Lad:
        crossed = False

    monkeypatch.setattr(U.P, "book_from_venue", lambda s: {
        "readable": True, "bid": D("0.30"), "ask": D("0.40"),
        "bid_size": D("25"), "ask_size": D("25"),
        "snapshot_age_s": 2.5, "ladder": Lad()})
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: {
        "readable": True, "venue_ts": "1791216316",
        "asks": {"levels": [{"price": "0.40", "qty": "25"}]},
        "bids": {"levels": [{"price": "0.30", "qty": "25"}]}})
    d = U.evaluate("GEMI-X-UP", elapsed_since_open_s=16.0)
    assert d["qualifies"] is False
    assert "quote_fresh" in d["failed_checks"]


# ------------------------------------------------------------- the refusals
# This suite uses asyncio.run, matching the existing canary suite; there is
# no pytest-asyncio in this environment and adding a plugin to a safety
# suite is not worth the dependency.
def _attempt(symbol, decision):
    return asyncio.run(U.attempt(symbol, decision))


def _armed(monkeypatch):
    """Pass the ENV gate so a LATER refusal is the one under test.

    Without this the env gate refuses first -- correctly, since nothing is
    armed locally -- and a test asserting "already consumed" would pass on
    the wrong refusal, proving nothing about the gate it names.
    """
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", U.UNDERDOG_AUTH_ID)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert U.auth_gate()["env_matches"] is True


def test_refuses_a_non_qualifying_decision(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    with pytest.raises(U.UnderdogRefused):
        _attempt("GEMI-X-UP",
                 {"qualifies": False, "failed_checks": ["quote_fresh"]})


def test_refuses_once_the_cohort_expires(tmp_path, monkeypatch):
    _armed(monkeypatch)
    rows = [{"kind": "SLOT", "slot": i, "market": "GEMI-%d-UP" % i,
             "committed_usd": "0.10", "requested_price": "0.10",
             "requested_quantity": "1"} for i in range(1, 6)]
    _ledger(tmp_path, monkeypatch, rows)
    assert U.cohort_state()["expired"] is True
    with pytest.raises(U.UnderdogRefused) as e:
        _attempt("GEMI-NEW-UP", {"qualifies": True, "side": "UP",
                                 "requested_price": "0.10"})
    assert "EXPIRED" in str(e.value)


def test_refuses_a_second_attempt_in_the_same_window(tmp_path, monkeypatch):
    _armed(monkeypatch)
    _ledger(tmp_path, monkeypatch, [
        {"kind": "SLOT", "slot": 1, "market": "GEMI-X-UP",
         "committed_usd": "0.10", "requested_price": "0.10",
         "requested_quantity": "1"}])
    with pytest.raises(U.UnderdogRefused) as e:
        _attempt("GEMI-X-UP", {"qualifies": True, "side": "UP",
                               "requested_price": "0.10"})
    assert "already consumed" in str(e.value)


def test_refuses_a_price_above_the_ceiling(tmp_path, monkeypatch):
    _armed(monkeypatch)
    _ledger(tmp_path, monkeypatch)
    with pytest.raises(U.UnderdogRefused) as e:
        _attempt("GEMI-X-UP", {"qualifies": True, "side": "UP",
                               "requested_price": "0.61"})
    assert "ceiling" in str(e.value)


def test_refuses_when_halted(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    U.halt("TEST", {"why": "a halted cohort may not send"})
    with pytest.raises(U.UnderdogRefused):
        _attempt("GEMI-X-UP", {"qualifies": True, "side": "UP",
                               "requested_price": "0.10"})


def test_refuses_an_ambiguous_side(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    with pytest.raises(U.UnderdogRefused):
        _attempt("GEMI-X-UP", {"qualifies": True, "side": None,
                               "requested_price": "0.10"})


# --------------------------------------------------------- the authorization
def test_both_constants_name_this_grant():
    assert arming.EXPECTED_AUTH_ID == U.UNDERDOG_AUTH_ID
    assert payload.SEMANTICS_PROBE_AUTH_ID == U.UNDERDOG_AUTH_ID
    g = U.auth_gate()
    assert g["arming_matches"] and g["taker_key_matches"]


def test_this_grant_closes_the_other_cohort_paths():
    from src.gemini_execution import btc5m_canary as C
    from src.gemini_execution import ci15m_probe as P
    assert P.PROBE_AUTH_ID != arming.EXPECTED_AUTH_ID
    assert C.CANARY_AUTH_ID != arming.EXPECTED_AUTH_ID
    assert U.auth_gate()["ci15m_submit_is_now_refused"] is True


def test_attribution_is_by_exact_order_id(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, [
        {"kind": "SLOT", "slot": 1, "market": "M", "side": "DOWN",
         "outcome": "no", "requested_price": "0.35",
         "requested_quantity": "1"},
        {"kind": "SUBMITTED", "slot": 1, "market": "M",
         "venue": {"body": {"orderId": 777}}}])
    got = U.attribute_fills("M", [{"order_id": "777", "leg": "no",
                                   "price": "0.35", "qty": "1", "tid": 1}])
    assert got["ok"] is True
    assert got["attributed"][0]["_attribution_route"] == "ORDER_ID"


def test_a_stranger_fill_halts(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, [
        {"kind": "SLOT", "slot": 1, "market": "M", "side": "DOWN",
         "outcome": "no", "requested_price": "0.35",
         "requested_quantity": "1"},
        {"kind": "SUBMITTED", "slot": 1, "market": "M",
         "venue": {"body": {"orderId": 777}}}])
    got = U.attribute_fills("M", [{"order_id": "999", "leg": "no",
                                   "price": "0.20", "qty": "1", "tid": 1}])
    assert got["ok"] is False
    assert U.halted() is not None


# --------------------------------------------------------------------------
# RUN 1's CRASH, as a test. The runner copied `dec["book"]`, a key the canary
# decision carries and this one does not, so the cohort died of KeyError on
# its FIRST window -- before any order, but also before any evidence.
def test_every_decision_key_the_runner_copies_really_exists(monkeypatch):
    import ast
    import inspect
    from scripts import gemini_btc5m_underdog_run as R

    class Lad:
        crossed = False

    monkeypatch.setattr(U.P, "book_from_venue", lambda s: {
        "readable": True, "bid": D("0.30"), "ask": D("0.40"),
        "bid_size": D("25"), "ask_size": D("25"),
        "snapshot_age_s": 0.4, "ladder": Lad()})
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: {
        "readable": True, "venue_ts": "1791216316",
        "asks": {"levels": [{"price": "0.40", "qty": "25"}]},
        "bids": {"levels": [{"price": "0.30", "qty": "25"}]}})
    dec = U.evaluate("GEMI-X-UP", elapsed_since_open_s=16.0)

    tree = ast.parse(inspect.getsource(R.work_window))
    wanted = set()
    for node in ast.walk(tree):
        # dec.get("k") / dec["k"] inside the evidence comprehension
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and getattr(node.func.value, "id", None) == "dec"):
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    wanted.add(a.value)
        if (isinstance(node, ast.Subscript)
                and getattr(node.value, "id", None) == "dec"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            wanted.add(node.slice.value)
        # the tuple of names in the dict comprehension
        if isinstance(node, ast.Tuple):
            for e in node.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    wanted.add(e.value)
    missing = sorted(k for k in wanted
                     if k not in dec and k in _DECISION_SHAPED)
    assert not missing, ("the runner reads decision keys that do not "
                        "exist: %r" % missing)


#: the key names that are unambiguously decision fields, so the AST sweep
#: above does not flag unrelated string tuples elsewhere in the function.
_DECISION_SHAPED = frozenset({
    "utc", "qualifies", "failed_checks", "side", "underdog", "leader",
    "requested_price", "requested_quantity", "up_q1_vwap", "down_q1_vwap",
    "combined_q1_cost", "spread_touch", "depth", "elapsed_since_open_s",
    "quote_age_s", "above_ceiling_no_trade", "projected_fee_usd",
    "projected_outlay_usd", "book_integrity_detail", "book", "checks",
    "outcome", "ladder_snapshot_age_s",
})


def test_the_workflow_does_not_swallow_a_crash():
    """A money-moving step that reports success after dying is worse than a
    failing one."""
    wf = open(".github/workflows/gemini-btc5m-underdog.yml",
              encoding="utf-8").read()
    live = wf.split("BTC5m underdog -- live bounded cohort")[1]
    live = live.split("- name: Preserve the evidence")[0]
    code = "\n".join(ln for ln in live.splitlines()
                     if not ln.strip().startswith("#"))
    assert "|| echo" not in code and "|| true" not in code, code
    assert "if: always()" in wf


def test_work_window_has_no_use_before_assignment():
    """RUN 2's crash, as a test.

    The run placed a live order, confirmed it FILLED, and THEN died of
    `NameError: name 'att' is not defined` -- because a str.replace patch of
    mine silently no-op'd (no match) while a second patch that referenced its
    variable did apply. A non-matching patch must never again pass silently,
    and the function must not reference a name it never binds.
    """
    import ast
    import builtins
    import inspect
    from scripts import gemini_btc5m_underdog_run as R

    fn = ast.parse(inspect.getsource(R.work_window)).body[0]
    bound = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                bound |= {n.id for n in ast.walk(t)
                          if isinstance(n, ast.Name)}
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            bound |= {n.id for n in ast.walk(node.target)
                      if isinstance(n, ast.Name)}
        elif isinstance(node, ast.comprehension):
            bound |= {n.id for n in ast.walk(node.target)
                      if isinstance(n, ast.Name)}
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bound |= {n.id for n in ast.walk(node.target)
                      if isinstance(n, ast.Name)}
        elif isinstance(node, ast.withitem) and node.optional_vars:
            bound |= {n.id for n in ast.walk(node.optional_vars)
                      if isinstance(n, ast.Name)}
    used = {n.id for n in ast.walk(fn)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    unbound = sorted(u for u in used - bound
                     if not hasattr(builtins, u) and not hasattr(R, u))
    assert not unbound, ("work_window reads names it never binds: %r"
                         % unbound)


def test_the_runner_attributes_with_this_cohorts_own_ledger():
    import inspect
    from scripts import gemini_btc5m_underdog_run as R

    src = inspect.getsource(R.work_window)
    assert "BTC5M-CANARY-1" not in src, "still labelled as the canary cohort"
    assert "C.attribute_fills" in src
