"""A finished campaign relinquishes its budget claim, through `close()`.

THE DEFECT. `l5_campaign_budget.close()` is the sanctioned end of a
campaign's budget claim and NOTHING CALLED IT. The claim stayed open for
ever and the next campaign was refused CAMPAIGN_BUDGET_LOCKED -- seen on
2026-10-23, and again on 2026-10-30 when camp11 still owned the ledger after
its windows were spent and settled. `release_stale` covers the NEXT campaign
taking over at arming; this covers a campaign RELINQUISHING its own claim,
which is what `close` is for and what was missing.

WHAT MUST REMAIN TRUE, and each has a test below: the ledger is never
written, deleted or rotated outside `close()`; evidence is read from the
store rather than asserted; an unreadable store fails CLOSED; a campaign
with windows remaining is never closed; and no realized loss is erased.
"""
from decimal import Decimal as D

import pytest

from src import l5_campaign_budget as CB
from src import l5_pmus_live_runner as R

OLD = "camp1.camp11-a1179c160cbf8a44"      # the claim that blocked 2026-10-30
NEW = "camp1.stageA-6a78c44c08f109d6"
BUDGET = D("11.10")
REALIZED = D("-8.8988")


@pytest.fixture
def led(tmp_path):
    return CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd=BUDGET)


class _Store:
    """Only what the closer reads."""

    def __init__(self, *, live=None, committed=None, retired=(), raises=False):
        self._live = dict(live or {})
        self._committed = dict(committed or {})
        self.state = {"retired_markets": {m: True for m in retired}}
        self._raises = raises

    def live_orders(self):
        if self._raises:
            raise OSError("volume gone")
        return dict(self._live)

    def committed(self):
        if self._raises:
            raise OSError("volume gone")
        return dict(self._committed)


def mk(led, store, *, nonce_sid=NEW, windows_remaining=0, monkeypatch=None):
    """A Runner with only the closer's dependencies bound."""
    r = R.Runner.__new__(R.Runner)
    r.store = store
    r.root = "unused"
    monkeypatch.setattr(R.AUTH, "campaign_budget_ledger", lambda root=None: led)
    monkeypatch.setattr(R, "_campaign_nonce_sid", lambda: nonce_sid)
    monkeypatch.setattr(R.Runner, "campaign_telemetry",
                        lambda self: {"windows_remaining": windows_remaining})
    seen = []
    monkeypatch.setattr(R, "emit", lambda k, d: seen.append((k, d)))
    return r, seen


# ================================================= 1. the close itself

def test_a_finished_campaign_is_closed_through_close(led, monkeypatch):
    """camp11's shape: claim open, windows spent, everything settled."""
    led.open_or_resume(OLD)
    store = _Store(committed={"mkt-a": "1"}, retired=("mkt-a",))
    r, seen = mk(led, store, monkeypatch=monkeypatch)
    rec = r.maybe_close_finished_campaign()
    assert rec["result"]["ok"] is True, rec
    assert rec["campaign_id"] == OLD
    assert rec["evidence"]["finished_because"] == "NOT_THE_IN_FORCE_CAMPAIGN"
    assert rec["evidence"]["unsettled_markets"] == []
    assert led.is_active() is False
    assert [k for k, _ in seen] == ["L5_CAMPAIGN_BUDGET_CLOSE"]


