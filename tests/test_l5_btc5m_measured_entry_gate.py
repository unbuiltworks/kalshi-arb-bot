"""The measured-rate entry gate on the BTC5m executor.

EVERY TEST IS A WAY THE GATE COULD ADMIT AN ORDER IT SHOULD REFUSE, or
refuse one it must not. The second half matters as much as the first: a
gate that also blocks completions converts held inventory into inventory
that cannot be closed.
"""
from __future__ import annotations

import json

import pytest

from src import l5_btc5m_execution as X

GATE = X.MEASURED_ENTRY_GATE_ENV
ROWS = X.MEASURED_ENTRY_ROWS_ENV
FLOOR = X.MEASURED_ENTRY_MIN_EV_ENV


def _paired(pnl="0.20"):
    return {"matched_qty": "1", "matched_pnl": pnl, "residual_qty": "0",
            "residual_cost": "0", "settlement_pnl": pnl}


def _placeholder():
    """A residual booked at exactly $1.0000 a contract."""
    return {"matched_qty": "0", "matched_pnl": "0", "residual_qty": "5",
            "residual_cost": "5.00", "settlement_pnl": "-5.00"}


def _rows_file(tmp_path, rows):
    p = tmp_path / "rows.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(p)


# ------------------------------------------------- OFF is unchanged

def test_off_by_default():
    assert X.measured_entry_gate_on() is False


@pytest.mark.parametrize("raw", ["", "0", "on", "On", "YES", "1", "true",
                                 "ENABLED", " ON"])
def test_only_the_exact_string_ON_arms_it(monkeypatch, raw):
    """Same discipline as every other gate here: a word that READS like
    enforcement is not enforcement. (' ON' strips to 'ON' and does arm.)"""
    monkeypatch.setenv(GATE, raw)
    assert X.measured_entry_gate_on() is (raw.strip() == "ON")


def test_while_off_it_refuses_nothing(monkeypatch):
    monkeypatch.delenv(GATE, raising=False)
    assert X.measured_entry_refusal() is None


# ---------------------------------------------- ON fails closed

def test_on_with_no_rows_path_refuses(monkeypatch):
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.delenv(ROWS, raising=False)
    assert X.measured_entry_refusal() == "MEASURED_ENTRY_ROWS_UNSET"


def test_on_with_a_missing_file_refuses(monkeypatch, tmp_path):
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.setenv(ROWS, str(tmp_path / "nope.jsonl"))
    assert X.measured_entry_refusal().startswith(
        "MEASURED_ENTRY_ROWS_UNREADABLE:")


def test_on_with_unparseable_rows_refuses(monkeypatch, tmp_path):
    monkeypatch.setenv(GATE, "ON")
    p = tmp_path / "bad.jsonl"
    p.write_text("{not json\n")
    monkeypatch.setenv(ROWS, str(p))
    assert X.measured_entry_refusal().startswith(
        "MEASURED_ENTRY_ROWS_UNREADABLE:")


def test_on_with_too_few_authenticated_rows_refuses(monkeypatch, tmp_path):
    """THE BTC5m CASE TODAY. One known fill is not a measurement."""
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.setenv(ROWS, _rows_file(tmp_path, [_paired()] * 3))
    assert X.measured_entry_refusal() == \
        "MEASURED_ENTRY_EV:TOO_FEW_AUTHENTICATED_MARKETS"


def test_placeholders_cannot_make_up_the_sample(monkeypatch, tmp_path):
    """Twenty $1-placeholder rows plus three real ones is still three
    authenticated markets, so it still refuses."""
    monkeypatch.setenv(GATE, "ON")
    rows = [_paired()] * 3 + [_placeholder()] * 20
    monkeypatch.setenv(ROWS, _rows_file(tmp_path, rows))
    assert X.measured_entry_refusal() == \
        "MEASURED_ENTRY_EV:TOO_FEW_AUTHENTICATED_MARKETS"


def test_a_negative_lower_bound_refuses(monkeypatch, tmp_path):
    """A positive MEAN on a noisy sample is not an edge."""
    rows = []
    for i in range(30):
        if i % 2 == 0:
            rows.append(_paired("1.05"))
        else:
            rows.append({"matched_qty": "0", "matched_pnl": "0",
                         "residual_qty": "1", "residual_cost": "0.5",
                         "settlement_pnl": "-1.00"})
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.setenv(ROWS, _rows_file(tmp_path, rows))
    assert X.measured_entry_refusal() == \
        "MEASURED_ENTRY_EV:EV_LOWER_NOT_ABOVE_MIN"


def test_a_consistent_measured_edge_admits(monkeypatch, tmp_path):
    """The gate is not always-refuse -- which is what makes every
    refusal above meaningful."""
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.setenv(ROWS, _rows_file(tmp_path, [_paired("0.20")] * 30))
    assert X.measured_entry_refusal() is None


