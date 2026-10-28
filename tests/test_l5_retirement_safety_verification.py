"""Final safety verification of the round-retirement correction (cdf99c35).

Five properties, each driven through a real `FaithfulExecutor` with a real
`InventoryStore` against a fake venue. Nothing here inspects source text.

The correction changed `probe_round_is_safe_to_retire` from blocking on ANY
filled quantity to blocking on UNMATCHED quantity. These tests exist to
prove that it unblocked exactly the deadlock and nothing else.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_live_validation_gates as VG
from src import l5_live_authorization as AUTH
from src import l5_campaign_approval as CA
from tests import test_l5_live_execution as H

SLUG = "cpc-btc-updown-15m-2026-09-23-1615z"
PLAN = {"pair_cost": "0.9500",
        "legs": [{"side": "UP", "price": "0.6000", "qty": "1.87",
                  "mode": "MAKER"},
                 {"side": "DOWN", "price": "0.3500", "qty": "1.87",
                  "mode": "MAKER"}]}


def book(bid="0.4300", ask="0.4400", age=0.05):
    return {"bid": bid, "ask": ask, "stream_age_s": age,
            "bid_shares": "500", "ask_shares": "500"}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "EXP-VERIFY")
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "TS-VERIFY")
    monkeypatch.setenv("L5_WINDOW_STRATEGY", "TWO_SIDED")
    monkeypatch.setenv("L5_TS_MARKET", SLUG)
    monkeypatch.delenv("L5_FRESH_BOOK_REQUOTE", raising=False)
    monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    for k, v in (("L5_LIVE_AUTH_MAX_OPEN", "40"),
                 ("L5_LIVE_AUTH_MAX_COMPLETE", "40"),
                 ("L5_LIVE_AUTH_MAX_REPLACE", "64"),
                 ("L5_LIVE_AUTH_MAX_SUBMISSIONS", "40")):
        monkeypatch.setenv(k, v)
    t = H.FakeTransport(fill_all=True)
    t.tick_size = D("0.01")
    st = H.store(tmp_path)
    ex = E.FaithfulExecutor(st, H.limits(), transport=t, now_fn=H.NOW,
                            maker_only=True)
    ex.validation_gates = VG.ValidationGates(max_cash_deployed_usd=D("12"))
    ex.available_cash_usd = D("300")
    ex.probe_store_plan(SLUG, PLAN, 0)
    return {"ex": ex, "t": t, "store": st, "mp": monkeypatch}


def fill(rig_, side, qty, px):
    rig_["store"].apply_fill(SLUG, side, D(qty), D(px), basis="OWN")


def matched(rig_):
    fill(rig_, "UP", "1.87", "0.6100")
    fill(rig_, "DOWN", "1.87", "0.3300")


# ============ 1. a matched, zero-residual round retires and frees its slot
def test_a_matched_zero_residual_round_may_retire(rig):
    matched(rig)
    assert rig["store"].residual(SLUG) == D(0)
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) is None


def test_retiring_advances_the_round_slot(rig):
    """"Releases its allocation" means the round SLOT advances, so the next
    allocation of the cash envelope becomes usable."""
    matched(rig)
    assert rig["ex"].probe_round(SLUG) == 0
    rig["ex"].probe_retire_round(SLUG, 0, "LEG_WOULD_CROSS:UP")
    assert rig["ex"].probe_round(SLUG) == 1
    assert rig["ex"].probe_round_retired(SLUG, 0) is True


def test_retirement_does_NOT_refund_committed_cash(rig):
    """NO_RECYCLING is unchanged: committed cash is released by SETTLEMENT,
    never by retiring a round. Stating this explicitly because "releases its
    allocation" could otherwise be read as a refund."""
    matched(rig)
    before = dict(rig["store"].state.get("committed") or {})
    rig["ex"].probe_retire_round(SLUG, 0, "why")
    assert dict(rig["store"].state.get("committed") or {}) == before


def test_retirement_keeps_the_matched_inventory(rig):
    matched(rig)
    rig["ex"].probe_retire_round(SLUG, 0, "why")
    assert rig["store"].position(SLUG, "UP") == D("1.87")
    assert rig["store"].position(SLUG, "DOWN") == D("1.87")