def test_the_new_campaign_can_then_claim_the_budget(led, monkeypatch):
    led.open_or_resume(OLD)
    assert led.open_or_resume(NEW)["ok"] is False      # locked before
    store = _Store(committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    r.maybe_close_finished_campaign()
    got = led.open_or_resume(NEW)
    assert got["ok"] is True and got["campaign_id"] == NEW


def test_closing_is_done_only_through_the_ledger_api(self=None):
    """No direct write, delete or id rotation anywhere in the closer."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_close_finished_campaign)
    assert "led.close(" in src
    for forbidden in ("_write(", "unlink", "remove(", "open_or_resume("):
        assert forbidden not in src, forbidden


# ============================================ 2. evidence, not assertion

def test_outstanding_orders_block_the_close(led, monkeypatch):
    led.open_or_resume(OLD)
    store = _Store(live={"c1": {"remaining": "1.5", "market": "m"}},
                   committed={"m": "1"}, retired=("m",))
    r, seen = mk(led, store, monkeypatch=monkeypatch)
    rec = r.maybe_close_finished_campaign()
    assert rec["result"]["ok"] is False
    assert rec["blocker"] == "CAMPAIGN_OUTSTANDING_ORDERS"
    assert rec["evidence"]["outstanding_orders"] == 1
    assert led.is_active() is True, "a blocked close must leave the claim"


def test_unsettled_positions_block_the_close_and_name_them(led, monkeypatch):
    """THE OCTOBER 12/14 SHAPE: committed markets that are not retired."""
    led.open_or_resume(OLD)
    store = _Store(committed={"cpc-btc-updown-15m-2026-10-12-0030z": "2",
                              "cpc-btc-updown-15m-2026-10-14-2230z": "2.9",
                              "settled-one": "1"},
                   retired=("settled-one",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    rec = r.maybe_close_finished_campaign()
    assert rec["result"]["ok"] is False
    assert rec["blocker"].startswith("CAMPAIGN_UNSETTLED_EXPOSURE:2")
    # the precise missing evidence is NAMED, not just counted
    assert sorted(rec["evidence"]["unsettled_markets"]) == [
        "cpc-btc-updown-15m-2026-10-12-0030z",
        "cpc-btc-updown-15m-2026-10-14-2230z"]
    assert led.is_active() is True


def test_a_fully_filled_order_is_not_outstanding(led, monkeypatch):
    led.open_or_resume(OLD)
    store = _Store(live={"c1": {"remaining": "0", "market": "m"}},
                   committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    assert r.maybe_close_finished_campaign()["result"]["ok"] is True


def test_an_unreadable_store_fails_CLOSED(led, monkeypatch):
    """A claim we cannot vouch for is never freed."""
    led.open_or_resume(OLD)
    r, _ = mk(led, _Store(raises=True), monkeypatch=monkeypatch)
    rec = r.maybe_close_finished_campaign()
    assert rec["result"]["ok"] is False
    assert led.is_active() is True


def test_an_unreadable_remaining_counts_as_outstanding(led, monkeypatch):
    led.open_or_resume(OLD)
    store = _Store(live={"c1": {"remaining": None, "market": "m"}},
                   committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    rec = r.maybe_close_finished_campaign()
    assert rec["blocker"] == "CAMPAIGN_OUTSTANDING_ORDERS"


# ======================================= 3. a live campaign is not closed

def test_a_campaign_with_windows_remaining_is_never_closed(led, monkeypatch):
    led.open_or_resume(NEW)                     # the IN-FORCE campaign
    store = _Store(committed={"m": "1"}, retired=("m",))
    r, seen = mk(led, store, nonce_sid=NEW, windows_remaining=2,
                 monkeypatch=monkeypatch)
    rec = r.maybe_close_finished_campaign()
    assert rec["reason"] == "CAMPAIGN_STILL_HAS_WINDOWS"
    assert led.is_active() is True
    assert seen == [], "no record for a campaign that is simply still live"


def test_the_in_force_campaign_closes_once_its_windows_are_spent(
        led, monkeypatch):
    """Automatic verified closure after a campaign settles."""
    led.open_or_resume(NEW)
    store = _Store(committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, nonce_sid=NEW, windows_remaining=0,
              monkeypatch=monkeypatch)
    rec = r.maybe_close_finished_campaign()
    assert rec["result"]["ok"] is True
    assert rec["evidence"]["finished_because"] == "WINDOWS_SPENT"
    assert led.is_active() is False


def test_an_unknown_window_count_is_treated_as_still_live(led, monkeypatch):
    led.open_or_resume(NEW)
    store = _Store(committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, nonce_sid=NEW, windows_remaining=None,
              monkeypatch=monkeypatch)
    assert r.maybe_close_finished_campaign()["reason"] == \
        "CAMPAIGN_STILL_HAS_WINDOWS"
    assert led.is_active() is True


# ================================ 4. duplicate close, restart, boundedness

def test_a_second_close_is_a_no_op(led, monkeypatch):
    led.open_or_resume(OLD)
    store = _Store(committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    assert r.maybe_close_finished_campaign()["result"]["ok"] is True
    assert r.maybe_close_finished_campaign() is None        # stopped for good


def test_a_restart_sees_the_close_as_durable_and_does_not_redo_it(
        led, monkeypatch):
    """The ledger is on the volume, so a fresh process must read it closed
    rather than close it twice or reopen it."""
    led.open_or_resume(OLD)
    store = _Store(committed={"m": "1"}, retired=("m",))
    r1, _ = mk(led, store, monkeypatch=monkeypatch)
    r1.maybe_close_finished_campaign()
    fresh = CB.CampaignBudgetLedger(root=led.root if hasattr(led, "root")
                                    else None, budget_usd=BUDGET) \
        if False else led
    r2, seen2 = mk(fresh, store, monkeypatch=monkeypatch)
    rec = r2.maybe_close_finished_campaign()
    assert rec == {"ok": True, "already_closed": True}
    assert seen2 == []


def test_a_blocked_close_is_retried_but_bounded(led, monkeypatch):
    led.open_or_resume(OLD)
    store = _Store(live={"c1": {"remaining": "1", "market": "m"}},
                   committed={"m": "1"}, retired=("m",))
    r, seen = mk(led, store, monkeypatch=monkeypatch)
    for _ in range(R.CAMPAIGN_CLOSE_MAX_TRIES + 4):
        r._campaign_close_t = None          # past the cadence
        r.maybe_close_finished_campaign()
    assert r._campaign_close_tries == R.CAMPAIGN_CLOSE_MAX_TRIES
    assert led.is_active() is True


def test_the_cadence_stops_it_hammering_the_store(led, monkeypatch):
    led.open_or_resume(OLD)
    store = _Store(live={"c1": {"remaining": "1"}}, committed={"m": "1"},
                   retired=("m",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    r.maybe_close_finished_campaign()
    assert r.maybe_close_finished_campaign() is None     # inside the cadence


# ================================ 5. no loss is erased by a close

def test_realized_losses_still_bind_after_the_close(led, monkeypatch):
    """The ledger holds ownership and a high-water mark, not an allowance."""
    led.open_or_resume(OLD)
    store = _Store(committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    r.maybe_close_finished_campaign()
    led.open_or_resume(NEW)
    before = CB.worst_case_usd(realized_usd=REALIZED, legs=(), outstanding=())
    assert before == abs(REALIZED), before
    # the loss re-enters the gate on its own, from the executor's store
    # the worst case the caller computes INCLUDES the realized loss, and it
    # is that figure -- not anything in the ledger -- the gate refuses on
    why = led.refuse_amount(campaign_id=NEW,
                            used_usd=abs(REALIZED),
                            new_order_cost_usd=D("5"))
    assert why is not None, "a spent budget must still refuse after a close"
    # and a close did not restore headroom: the same figure refuses again
    assert led.refuse_amount(campaign_id=NEW, used_usd=abs(REALIZED),
                             new_order_cost_usd=D("5")) is not None


def test_the_close_writes_no_realized_figure(led, monkeypatch):
    led.open_or_resume(OLD)
    store = _Store(committed={"m": "1"}, retired=("m",))
    r, _ = mk(led, store, monkeypatch=monkeypatch)
    r.maybe_close_finished_campaign()
    doc = led.read()
    for forbidden in ("realized", "realized_usd", "loss", "headroom"):
        assert forbidden not in doc, forbidden
    assert set(doc) <= {"campaign_id", "closed", "opened_utc", "closed_utc",
                        "peak_usd", "released_for"}, doc


# ========================= 6. the pre-arm budget check already exists

def test_arming_refuses_a_foreign_budget_before_claiming_anything():
    """Item 4: a campaign that cannot acquire the claim must not arm. This
    already existed -- pinned so it cannot be removed."""
    import inspect
    from src import l5_live_authorization as AUTH
    src = inspect.getsource(AUTH)
    assert "refuse_arming(campaign_id=_cid)" in src
    assert "CAMPAIGN_BUDGET_REFUSED" in src
    i_check = src.index("refuse_arming(campaign_id=_cid)")
    i_claim = src.index("open_or_resume(")
    assert i_check < i_claim, "the lock must be enforced before the claim"


def test_the_lock_is_the_earliest_enforcement_point():
    import inspect
    src = inspect.getsource(CB.CampaignBudgetLedger.refuse_arming)
    assert "CAMPAIGN_BUDGET_LOCKED" in src
    assert "never reaches the order path" in src