def test_the_floor_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.setenv(ROWS, _rows_file(tmp_path, [_paired("0.20")] * 30))
    monkeypatch.setenv(FLOOR, "0.50")
    assert X.measured_entry_refusal() == \
        "MEASURED_ENTRY_EV:EV_LOWER_NOT_ABOVE_MIN"


def test_the_real_btc15m_tape_refuses(monkeypatch):
    """The measured proxy today: ev_lower -$0.0750. ON must refuse it."""
    import os
    tape = os.path.join("data", "l5", "ops",
                        "btc15m_settled_20261012_20261105.jsonl")
    if not os.path.exists(tape):
        pytest.skip("tape absent")
    rows = []
    with open(tape) as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("retired") and str(r.get("market", "")).startswith(
                    "cpc-btc-updown-15m"):
                rows.append(r)
    p = "/tmp/_b15_rows.jsonl"
    with open(p, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.setenv(ROWS, p)
    assert X.measured_entry_refusal() == \
        "MEASURED_ENTRY_EV:EV_LOWER_NOT_ABOVE_MIN"


def test_it_never_raises_whatever_the_inputs(monkeypatch, tmp_path):
    """A gate that crashes is a gate that cannot refuse."""
    monkeypatch.setenv(GATE, "ON")
    for bad in ("", "/dev/null", str(tmp_path), "/proc/self/mem"):
        monkeypatch.setenv(ROWS, bad)
        out = X.measured_entry_refusal()
        assert out is None or isinstance(out, str)


# ------------------------- it gates OPENINGS, never risk reduction

class _Inv:
    """Minimal executor stand-in for the structural helper.

    The shape is the one `Btc5mExecutor.inventory` really returns --
    {side: {"qty", "cost"}} -- NOT the tuples `_read_inventory` produces
    internally. Writing the fixture in the internal shape made
    `C.exposure` return None and the helper read every order as
    risk-adding, which is the fail-closed direction and so looked like a
    code bug rather than a fixture bug.
    """

    def __init__(self, held, qty="5", cost="2.50"):
        from src import l5_btc5m_controller as C
        self._inv = {C.UP: {"qty": "0", "cost": "0"},
                     C.DOWN: {"qty": "0", "cost": "0"}}
        if held is not None:
            self._inv[held] = {"qty": qty, "cost": cost}

    def inventory(self, market):
        return self._inv


def _reduces(held, side):
    from src import l5_btc5m_controller as C
    ex = _Inv(held)
    return X.Btc5mExecutor._reduces_exposure(ex, "m", side, "1")


def test_buying_the_complement_of_a_held_leg_reduces_exposure():
    from src import l5_btc5m_controller as C
    assert _reduces(C.UP, C.DOWN) is True
    assert _reduces(C.DOWN, C.UP) is True


def test_buying_more_of_the_held_side_does_not_reduce_exposure():
    from src import l5_btc5m_controller as C
    assert _reduces(C.UP, C.UP) is False
    assert _reduces(C.DOWN, C.DOWN) is False


def test_with_no_inventory_every_order_is_an_opening():
    from src import l5_btc5m_controller as C
    assert _reduces(None, C.UP) is False
    assert _reduces(None, C.DOWN) is False


def test_an_unreadable_inventory_is_treated_as_risk_adding():
    """Fail closed: an order of unknown effect must pass the gate."""
    class Broken:
        def inventory(self, market):
            raise RuntimeError("boom")
    assert X.Btc5mExecutor._reduces_exposure(
        Broken(), "m", "UP", "1") is False


def test_a_missing_module_is_reported_as_such_not_as_a_rows_problem(
        monkeypatch, tmp_path):
    """WHAT THE IMAGE-DEPS TEST CAUGHT. The module was not in
    deploy/l5_btc5m_live/Dockerfile, so ON would have refused with
    MEASURED_ENTRY_ROWS_UNREADABLE:ImportError -- safe, but it sends an
    operator to the rows file for a problem in the image.

    `from src import l5_measured_entry_rates` resolves via the ATTRIBUTE
    on the already-imported `src` package before it consults sys.modules,
    so a None there alone is not enough under pytest (where sibling tests
    have already imported it). Both the attribute and the sys.modules
    entry have to go -- which is exactly why my first attempt passed
    standalone and failed in the suite.
    """
    import sys
    import src as src_pkg
    monkeypatch.delattr(src_pkg, "l5_measured_entry_rates", raising=False)
    monkeypatch.setitem(sys.modules, "src.l5_measured_entry_rates", None)
    monkeypatch.setenv(GATE, "ON")
    monkeypatch.setenv(ROWS, _rows_file(tmp_path, [_paired()] * 30))
    #: the concrete class is ModuleNotFoundError (an ImportError
    #: subclass), which is also what a missing COPY raises in the image.
    #: The CATEGORY is the property worth pinning, not the class name.
    out = X.measured_entry_refusal()
    assert out.startswith("MEASURED_ENTRY_MODULE_UNAVAILABLE:"), out
    assert "ROWS_UNREADABLE" not in out