def test_retirement_deletes_no_claim(rig):
    matched(rig)
    AUTH.claim_accumulation_leg(SLUG, 0, "DOWN", {"n": 1}, klass="OPEN")
    before = len(AUTH.submissions_spent(AUTH.ledger_path(),
                                        AUTH.experiment_id()))
    rig["ex"].probe_retire_round(SLUG, 0, "why")
    assert len(AUTH.submissions_spent(AUTH.ledger_path(),
                                      AUTH.experiment_id())) == before


# ============ 2. a genuinely imbalanced round cannot retire
@pytest.mark.parametrize("side", ["UP", "DOWN"])
def test_a_one_sided_holding_blocks_retirement(rig, side):
    fill(rig, side, "1.87", "0.5000")
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "LEG_HOLDS_UNMATCHED:%s" % side


def test_a_partial_complement_still_blocks_retirement(rig):
    fill(rig, "UP", "1.87", "0.6100")
    fill(rig, "DOWN", "0.50", "0.3300")
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "LEG_HOLDS_UNMATCHED:UP"


def test_the_smallest_representable_imbalance_blocks_retirement(rig):
    fill(rig, "UP", "1.87", "0.6100")
    fill(rig, "DOWN", "1.86", "0.3300")
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "LEG_HOLDS_UNMATCHED:UP"


# ============ 3. retirement cannot orphan anything the venue may still hold
def test_a_resting_order_blocks_retirement_even_when_matched(rig):
    matched(rig)
    rig["ex"].store.live_order_count = lambda: 1
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "ORDER_STILL_LIVE"


def test_an_unverified_cancellation_blocks_retirement_even_when_matched(rig):
    """A cancel that could not be verified parks in `pending_terminal`,
    which `still_fillable_order_count` counts -- so it blocks."""
    matched(rig)
    rig["ex"].store.live_order_count = lambda: 0
    rig["ex"].store.still_fillable_order_count = lambda: 1
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "ORDER_STILL_FILLABLE"


def test_an_unconfirmed_fill_blocks_retirement_even_when_matched(rig):
    """Same path: absent from the venue's open orders includes FILLED, and
    until an authoritative read books the final cumQuantity the order is
    still fillable."""
    matched(rig)
    rig["ex"].store.live_order_count = lambda: 0
    rig["ex"].store.still_fillable_order_count = lambda: 2
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "ORDER_STILL_FILLABLE"


def test_order_safety_is_checked_BEFORE_the_balance(rig):
    """Order state must win over a balanced book, or a matched round could
    walk away from an order the venue still holds."""
    matched(rig)
    rig["ex"].store.live_order_count = lambda: 1
    rig["ex"].store.still_fillable_order_count = lambda: 1
    assert rig["ex"].probe_round_is_safe_to_retire(SLUG, 0) == \
        "ORDER_STILL_LIVE"


@pytest.mark.parametrize("attr", ["live_order_count",
                                  "still_fillable_order_count",
                                  "position"])
def test_unreadable_state_forbids_retirement(rig, attr):
    matched(rig)

    def boom(*_a, **_k):
        raise RuntimeError("store exploded")

    setattr(rig["ex"].store, attr, boom)
    why = rig["ex"].probe_round_is_safe_to_retire(SLUG, 0)
    assert why is not None and why.startswith("STATE_UNREADABLE")


def test_retirement_does_not_touch_the_uncertainty_latch(rig):
    matched(rig)
    rig["store"].mark_uncertain("a prior ambiguous cancel")
    rig["ex"].probe_retire_round(SLUG, 0, "why")
    assert rig["store"].state.get("orders_uncertain")


# ============ 4. reconciliation, loss limits and the watchdog are intact
def test_retirement_writes_only_an_additive_marker(rig):
    """Anything else would be a way for a lifecycle transition to move money
    or inventory state."""
    matched(rig)
    before = {k: json.dumps(v, sort_keys=True, default=str)
              for k, v in rig["store"].state.items()}
    rig["ex"].probe_retire_round(SLUG, 0, "why")
    after = {k: json.dumps(v, sort_keys=True, default=str)
             for k, v in rig["store"].state.items()}
    changed = {k for k in set(before) | set(after)
               if before.get(k) != after.get(k)}
    assert changed == {"probe_rounds_retired"}, changed


def test_the_loss_ceilings_do_not_consult_round_retirement():
    import inspect
    src = inspect.getsource(VG)
    for token in ("probe_round", "rounds_retired", "retire"):
        assert token not in src, token


def test_the_campaign_baseline_does_not_consult_round_retirement():
    import inspect
    from src import l5_campaign_baseline as CBL
    src = inspect.getsource(CBL)
    for token in ("probe_round", "rounds_retired", "retire"):
        assert token not in src, token


