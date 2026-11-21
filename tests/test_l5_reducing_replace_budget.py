"""A re-placed completion may be charged to COMPLETE once REPLACE is spent.

THE FAILURE, order level, 2026-10-30 to 2026-11-04. Every re-placement of a
leg is classed REPLACE whatever its direction
(`l5_live_execution.submission_class`: `attempt > 0 -> "REPLACE"`), and
`class_claim_or_refuse` spent a REPLACE ticket for it. Once the campaign's
REPLACE budget was gone, the leg that would have completed a pair could not
be sent again. In nine settled windows one first leg filled and the
completing leg was then refused REPLACE_BUDGET_EXHAUSTED 1,058 times, with a
pair of 0.95 or less planned each time; COMPLETE_BUDGET_EXHAUSTED was never
logged, and all nine settled unmatched
(analysis/l5/completion_decline_20261111.py, section 3).

`L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE=ON` (default off): a REPLACE
order that only REDUCES unmatched inventory, finding REPLACE exhausted,
takes one COMPLETE ticket instead -- if that budget is configured and has
room. Nothing else changes: no budget is raised, an order that is not
risk-reducing never takes this path, and the overall submission bound and
every other gate still apply.
"""
from __future__ import annotations

import pytest

from src import l5_live_authorization as AUTH

EXP = "reducing-replace-test"
ENV = AUTH.REDUCING_REPLACE_USES_COMPLETE_ENV


@pytest.fixture
def led(tmp_path, monkeypatch):
    path = str(tmp_path / "auth.jsonl")
    monkeypatch.setenv(AUTH.LEDGER_ENV, path)
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, EXP)
    for env in AUTH.CLASS_BUDGET_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    monkeypatch.delenv(ENV, raising=False)
    return path


def budgets(monkeypatch, replace="1", complete="2", open_="5"):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], replace)
    if complete is not None:
        monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], complete)
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], open_)


def spent(led, klass):
    return len(AUTH.class_spent(led, EXP, klass))


def claim(led, reducing):
    return AUTH.class_claim_or_refuse(led, EXP, "REPLACE", reducing=reducing)


@pytest.mark.parametrize("raw", [None, "", "on", "1", "ON ", "COMPLETE"])
def test_only_the_exact_token_turns_it_on(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, raw)
    assert AUTH.reducing_replace_uses_complete() is False


def test_off_reproduces_it_the_completion_is_refused_with_room_in_complete(
        led, monkeypatch):
    budgets(monkeypatch)
    assert claim(led, True)["klass"] == "REPLACE"
    with pytest.raises(AUTH.Refused) as e:
        claim(led, True)
    assert e.value.args[0] == "REPLACE_BUDGET_EXHAUSTED"
    assert spent(led, "COMPLETE") == 0             # two tickets, untouched


def test_on_the_completion_takes_a_complete_ticket(led, monkeypatch):
    budgets(monkeypatch)
    monkeypatch.setenv(ENV, "ON")
    assert claim(led, True)["klass"] == "REPLACE"  # REPLACE is used first
    t = claim(led, True)
    assert t["klass"] == "COMPLETE" and t["charged_to"] == "COMPLETE"
    assert t["because"] == "REPLACE_BUDGET_EXHAUSTED_AND_RISK_REDUCING"
    assert (spent(led, "REPLACE"), spent(led, "COMPLETE")) == (1, 1)


def test_on_it_stops_when_complete_is_spent_too(led, monkeypatch):
    budgets(monkeypatch, replace="1", complete="2")
    monkeypatch.setenv(ENV, "ON")
    for _ in range(3):
        claim(led, True)
    with pytest.raises(AUTH.Refused) as e:
        claim(led, True)
    assert e.value.args[0] == "COMPLETE_BUDGET_EXHAUSTED"
    assert (spent(led, "REPLACE"), spent(led, "COMPLETE")) == (1, 2)


def test_on_an_order_that_is_not_risk_reducing_is_still_refused(
        led, monkeypatch):
    budgets(monkeypatch)
    monkeypatch.setenv(ENV, "ON")
    claim(led, False)
    with pytest.raises(AUTH.Refused) as e:
        claim(led, False)
    assert e.value.args[0] == "REPLACE_BUDGET_EXHAUSTED"
    assert spent(led, "COMPLETE") == 0


def test_on_with_no_complete_budget_configured_it_is_still_refused(
        led, monkeypatch):
    """An unconfigured budget is not an unlimited one to borrow from."""
    budgets(monkeypatch, complete=None)
    monkeypatch.setenv(ENV, "ON")
    claim(led, True)
    with pytest.raises(AUTH.Refused) as e:
        claim(led, True)
    assert e.value.args[0] == "REPLACE_BUDGET_EXHAUSTED"


def test_on_a_complete_budget_of_zero_permits_nothing(led, monkeypatch):
    budgets(monkeypatch, complete="0")
    monkeypatch.setenv(ENV, "ON")
    claim(led, True)
    with pytest.raises(AUTH.Refused) as e:
        claim(led, True)
    assert e.value.args[0] == "COMPLETE_BUDGET_EXHAUSTED"


def test_on_the_opening_budget_is_never_borrowed(led, monkeypatch):
    budgets(monkeypatch, replace="0", complete="0", open_="5")
    monkeypatch.setenv(ENV, "ON")
    with pytest.raises(AUTH.Refused):
        claim(led, True)
    assert spent(led, "OPEN") == 0


def test_on_the_tickets_are_durable(led, monkeypatch):
    budgets(monkeypatch)
    monkeypatch.setenv(ENV, "ON")
    claim(led, True)
    claim(led, True)
    # a restart reads the same ledger: one of each is still spent
    assert (spent(led, "REPLACE"), spent(led, "COMPLETE")) == (1, 1)
    rep = AUTH.class_budget_report(led, EXP)
    assert rep["REPLACE"]["remaining"] == 0
    assert rep["COMPLETE"]["remaining"] == 1


def test_on_the_accumulation_path_carries_it(led, monkeypatch):
    budgets(monkeypatch, replace="1", complete="1")
    monkeypatch.setenv(ENV, "ON")
    market = "cpc-btc-updown-15m-2099-01-01-0000z"
    monkeypatch.setattr(AUTH, "refuse_unauthorized_window", lambda m: None)
    a = AUTH.claim_accumulation_leg(market, 0, "UP", {"x": 1}, attempt=1,
                                    klass="REPLACE", reducing=True)
    b = AUTH.claim_accumulation_leg(market, 0, "UP", {"x": 1}, attempt=2,
                                    klass="REPLACE", reducing=True)
    assert a["class_ticket"] != b["class_ticket"]
    assert (spent(led, "REPLACE"), spent(led, "COMPLETE")) == (1, 1)
    with pytest.raises(AUTH.Refused):
        AUTH.claim_accumulation_leg(market, 0, "UP", {"x": 1}, attempt=3,
                                    klass="REPLACE", reducing=True)
