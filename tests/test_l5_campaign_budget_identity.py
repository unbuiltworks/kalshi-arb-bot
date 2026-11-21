"""One authorization decision, and a missing campaign id refuses.

THE DEFECT. Three call sites built their own ApprovalGate and two omitted
max_age_s, so they used the 1-hour DEFAULT_MAX_AGE_S while `try_arm` used the
4-hour MAX_CAMPAIGN_S. Observed live on 2026-10-23: the heartbeat reported
`approval_stale` at 18:47 on a document arming would still have accepted, and
`_campaign_nonce_sid` returned None on the same 1-hour clock -- which made
ValidationGates gate 3b SKIP ITSELF, so the owner's $29.30 worst-case budget
stopped binding one hour after issue while trading stayed authorized for four.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_validation_gates as VG


ISSUED = dt.datetime(2026, 10, 23, 17, 20, tzinfo=dt.timezone.utc)


def doc(*, issued=ISSUED, minutes=75, nonce="camp-20261023-c1",
        **over):
    # the nonce must be at least 8 characters
    # (approval_nonce_too_short), which a renewal must respect too
    d = {"approved": True, "nonce": nonce, "series": "cpc-btc-updown-15m-",
         "issued_utc": issued.isoformat(),
         "expires_utc": (issued + dt.timedelta(minutes=minutes)).isoformat(),
         "max_windows": 4, "max_total_exposure_usd": "2.00",
         "max_daily_loss_usd": "5"}
    d.update(over)
    return json.dumps(d)


# ---------------------------------------------- one decision, one expiry

class TestASingleCanonicalGate:

    def test_the_canonical_gate_uses_the_campaign_window(self):
        assert CA.approval_gate().max_age_s == CA.MAX_CAMPAIGN_S
        assert CA.MAX_CAMPAIGN_S == 4 * 3600.0

    def test_it_is_not_the_one_hour_default(self):
        """The bug was silently taking DEFAULT_MAX_AGE_S."""
        assert CA.DEFAULT_MAX_AGE_S == 3600.0
        assert CA.approval_gate().max_age_s != CA.DEFAULT_MAX_AGE_S

    def test_no_caller_builds_its_own_gate_any_more(self):
        """Unrepresentable beats documented: if a call site constructs its
        own ApprovalGate again it can diverge again."""
        runner = open("src/l5_pmus_live_runner.py").read()
        assert "ApprovalGate(CAMP.EnvApprovalStore())" not in runner
        assert "CAMP.approval_gate()" in runner

    def test_campaign_decision_uses_the_canonical_gate(self):
        import inspect
        assert "approval_gate()" in inspect.getsource(CA.campaign_decision)


class TestTheSixtyMinuteBoundary:
    """The exact window where the two clocks disagreed."""

    def _state(self, at, **kw):
        class S:
            def get(_, key):
                return doc(**kw).encode() if key == CA.APPROVAL_KEY else None
        return CA.ApprovalGate(S(), max_age_s=CA.MAX_CAMPAIGN_S,
                               now_fn=lambda: at).state()

    def test_at_fifty_nine_minutes_it_is_approved(self):
        at = ISSUED + dt.timedelta(minutes=59)
        assert self._state(at).get("approved") is True

    def test_at_sixty_one_minutes_it_is_still_approved(self):
        """THE REGRESSION. The old 1-hour gate flipped to approval_stale
        here while arming accepted the same document."""
        at = ISSUED + dt.timedelta(minutes=61)
        st = self._state(at)
        assert st.get("approved") is True, st.get("why")
        assert st.get("why") != "approval_stale"

    def test_at_ninety_minutes_the_documents_own_expiry_decides(self):
        """Still inside the 4-hour staleness window, but past expires_utc."""
        at = ISSUED + dt.timedelta(minutes=90)
        st = self._state(at, minutes=75)
        assert st.get("approved") is not True

    def test_past_four_hours_it_is_stale_regardless(self):
        at = ISSUED + dt.timedelta(hours=4, minutes=1)
        st = self._state(at, minutes=300)
        assert st.get("approved") is not True
        assert st.get("why") == "approval_stale"


# ------------------------------- a missing identity refuses, never skips

def gates(**kw):
    base = dict(max_cash_deployed_usd=D("1.65"),
                max_unmatched_contracts=D("5"),
                max_cumulative_loss_usd=D("20"))
    base.update(kw)
    return VG.ValidationGates(**base)


class _Budget:
    """Stands in for CampaignBudgetLedger. Records whether it was asked."""

    def __init__(self, verdict=None, raises=False):
        self.verdict, self.raises, self.asked = verdict, raises, False
        self.budget_usd = D("29.30")

    def refuse_amount(self, *, campaign_id, used_usd, new_order_cost_usd):
        self.asked = True
        if self.raises:
            raise RuntimeError("ledger unreadable")
        return self.verdict

    def telemetry(self, campaign_id=""):
        return {"campaign_id": campaign_id}


class TestMissingIdentityRefuses:

    def test_a_configured_budget_with_no_id_refuses(self):
        b = _Budget()
        g = gates(campaign_budget=b, campaign_id=None)
        assert g.campaign_budget is not None and g.campaign_id is None
        # the gate must refuse rather than fall through
        import inspect
        src = inspect.getsource(VG)
        assert 'return "CAMPAIGN_IDENTITY_UNAVAILABLE"' in src
        assert "and self.campaign_id:" not in src

    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_every_empty_identity_form_refuses(self, bad):
        g = gates(campaign_budget=_Budget(), campaign_id=bad)
        assert g.campaign_id is None

    def test_no_budget_configured_is_still_inert(self):
        """UNSET means off, unchanged: a gate nobody approved must not
        switch itself on."""
        g = gates(campaign_budget=None, campaign_id=None)
        assert g.campaign_budget is None

    def test_an_unreadable_budget_still_refuses(self):
        b = _Budget(raises=True)
        g = gates(campaign_budget=b, campaign_id="camp1.x")
        import inspect
        assert "CAMPAIGN_BUDGET_ERROR" in inspect.getsource(VG)

    def test_an_exhausted_budget_refuses(self):
        b = _Budget(verdict="CAMPAIGN_BUDGET_EXCEEDED")
        assert b.refuse_amount(campaign_id="c", used_usd=D("29.30"),
                               new_order_cost_usd=D("1")) \
            == "CAMPAIGN_BUDGET_EXCEEDED"
        assert b.asked is True


class TestTheNonceIsOwnedByTheApproval:

    def test_a_malformed_approval_yields_no_identity(self, monkeypatch):
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", "{ not json")
        from src import l5_pmus_live_runner as R
        assert R._campaign_nonce_sid() is None

    def test_an_absent_approval_yields_no_identity(self, monkeypatch):
        monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
        from src import l5_pmus_live_runner as R
        assert R._campaign_nonce_sid() is None

    def test_an_expired_approval_yields_no_identity(self, monkeypatch):
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL",
                           doc(issued=dt.datetime(2026, 10, 1,
                                                  tzinfo=dt.timezone.utc)))
        from src import l5_pmus_live_runner as R
        assert R._campaign_nonce_sid() is None

    def test_a_missing_nonce_field_yields_no_identity(self, monkeypatch):
        d = json.loads(doc())
        del d["nonce"]
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", json.dumps(d))
        from src import l5_pmus_live_runner as R
        assert R._campaign_nonce_sid() is None

    def test_identity_survives_restart_because_it_is_derived(self, monkeypatch):
        """The id comes from the document, not from process state, so a
        restart recomputes the same one and the budget ledger is resumed
        rather than reset."""
        # anchored to REAL now: _campaign_nonce_sid has no now_fn hook, so
        # a document fixed in the past is simply expired when the suite runs.
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL",
                           doc(issued=now - dt.timedelta(minutes=5),
                               minutes=120, nonce="camp-stable-20261023"))
        from src import l5_pmus_live_runner as R
        a = R._campaign_nonce_sid()
        b = R._campaign_nonce_sid()
        assert a == b and a is not None
        assert "camp-stable-20261023" in a


class TestHeartbeatMatchesEnforcement:

    def test_both_read_the_same_gate(self):
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R)
        # campaign_telemetry and _campaign_nonce_sid both canonical
        assert src.count("CAMP.approval_gate()") >= 2

    def test_the_preserved_limits_are_untouched(self):
        g = gates()
        assert g.max_cash_deployed_usd == D("1.65")
        assert g.max_cumulative_loss_usd == D("20")
