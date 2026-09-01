from decimal import Decimal as D

import src.gemini_l5_incremental_live_q1 as m


def test_acceptable_model_requires_band_and_nonnegative_edge():
    assert m.acceptable_model({"yes_quote":"0.40","no_quote":"0.55","modeled_edge":"0.03"})
    assert not m.acceptable_model({"yes_quote":"0.19","no_quote":"0.55","modeled_edge":"0.03"})
    assert not m.acceptable_model({"yes_quote":"0.40","no_quote":"0.55","modeled_edge":"-0.01"})


def test_armed_requires_dual_key(monkeypatch):
    monkeypatch.setenv(m.LIVE_ENV, "YES")
    monkeypatch.setenv(m.SESSION_ENV, "abc123")
    monkeypatch.setenv(m.ARM_ENV, "wrong")
    assert not m.armed()
    monkeypatch.setenv(m.ARM_ENV, "abc123")
    assert m.armed()


def test_quarantine_parse(monkeypatch):
    monkeypatch.setenv(m.QUARANTINE_ENV, "foo, Bar ,,baz")
    assert m._quarantine() == {"FOO","BAR","BAZ"}


def test_hard_bounds_are_small():
    assert m.MAX_CYCLES_PER_FAMILY == 2
    assert m.MAX_WORK_SECONDS <= 90
    assert m.MAX_NEW_RESIDUAL_NOTIONAL <= D("1.50")
    assert m.MIN_PRICE >= D("0.20")
    assert m.MAX_PRICE <= D("0.80")
