"""The 2026-09-16 failure, reproduced step by step, and refused at every step.

These are not unit tests of a helper. Each one re-creates a thing that actually
happened -- a stale September-6 approval continuing a September-16 campaign, a
COMPLETE leg spending past an exhausted envelope, a restart forgetting what had
been spent -- and asserts the system now says no.

The spend figures are the real ones: $0.05 authorized, $7.50 substituted,
$19.3295 actually spent on aec-itfme-conbra-andpet.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

#: These files test the mandate itself, so the suite's permissive stand-in
#: mandate must never be what makes them pass.
pytestmark = pytest.mark.real_mandate_env

from src import pmus_live_mandate as M

NOW = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
R1 = {
    "authorization_id": "R1-2026-09-16-01",
    "authorized_at": "2026-09-16T05:55:00Z",
    "service": "l5-r1-probe",
    "strategy": "LANE_A_EXECUTION_MEASUREMENT_PROBE",
    "strategy_version": "V0",
    "max_markets": 1,
    "max_total_markets": 1,
    "max_spend_per_market": "0.05",
    "max_total_spend": "0.05",
    "max_residual": "0.06",
    "max_daily_realized_loss": "1.00",
    "expiry": "2026-09-16T23:59:00Z",
}


def mandate(**over):
    m, why = M.parse_mandate({**R1, **over})
    assert m is not None, why
    return m


def buy(slug="mkt-1", side="OUTCOME_SIDE_YES", px="0.35", qty="0.02",
        action="ORDER_ACTION_BUY"):
    """An order body in the venue's own encoding: price.value is the LONG
    side's price, so a NO leg at 0.43 goes on the wire as 0.57."""
    wire = px if side.endswith("YES") else str(D("1") - D(px))
    return {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
            "price": {"value": wire}, "quantity": float(qty),
            "outcomeSide": side, "action": action}


def terminal(d: Path, order_id: str, filled_qty: str, price: str = "0.35"):
    """A venue reconciliation row -- the authoritative fill evidence."""
    with (d / "ledger.jsonl").open("a") as fh:
        fh.write(json.dumps({"kind": "order_terminal", "order_id": order_id,
                             "filled_qty": filled_qty, "price": price}) + "\n")


def settle(d: Path, slug: str):
    with (d / "ledger.jsonl").open("a") as fh:
        fh.write(json.dumps({"kind": "settlement", "slug": slug,
                             "settled": True, "realized_usd": "-0.0214"}) + "\n")


class FakeVenue:
    """Accepts everything. The venue is not the safety mechanism."""

    def __init__(self):
        self.orders = []
        self.n = 0

    def request(self, method, path, body=None, **kw):
        if method == "POST" and path == "/v1/orders":
            self.n += 1
            self.orders.append(body)
            return {"id": f"ORD{self.n}"}
        return {}


def submit(g, body):
    """Try one order; return the order id or the refusal reason."""
    try:
        return g.request("POST", "/v1/orders", body)["id"]
    except M.MandateRefusal as exc:
        return exc.verdict["reason"]


# ==========================================================================
# 1-2. R1 authorized for ONE market at $0.05; the first market settles

def test_1_and_2_one_market_at_five_cents_then_it_settles(tmp_path):
    v, m = FakeVenue(), mandate()
    g = M.guard(v, m, tmp_path)
    oid = submit(g, buy(px="0.35", qty="0.02"))
    assert oid == "ORD1"
    terminal(tmp_path, oid, "0.02")
    settle(tmp_path, "mkt-1")
    used = M.consumption(tmp_path, m.authorization_id)
    assert D(used["per_market"]["mkt-1"]) == D("0.0070")
    assert used["markets_open"] == []          # settled, but still touched
    assert used["n_markets_touched"] == 1


# ==========================================================================
# 3-5. stale R2/R3 approvals exist, continuation is attempted, and refused