def test_the_halt_gate_is_independent_of_retirement(rig):
    """The watchdog's verdict must not move when a round retires."""
    calls = []
    rig["ex"].halt_gate = type("G", (), {
        "permitted": lambda _s: (calls.append(1), True)[1]})()
    matched(rig)
    rig["ex"].probe_retire_round(SLUG, 0, "why")
    assert calls == [], "retirement consulted the halt gate"


def test_retirement_is_idempotent_and_keeps_its_first_reason(rig):
    matched(rig)
    rig["ex"].probe_retire_round(SLUG, 0, "FIRST")
    rig["ex"].probe_retire_round(SLUG, 0, "SECOND")
    reg = rig["store"].state["probe_rounds_retired"]
    assert reg["%s|0" % SLUG]["why"] == "FIRST"


def test_retirement_survives_a_restart(rig, tmp_path):
    matched(rig)
    rig["ex"].probe_retire_round(SLUG, 0, "why")
    reopened = E.InventoryStore(rig["store"].path)
    ex2 = E.FaithfulExecutor(reopened, H.limits(),
                             transport=rig["t"], now_fn=H.NOW)
    assert ex2.probe_round_retired(SLUG, 0) is True
    assert ex2.probe_round(SLUG) == 1


# ============ 5. campaign identity: durable and isolated
def _doc(nonce, hours=2.0):
    now = dt.datetime.now(dt.timezone.utc)
    return json.dumps({
        "approved": True, "nonce": nonce, "series": "cpc-btc-updown-15m-",
        "issued_utc": now.isoformat(),
        "expires_utc": (now + dt.timedelta(hours=hours)).isoformat(),
        "max_windows": 4, "max_total_exposure_usd": "20",
        "max_daily_loss_usd": "5"})


def test_the_campaign_identity_is_stable_across_a_restart(rig):
    rig["mp"].setenv("L5_CAMPAIGN_APPROVAL", _doc("a" * 64))
    first = AUTH.experiment_id()
    rig["mp"].setenv("L5_CAMPAIGN_APPROVAL", _doc("a" * 64))   # restart
    assert AUTH.experiment_id() == first
    assert ".camp." in first


def test_two_authorizations_do_not_share_a_budget(rig):
    rig["mp"].setenv("L5_CAMPAIGN_APPROVAL", _doc("a" * 64))
    a = AUTH.experiment_id()
    for _ in range(3):
        AUTH.claim_class_ticket(AUTH.ledger_path(), a, "OPEN")
    rig["mp"].setenv("L5_CAMPAIGN_APPROVAL", _doc("b" * 64))
    b = AUTH.experiment_id()
    assert b != a
    assert AUTH.class_spent(AUTH.ledger_path(), b, "OPEN") == []


def test_the_bare_id_still_counts_every_campaign(rig):
    for n in ("a" * 64, "b" * 64):
        rig["mp"].setenv("L5_CAMPAIGN_APPROVAL", _doc(n))
        AUTH.claim_class_ticket(AUTH.ledger_path(),
                                AUTH.experiment_id(), "OPEN")
    rig["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    assert len(AUTH.class_spent(AUTH.ledger_path(),
                               AUTH.experiment_id(), "OPEN")) == 2


def test_an_expired_authorization_cannot_open_a_fresh_budget(rig):
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=9)
    doc = json.loads(_doc("a" * 64))
    doc["issued_utc"] = past.isoformat()
    doc["expires_utc"] = (past + dt.timedelta(hours=1)).isoformat()
    rig["mp"].setenv("L5_CAMPAIGN_APPROVAL", json.dumps(doc))
    assert AUTH.experiment_id() == "EXP-VERIFY"      # the bare id


# ============ the requote stays off
def test_the_requote_is_disabled_by_default(rig):
    assert E.FaithfulExecutor.fresh_book_requote_enabled() is False


def test_a_matched_round_resolves_without_the_requote(rig):
    matched(rig)
    rec = rig["ex"].handle(
        H.row(side="UP", l5px="0.46", bid="0.43", ask="0.44"),
        book(), market=SLUG, window="16:15")
    assert rec.get("FRESH_BOOK_REQUOTE") is None
    assert rec["TWO_SIDED_ROUND"]["retired"] is True
    assert rig["t"].sent
    assert D(str(rig["t"].sent[-1]["price"])) == D("0.41")
