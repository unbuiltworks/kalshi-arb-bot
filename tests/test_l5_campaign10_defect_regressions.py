"""The two defects that cost campaign 10 two of its three windows.

Both were observed live on 2026-10-30 and both are reproduced here from the
service's own evidence, so neither can come back unnoticed.

**0145z — a window spent on an arm that could not happen.**
`authorize_arming` step 2 accepts EITHER a valid campaign approval OR the Q1
handshake, deliberately. `PMUSTransport.arm()` requires `Q1.armed()` always.
So a process holding a campaign and no handshake passed the boundary, SPENT
a window at the claim, and was then refused by the transport:

    01:45:01.572  claim written for 0145z
    01:45:18.007  L5_LIVE_DISARMED "owner arm token absent or does not match"
    01:45:21.698  L5_LIVE_DISARMED AUTHORIZATION_ALREADY_USED, same lock

**0200z — an adopted order with no submit time.**
We submitted under `l5f-e68bbd642d44a1190afd16fd`; the venue's ack carried no
`venue_order_id`, so the reconciliation could not match our id and adopted
the open order keyed by the VENUE id `DDDFX4X012VP`. That row's `t` was
`mine.get("t")` on an empty `mine`, i.e. None:

    02:00:16.414  SUBMIT  l5f-e68bbd…  DOWN own 0.4100 qty 1.94
    02:00:16.779  ACK     venue_order_id: NULL
    02:00:21.565  L5_LIVE_DISARMED ORDER_STATE_UNCERTAIN
                  "accumulation order submit time unparseable: DDDFX4X012VP"
    02:00:36.293  DDDFX4X012VP  ORDER_STATE_CANCELED  cum 0

Nothing here reaches a venue.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX

SERIES = "cpc-btc-updown-15m-"
W_0145 = SERIES + "2026-10-30-0145z"
W_0200 = SERIES + "2026-10-30-0200z"
NONCE = "camp10-regression-nonce-01"

#: the real ids from the live record
OUR_COID = "l5f-e68bbd642d44a1190afd16fd"
VENUE_ID = "DDDFX4X012VP"


# =============================================== 1. the window that was spent

def _doc(**over):
    now = dt.datetime.now(dt.timezone.utc)
    base = {"approved": True, "nonce": NONCE, "series": SERIES,
            "issued_utc": (now - dt.timedelta(minutes=1)).isoformat(),
            "expires_utc": (now + dt.timedelta(hours=2)).isoformat(),
            # OPENING BOUND, 2026-11-04. Set equal to the expiry so this
            # fixture reproduces the pre-bound semantics EXACTLY -- windows
            # may open until expiry -- and keeps testing what it tested.
            # The bound itself is covered in test_l5_opening_bound.py.
            "last_open_utc": (now + dt.timedelta(hours=2)
                              - dt.timedelta(minutes=15)).isoformat(),
            "max_windows": 3, "max_total_exposure_usd": "20",
            "max_daily_loss_usd": "5"}
    base.update(over)
    return base


class _Store:
    def orders_uncertain(self):
        return None

    def residual(self, market):
        return D(0)


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    """The boundary with a VALID campaign and NO Q1 handshake -- the exact
    state the service was in at 01:45:01."""
    import src.pmus_auth as Q1
    for e in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "test-auth-holder")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "test-auth-holder")
    for e in (AUTH.MARKET_ENV, AUTH.NOT_AFTER_ENV, AUTH.SERIES_ENV,
              AUTH.MAX_WINDOWS_ENV, AUTH.MAX_SUBMISSIONS_ENV):
        monkeypatch.delenv(e, raising=False)
    for e in AUTH.CLASS_BUDGET_ENV.values():
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setattr(AUTH, "durability_refusal", lambda p: None)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "test-deployment")
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(_doc()))
    return tmp_path


def _args(market, **over):
    now = dt.datetime.now(dt.timezone.utc)
    base = dict(
        market=market, preflight={"preflight": "PASSED"}, store=_Store(),
        halted=False, reconciled=True, halt_gate_present=True,
        unresolved=(), max_windows=3,
        # the REAL SafetyLimits object, as the runner passes it
        limits=LX.SafetyLimits(max_total_exposure_usd=D("20"),
                               daily_realized_loss_halt_usd=D("5")),
        window_bounds={"source": "VENUE",
                       "start": (now - dt.timedelta(seconds=60)).isoformat(),
                       "end": (now + dt.timedelta(seconds=840)).isoformat()},
        campaign=CA.campaign_decision(market, reserve=False),
    )
    base.update(over)
    return base


def _locks(root):
    """The WINDOW locks only.

    `windows_spent` globs `l5_auth_claim_<sid>.w.` -- the parent mandate
    lock has no `.w.` and does not consume a window, so counting it would
    make this test assert something the budget does not.
    """
    return sorted(p.name for p in pathlib.Path(root).glob(
        "l5_auth_claim_*.lock") if AUTH.WINDOW_KEY_SEP in p.name)


class TestAWindowIsNotSpentOnAnArmThatCannotHappen:

    def test_the_observed_failure_reproduces_without_the_fix(self, boundary):
        """`transport_armable` unstated == the old behaviour: the campaign
        alone passes the boundary and the window is SPENT. This is 0145z."""
        claim = AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                      **_args(W_0145))
        assert claim["market"] == W_0145
        assert len(_locks(boundary)) == 1, _locks(boundary)

    def test_the_fix_refuses_before_the_claim_and_spends_no_window(
            self, boundary):
        """The transport would refuse this process, so nothing is spent."""
        with pytest.raises(AUTH.Refused) as e:
            AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                  transport_armable=False, **_args(W_0145))
        assert e.value.reason == "TRANSPORT_CANNOT_ARM"
        assert _locks(boundary) == [], _locks(boundary)

    def test_the_window_is_still_available_afterwards(self, boundary):
        """The point of the fix: the refused window can still be used."""
        with pytest.raises(AUTH.Refused):
            AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                  transport_armable=False, **_args(W_0145))
        claim = AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                      transport_armable=True, **_args(W_0145))
        assert claim["market"] == W_0145
        assert len(_locks(boundary)) == 1

    def test_true_behaves_exactly_as_unstated(self, boundary):
        """So the fix adds a refusal and never adds a permission."""
        a = AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                  transport_armable=True, **_args(W_0145))
        assert a["market"] == W_0145

    def test_duplicate_arming_is_still_prevented(self, boundary):
        """Atomicity preserved: the second claim is still refused."""
        AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                              transport_armable=True, **_args(W_0145))
        with pytest.raises(AUTH.Refused) as e:
            AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                  transport_armable=True, **_args(W_0145))
        assert e.value.reason == "AUTHORIZATION_ALREADY_USED"

    def test_the_refusal_names_what_is_missing_and_says_the_window_survived(
            self, boundary):
        with pytest.raises(AUTH.Refused) as e:
            AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                  transport_armable=False, **_args(W_0145))
        d = e.value.detail if isinstance(e.value.detail, dict) else {}
        assert d.get("window_preserved") is True
        assert d.get("needs")

    def test_the_runner_now_wires_the_guard(self):
        """OPTION B, owner-approved 2026-10-30. This test previously pinned
        the guard as deliberately UNWIRED, because choosing a side changed
        the authorization scope and that was the owner's call. It has been
        made: the Q1 handshake is required alongside the campaign, and the
        refusal happens before the claim.

        The contradiction that hid this is closed on both sides -- the
        superseded invariant is restated in
        `test_l5_campaign_is_the_authorization.py`, and that file's FAKE
        transport now raises `NotArmed` exactly as the real
        `PMUSTransport.arm()` does, so the two layers cannot silently
        disagree again.
        """
        import inspect
        from src import l5_pmus_live_runner as R
        assert "transport_armable" in inspect.signature(
            AUTH.authorize_arming).parameters
        src = inspect.getsource(R.Runner.try_arm)
        assert "transport_armable=Q1.armed()" in src
        assert "PENDING AN OWNER DECISION" not in src

    def test_the_fake_transport_mirrors_the_real_arm_gate(self):
        """The mechanism that let the layers disagree, pinned shut."""
        import inspect
        import tests.test_l5_campaign_is_the_authorization as T
        src = inspect.getsource(T._Transport.arm)
        assert "Q1.armed()" in src
        assert "NotArmed" in src

    def test_the_guard_is_inert_unless_explicitly_told_false(self, boundary):
        """So its presence cannot change any existing caller."""
        a = AUTH.authorize_arming(path=str(boundary / "auth.jsonl"),
                                  **_args(W_0200))
        assert a["market"] == W_0200


# ================================ 2. the adopted order with no submit time

class TestAnAdoptedOrderRecoversItsSubmitTime:

    def test_the_observed_failure_shape(self):
        """An adopted row whose `mine` is empty has t None -- which is what
        `maybe_acc_deadline` reads as unusable."""
        got = LX.recover_submit_time(
            {"id": VENUE_ID}, mine={}, live_orders={}, submitted={},
            venue_order_id=VENUE_ID)
        assert got is None

    def test_it_is_recovered_from_our_durable_client_order_record(self):
        """0200z, fixed: our own l5f- record carries the venue id, because
        `link_venue_order_id` writes it as soon as the venue discloses it."""
        t = "2026-10-30T02:00:16.414775+00:00"
        got = LX.recover_submit_time(
            {"id": VENUE_ID}, mine={},
            live_orders={OUR_COID: {"venue_order_id": VENUE_ID, "t": t}},
            submitted={}, venue_order_id=VENUE_ID)
        assert got == t

    def test_the_submitted_log_is_read_too(self):
        t = "2026-10-30T02:00:16.414775+00:00"
        got = LX.recover_submit_time(
            {"id": VENUE_ID}, mine={}, live_orders={},
            submitted={OUR_COID: {"venue_order_id": VENUE_ID, "t": t}},
            venue_order_id=VENUE_ID)
        assert got == t

    def test_our_own_record_wins_over_everything(self):
        got = LX.recover_submit_time(
            {"id": VENUE_ID, "createTime": "2026-01-01T00:00:00+00:00"},
            mine={"t": "2026-10-30T02:00:16+00:00"},
            live_orders={OUR_COID: {"venue_order_id": VENUE_ID,
                                    "t": "2020-01-01T00:00:00+00:00"}},
            submitted={}, venue_order_id=VENUE_ID)
        assert got == "2026-10-30T02:00:16+00:00"

    @pytest.mark.parametrize("key", list(LX.VENUE_ORDER_CREATED_KEYS))
    def test_authoritative_venue_metadata_is_used_when_we_have_no_record(
            self, key):
        got = LX.recover_submit_time(
            {"id": VENUE_ID, key: "2026-10-30T02:00:16Z"},
            mine={}, live_orders={}, submitted={}, venue_order_id=VENUE_ID)
        assert got is not None
        parsed = dt.datetime.fromisoformat(got)
        assert parsed.tzinfo is not None
        assert parsed == dt.datetime(2026, 10, 30, 2, 0, 16,
                                     tzinfo=dt.timezone.utc)

    def test_a_naive_venue_timestamp_is_refused_not_assumed_utc(self):
        """Assuming UTC for a venue that did not say so is the fabrication
        the deadline rule exists to prevent."""
        got = LX.recover_submit_time(
            {"id": VENUE_ID, "createTime": "2026-10-30T02:00:16"},
            mine={}, live_orders={}, submitted={}, venue_order_id=VENUE_ID)
        assert got is None

    @pytest.mark.parametrize("bad", ["", "not-a-time", "0", "null",
                                     "1793325616", "30/10/2026"])
    def test_an_unparseable_venue_timestamp_is_refused(self, bad):
        got = LX.recover_submit_time(
            {"id": VENUE_ID, "createTime": bad},
            mine={}, live_orders={}, submitted={}, venue_order_id=VENUE_ID)
        assert got is None

    def test_a_different_venue_id_is_not_matched(self):
        """Recovery must not borrow another order's timestamp."""
        got = LX.recover_submit_time(
            {"id": VENUE_ID}, mine={},
            live_orders={OUR_COID: {"venue_order_id": "SOMEOTHERID",
                                    "t": "2026-10-30T02:00:16+00:00"}},
            submitted={}, venue_order_id=VENUE_ID)
        assert got is None

    def test_everything_it_returns_satisfies_the_deadline_parser(self):
        """The contract that matters: whatever comes back must parse the way
        `maybe_acc_deadline` parses it, tz-aware."""
        for row, mine, live in (
            ({"id": VENUE_ID, "createTime": "2026-10-30T02:00:16Z"}, {}, {}),
            ({"id": VENUE_ID}, {"t": "2026-10-30T02:00:16+00:00"}, {}),
            ({"id": VENUE_ID}, {},
             {OUR_COID: {"venue_order_id": VENUE_ID,
                         "t": "2026-10-30T02:00:16.4+00:00"}}),
        ):
            got = LX.recover_submit_time(row, mine=mine, live_orders=live,
                                         submitted={},
                                         venue_order_id=VENUE_ID)
            assert got is not None
            st = dt.datetime.fromisoformat(str(got))
            assert st.tzinfo is not None
            st.timestamp()

    def test_the_fail_closed_rule_is_untouched(self):
        """`maybe_acc_deadline` still marks uncertain and disarms on a row it
        cannot time. The fix makes a recoverable case recoverable; it does
        not relax the rule."""
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R.Runner.maybe_acc_deadline)
        assert "mark_uncertain(why)" in src
        assert 'disarm("acc_deadline_record_unusable")' in src
        assert "unusable.append(coid)" in src
        assert "ACC_SUBMIT_TIME_UNPARSEABLE" in src

    def test_the_adoption_site_records_where_the_time_came_from(self):
        """So an audit can tell a recovered timestamp from one of ours."""
        import inspect
        src = inspect.getsource(LX.InventoryStore.reconcile_from_venue)
        assert "recover_submit_time(" in src
        assert "submit_time_source" in src
        assert "submit_time_unrecoverable" in src