def test_3_4_5_stale_september_6_approvals_cannot_continue_a_september_16_run(
        tmp_path, monkeypatch, capsys):
    """THE ACTUAL INCIDENT. R2C/R2C2/R3L were approved on 2026-09-06 and were
    handed to a campaign armed on 2026-09-16 the moment market #1 settled."""
    from src import pmus_l5_continuous_session as cs
    stale = [
        {"campaign_id": "R2C-RUN-2026-09-06", "stop_rule": "rung_markets",
         "rung": "R2C", "approved_utc": "2026-09-06T17:16:39Z"},
        {"campaign_id": "R3L-RUN-2026-09-06", "stop_rule": "rung_markets",
         "rung": "R3L", "approved_utc": "2026-09-06T19:05:29Z"},
    ]
    # without a mandate the old behaviour is preserved and it DOES continue --
    # pinned so the fix is shown to be the mandate, not an unrelated change
    monkeypatch.setattr(M, "load_mandate", lambda env=None: (None, "no_mandate"))
    assert cs.approved_continuation("R1-INITIAL", [], approvals=stale) is not None

    # with the R1 mandate active, continuation is refused and says so
    monkeypatch.setattr(M, "load_mandate", lambda env=None: (mandate(), None))
    assert cs.approved_continuation("R1-INITIAL", [], approvals=stale) is None
    out = capsys.readouterr().out
    assert "campaign_continuation_refused_by_mandate" in out
    assert "R1-2026-09-16-01" in out


def test_5b_even_if_continuation_ran_the_wire_still_refuses_market_two(tmp_path):
    """Belt and braces: suppose something DID continue the campaign. The
    mandate is enforced at the wire, so market #2 cannot be traded anyway."""
    v, m = FakeVenue(), mandate()
    g = M.guard(v, m, tmp_path)
    terminal(tmp_path, submit(g, buy(slug="mkt-1")), "0.02")
    settle(tmp_path, "mkt-1")
    assert submit(g, buy(slug="mkt-2")) == "max_total_markets_exhausted"
    assert len(v.orders) == 1


def test_5c_a_rung_raising_the_default_cannot_raise_the_mandate(tmp_path, monkeypatch):
    """`_apply_rung` moved the per-market default $0.05 -> $7.50. The session's
    own cap now asks the mandate first, so the rung cannot lift it."""
    from src import pmus_spring_l5_session as base
    from src import pmus_micro_executor as micro
    monkeypatch.setattr(micro, "per_market_first_leg_cap", lambda: D("7.50"))
    monkeypatch.setattr(M, "load_mandate", lambda env=None: (mandate(), None))
    assert base.session_notional_cap() == D("0.05")

    # and the reverse still holds: a TIGHTER rung under a roomier mandate
    # binds, because the effective envelope is the tightest of the two
    monkeypatch.setattr(micro, "per_market_first_leg_cap", lambda: D("0.01"))
    assert base.session_notional_cap() == D("0.01")


# ==========================================================================
# 6-7. a COMPLETE after $0.04 spent gets only the remaining authorized dollars

def test_6_and_7_complete_consumes_the_envelope_and_is_cut_off_by_it(tmp_path):
    """The exempt stage. A COMPLETE is a BUY of the complement, so it spends.
    After $0.042 is gone, a $0.0225 complete does not fit in the remaining
    $0.008 and is refused rather than trimmed silently."""
    v, m = FakeVenue(), mandate()
    g = M.guard(v, m, tmp_path)
    terminal(tmp_path, submit(g, buy(px="0.35", qty="0.12")), "0.12")   # $0.042
    used = M.consumption(tmp_path, m.authorization_id)
    assert D(used["per_market"]["mkt-1"]) == D("0.0420")

    # a NO leg at 0.45 for 0.05 shares = $0.0225 -- more than the $0.008 left
    r = submit(g, buy(side="OUTCOME_SIDE_NO", px="0.45", qty="0.05"))
    assert r == "max_spend_per_market_exhausted"

    # and what DOES fit is admitted: 0.01 shares at 0.45 = $0.0045
    assert submit(g, buy(side="OUTCOME_SIDE_NO", px="0.45", qty="0.01")) == "ORD2"


def test_7b_a_no_leg_is_priced_in_no_terms_not_off_the_wire_value(tmp_path):
    """The wire carries 1-X for a NO order. Charging the wire value would have
    priced this incident's completes wrong in both directions."""
    cash, qty, err = M.order_cash_effect(
        buy(side="OUTCOME_SIDE_NO", px="0.43", qty="10"))
    assert err is None and qty == D("10")
    assert cash == D("4.30")            # 10 x 0.43, NOT 10 x 0.57


def test_7c_no_stage_name_exists_that_escapes_the_envelope():
    """There is no allow-list left to forget to extend."""
    from src import pmus_spring_l5_session as base
    for stage in ("spring_open_1", "spring_extend_4", "spring_complete_7",
                  "complement_acceptance_flat", "micro_first_leg",
                  "some_stage_invented_next_year", "", None):
        assert base.counts_against_market_cap(stage) is True


# ==========================================================================
# 8. partial fills / retries / cancels cannot replenish already-spent cash

def test_8_a_partial_fill_keeps_its_filled_part_charged_forever(tmp_path):
    v, m = FakeVenue(), mandate(max_spend_per_market="1.00", max_total_spend="1.00")
    g = M.guard(v, m, tmp_path)
    oid = submit(g, buy(px="0.50", qty="1.00"))            # reserves $0.50
    assert D(M.consumption(tmp_path, m.authorization_id)["per_market"]["mkt-1"]) == D("0.50")
    terminal(tmp_path, oid, "0.40")                        # 40% filled, rest cancelled
    used = M.consumption(tmp_path, m.authorization_id)
    assert D(used["per_market"]["mkt-1"]) == D("0.20")     # 0.40 x 0.50, kept
    assert D(used["per_market"]["mkt-1"]) > 0              # never returns to zero


def test_8b_retrying_a_cancelled_order_does_not_hand_back_the_filled_cash(tmp_path):
    v, m = FakeVenue(), mandate(max_spend_per_market="1.00", max_total_spend="1.00")
    g = M.guard(v, m, tmp_path)
    spent_after = []
    for _ in range(4):
        oid = submit(g, buy(px="0.50", qty="0.40"))        # $0.20 each
        terminal(tmp_path, oid, "0.20")                    # half fills each time
        spent_after.append(D(M.consumption(
            tmp_path, m.authorization_id)["per_market"]["mkt-1"]))
    assert spent_after == sorted(spent_after)              # monotonic, never down
    assert spent_after[-1] == D("0.40")                    # 4 x 0.10 actually spent


def test_8c_an_order_whose_outcome_we_never_learned_keeps_costing_full_size(tmp_path):
    """Ambiguity must not free budget. No terminal row = charged in full."""
    v, m = FakeVenue(), mandate(max_spend_per_market="1.00", max_total_spend="1.00")
    g = M.guard(v, m, tmp_path)
    submit(g, buy(px="0.50", qty="1.00"))
    assert D(M.consumption(tmp_path, m.authorization_id)["per_market"]["mkt-1"]) == D("0.50")


# ==========================================================================
# 9. a restart cannot reset the consumed mandate

def test_9_restart_re_reads_consumption_from_disk_and_cannot_forget(tmp_path):
    m = mandate()
    g1 = M.guard(FakeVenue(), m, tmp_path)
    terminal(tmp_path, submit(g1, buy(px="0.35", qty="0.14")), "0.14")   # $0.049
    del g1                                                   # process dies

    v2 = FakeVenue()                                         # brand new process
    g2 = M.guard(v2, m, tmp_path)
    assert submit(g2, buy(px="0.35", qty="0.02")) == "max_spend_per_market_exhausted"
    assert v2.orders == []


def test_9b_a_new_authorization_id_does_not_inherit_the_old_headroom(tmp_path):
    """Scoping cuts both ways: a fresh mandate starts at zero spent, and the
    old mandate's spending stays attributed to the old mandate."""
    old = mandate()
    g = M.guard(FakeVenue(), old, tmp_path)
    terminal(tmp_path, submit(g, buy(px="0.35", qty="0.14")), "0.14")
    new = mandate(authorization_id="R1-2026-09-17-01")
    assert D(M.consumption(tmp_path, new.authorization_id)["total"]) == 0
    assert D(M.consumption(tmp_path, old.authorization_id)["total"]) > 0


# ==========================================================================
# 10. a race cannot spend the same dollars twice

def test_10_concurrent_submissions_cannot_exceed_the_mandate(tmp_path):
    """Sixteen threads, each trying $0.02 against a $0.05 market envelope.
    At most two can fit. The admission critical section is what stops the
    other fourteen -- without the lock they would all read 'nothing spent'."""
    v, m = FakeVenue(), mandate()
    g = M.guard(v, m, tmp_path)
    results, lock = [], threading.Lock()

    def one():
        r = submit(g, buy(px="0.50", qty="0.04"))          # $0.02 each
        with lock:
            results.append(r)

    ts = [threading.Thread(target=one) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]

    admitted = [r for r in results if r.startswith("ORD")]
    assert len(admitted) == 2, results
    assert len(v.orders) == 2
    total = D(M.consumption(tmp_path, m.authorization_id)["per_market"]["mkt-1"])
    assert total == D("0.04") <= D(str(m.max_spend_per_market))


# ==========================================================================
# fail-closed: the mandate itself

@pytest.mark.parametrize("bad,reason", [
    ({}, "no_mandate"),
    ({"PMUS_LIVE_MANDATE": "not json"}, "mandate_unparseable_json"),
    ({"PMUS_LIVE_MANDATE": json.dumps({"authorization_id": "x"})},
     "mandate_missing_fields"),
])
def test_an_unusable_mandate_is_no_mandate(bad, reason, monkeypatch, tmp_path):
    """DECOUPLED FROM REPO STATE 2026-09-16.

    The `{}` case means "nothing is configured at all". `load_mandate` falls
    back to DEFAULT_MANDATE_FILE when the env var is absent, so this case was
    silently asserting that the repository ships no committed mandate -- true
    until the owner authorized one, and then this went red for a reason that
    has nothing to do with what it is testing.

    Pointing the default at a path that does not exist tests the MECHANISM:
    no env var and no file is no mandate, whatever the repo happens to carry.
    """
    monkeypatch.setattr(M, "DEFAULT_MANDATE_FILE", str(tmp_path / "absent.json"))
    m, why = M.load_mandate(env=bad)
    assert m is None and why.startswith(reason)


def test_the_committed_default_path_is_still_what_production_reads():
    """Guards the decoupling above: the test may repoint DEFAULT_MANDATE_FILE,
    but production must still read the committed authorization directory."""
    assert M.DEFAULT_MANDATE_FILE == "analysis/l5/authorization/live_mandate.json"


def test_an_expired_mandate_cannot_spend(tmp_path):
    past = (NOW - timedelta(hours=1)).isoformat()
    v = FakeVenue()
    g = M.guard(v, mandate(expiry=past), tmp_path)
    assert submit(g, buy()) == "mandate_expired"
    assert v.orders == []


def test_a_mandate_is_frozen_against_runtime_edits():
    m = mandate()
    with pytest.raises(Exception):
        m.max_spend_per_market = D("7.50")      # the 2026-09-16 substitution


def test_an_unpriceable_order_is_refused_rather_than_guessed(tmp_path):
    v = FakeVenue()
    g = M.guard(v, mandate(), tmp_path)
    for body in ({"marketSlug": "m", "price": {"value": "0.3"}},        # no qty
                 {"marketSlug": "m", "quantity": 1.0},                   # no price
                 {"marketSlug": "m", "price": {"value": "0.3"},
                  "quantity": 1.0}):                                     # no side
        assert submit(g, body).startswith("order_unpriceable")
    assert v.orders == []


def test_an_unrecognised_action_is_charged_at_its_worst_case(tmp_path):
    cash, _q, err = M.order_cash_effect(
        buy(action="ORDER_ACTION_SOMETHING_NEW", qty="3", px="0.10"))
    assert err is None and cash == D("3")       # full contract value, not $0.30


def test_non_order_traffic_passes_through_untouched(tmp_path):
    v = FakeVenue()
    g = M.guard(v, mandate(), tmp_path)
    assert g.request("GET", "/v1/orders/open") == {}
    assert g.request("POST", "/v1/order/preview", {"x": 1}) == {}
    assert v.orders == []
