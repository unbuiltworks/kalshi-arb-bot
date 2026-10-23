"""Phase 1 regression suite for Ed25519 owner mandates.

THE KEYPAIR HERE IS AN EPHEMERAL TEST FIXTURE generated in-process, used only
to prove verification, and never written to disk or committed. The owner's
real private key is generated offline by the owner and never exists in this
repository, in Railway, or anywhere the executor can read.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from src import l5_mandate_signature as M


# --------------------------------------------------------------- fixtures

def _keypair():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization as S
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=S.Encoding.Raw, format=S.PublicFormat.Raw)
    return priv, pub.hex()


ENV = dict(environment="production", service_role="l5-pmus-live",
           series="cpc-btc-updown-15m-")


def _payload(**over):
    now = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
    p = {
        "schema": M.SCHEMA,
        "kind": "standing",
        "mandate_id": "l5-standing-001",
        "seq": 1,
        "venue": "PMUS",
        "strategy": "l5-two-sided-btc15m",
        "environment": "production",
        "service_role": "l5-pmus-live",
        "series": "cpc-btc-updown-15m-",
        "not_before": now.isoformat(),
        "not_after": (now + dt.timedelta(days=7)).isoformat(),
        "allowed_actions": ["OPEN", "COMPLETE", "CANCEL"],
        "execution_mode": "MAKER_ONLY",
        "max_windows_per_day": 4,
        "max_submissions_per_day": 12,
        "capital": {"max_cash_deployed_usd": "1.65",
                    "max_total_exposure_usd": "2.00",
                    "campaign_budget_usd": "29.30"},
        "loss_limits": {"daily_realized_loss_halt_usd": "5",
                        "max_cumulative_loss_usd": "20"},
        "residual_cap_contracts": 3,
        "promotion": {"min_settled_windows": 300,
                      "min_expectancy_usd": "0.05",
                      "oos_evidence_id": "btc15m-oos-20261023",
                      "requires_bounded_live_validation": True},
    }
    p.update(over)
    return p


def _signed(priv, payload):
    sig = priv.sign(M.canonical_bytes(payload)).hex()
    return json.dumps({"payload": payload, "sig": sig, "key_id": "test-key"})


@pytest.fixture
def trusted(monkeypatch):
    priv, pub_hex = _keypair()
    monkeypatch.setattr(M, "MANDATE_PUBKEYS", {"test-key": pub_hex})
    return priv


@pytest.fixture
def seq(tmp_path):
    return M.SeqStore(root=str(tmp_path))


NOW = dt.datetime(2026, 10, 23, 12, 30, tzinfo=dt.timezone.utc)


def _verify(raw, seq_store, **kw):
    return M.verify(raw, now=kw.pop("now", NOW), seq_store=seq_store,
                    revoked=kw.pop("revoked", []), **{**ENV, **kw})


# ------------------------------------------------------- the happy path

class TestValidMandate:

    def test_a_correctly_signed_mandate_verifies(self, trusted, seq):
        v = _verify(_signed(trusted, _payload()), seq)
        assert v["ok"], v
        assert v["mandate_id"] == "l5-standing-001"
        assert v["seq"] == 1

    def test_verification_alone_authorizes_nothing(self, trusted, seq):
        v = _verify(_signed(trusted, _payload()), seq)
        assert "authorizes nothing by itself" in v["labels"]

    def test_reporting_does_not_consume_the_monotonic_mark(self, trusted, seq):
        """record_seq defaults False, so the phase-1 reporting path cannot
        burn a seq and lock the owner out of their own mandate."""
        raw = _signed(trusted, _payload())
        assert _verify(raw, seq)["ok"]
        assert seq.highest("test-key") == 0
        assert _verify(raw, seq)["ok"]


# ------------------------------------------------- forgery and tampering

class TestForgery:

    def test_a_forged_signature_is_refused(self, trusted, seq):
        doc = json.loads(_signed(trusted, _payload()))
        doc["sig"] = "00" * 64
        assert _verify(json.dumps(doc), seq)["reason"] \
            == "MANDATE_SIGNATURE_INVALID"

    def test_a_mandate_signed_by_an_untrusted_key_is_refused(self, trusted, seq):
        other, _ = _keypair()
        assert _verify(_signed(other, _payload()), seq)["reason"] \
            == "MANDATE_SIGNATURE_INVALID"

    def test_an_unknown_key_id_is_refused(self, trusted, seq):
        doc = json.loads(_signed(trusted, _payload()))
        doc["key_id"] = "not-a-key"
        assert _verify(json.dumps(doc), seq)["reason"] == "MANDATE_KEY_UNKNOWN"

    @pytest.mark.parametrize("field,value", [
        ("max_windows_per_day", 400),
        ("residual_cap_contracts", 999),
        ("series", "cpc-eth-updown-15m-"),
        ("mandate_id", "someone-elses"),
    ])
    def test_tampering_with_any_signed_field_breaks_the_signature(
            self, trusted, seq, field, value):
        """The payload is signed as a whole, so editing ANY field after
        signing invalidates it -- including widening a limit."""
        doc = json.loads(_signed(trusted, _payload()))
        doc["payload"][field] = value
        r = _verify(json.dumps(doc), seq)["reason"]
        assert r in ("MANDATE_SIGNATURE_INVALID", "MANDATE_BINDING_MISMATCH")

    def test_an_empty_key_registry_refuses_a_valid_mandate(
            self, trusted, seq, monkeypatch):
        """The shipped state. An empty registry trusts nothing."""
        raw = _signed(trusted, _payload())
        monkeypatch.setattr(M, "MANDATE_PUBKEYS", {})
        assert _verify(raw, seq)["reason"] == "MANDATE_NO_TRUSTED_KEY"


# ----------------------------------------------------------- expiry

class TestValidityWindow:

    def test_an_expired_mandate_is_refused(self, trusted, seq):
        v = _verify(_signed(trusted, _payload()), seq,
                    now=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc))
        assert v["reason"] == "MANDATE_EXPIRED"

    def test_a_not_yet_valid_mandate_is_refused(self, trusted, seq):
        v = _verify(_signed(trusted, _payload()), seq,
                    now=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))
        assert v["reason"] == "MANDATE_NOT_YET_VALID"

    def test_a_naive_timestamp_is_refused_not_assumed_utc(self, trusted, seq):
        p = _payload(not_before="2026-10-23T12:00:00")
        assert _verify(_signed(trusted, p), seq)["reason"] \
            == "MANDATE_NOT_BEFORE_INVALID"

    def test_a_duration_above_the_ceiling_is_refused(self, trusted, seq):
        now = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
        p = _payload(not_after=(now + dt.timedelta(days=400)).isoformat())
        v = _verify(_signed(trusted, p), seq)
        assert v["reason"] == "MANDATE_DURATION_ABOVE_CEILING"
        assert v["detail"]["ceiling_s"] == M.MAX_MANDATE_S

    def test_an_inverted_window_is_refused(self, trusted, seq):
        now = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
        p = _payload(not_after=(now - dt.timedelta(days=1)).isoformat())
        assert _verify(_signed(trusted, p), seq)["reason"] \
            == "MANDATE_WINDOW_INVERTED"


# --------------------------------------------- replay / rollback / restart

class TestReplayAndRollback:

    def test_replaying_an_accepted_seq_is_refused(self, trusted, seq):
        raw = _signed(trusted, _payload(seq=5))
        assert M.verify(raw, now=NOW, seq_store=seq, revoked=[],
                        record_seq=True, **ENV)["ok"]
        v = _verify(raw, seq)
        assert v["reason"] == "MANDATE_REPLAYED_OR_ROLLED_BACK"
        assert v["detail"] == {"seq": 5, "highest_accepted": 5}

    def test_rolling_back_to_an_older_genuine_mandate_is_refused(
            self, trusted, seq):
        """The dangerous case: an OLDER document, validly signed, whose
        limits were looser."""
        M.verify(_signed(trusted, _payload(seq=9)), now=NOW, seq_store=seq,
                 revoked=[], record_seq=True, **ENV)
        loose = _payload(seq=8, max_windows_per_day=99)
        assert _verify(_signed(trusted, loose), seq)["reason"] \
            == "MANDATE_REPLAYED_OR_ROLLED_BACK"

    def test_a_higher_seq_supersedes(self, trusted, seq):
        M.verify(_signed(trusted, _payload(seq=1)), now=NOW, seq_store=seq,
                 revoked=[], record_seq=True, **ENV)
        assert _verify(_signed(trusted, _payload(seq=2)), seq)["ok"]

    def test_the_mark_survives_restart(self, tmp_path, trusted):
        """A fresh SeqStore over the same root is a restarted process."""
        a = M.SeqStore(root=str(tmp_path))
        M.verify(_signed(trusted, _payload(seq=7)), now=NOW, seq_store=a,
                 revoked=[], record_seq=True, **ENV)
        b = M.SeqStore(root=str(tmp_path))
        assert b.highest("test-key") == 7
        assert _verify(_signed(trusted, _payload(seq=7)), b)["reason"] \
            == "MANDATE_REPLAYED_OR_ROLLED_BACK"

    def test_record_never_lowers_the_mark(self, seq):
        seq.record("test-key", 10)
        seq.record("test-key", 3)
        assert seq.highest("test-key") == 10

    def test_seq_must_be_a_positive_int_not_a_string(self, trusted, seq):
        assert _verify(_signed(trusted, _payload(seq="3")), seq)["reason"] \
            == "MANDATE_SEQ_INVALID"

    def test_seq_zero_is_refused(self, trusted, seq):
        assert _verify(_signed(trusted, _payload(seq=0)), seq)["reason"] \
            == "MANDATE_SEQ_INVALID"


# ------------------------------------------------------------ revocation

class TestRevocation:

    def test_a_revoked_mandate_id_is_refused(self, trusted, seq):
        v = _verify(_signed(trusted, _payload()), seq,
                    revoked=["l5-standing-001"])
        assert v["reason"] == "MANDATE_REVOKED"

    def test_a_revoked_specific_seq_is_refused(self, trusted, seq):
        v = _verify(_signed(trusted, _payload(seq=4)), seq,
                    revoked=["l5-standing-001#4"])
        assert v["reason"] == "MANDATE_REVOKED"

    def test_unknown_revocation_state_refuses(self, trusted, seq):
        """None is NOT ESTABLISHED, never 'nothing revoked' -- the same rule
        HaltGate applies to the latch."""
        v = M.verify(_signed(trusted, _payload()), now=NOW, seq_store=seq,
                     revoked=None, **ENV)
        assert v["reason"] == "MANDATE_REVOCATION_UNKNOWN"

    def test_an_unrelated_revocation_does_not_block(self, trusted, seq):
        assert _verify(_signed(trusted, _payload()), seq,
                       revoked=["some-other-mandate"])["ok"]


# -------------------------------------------------------- missing storage

class TestMissingOrBrokenStorage:

    def test_an_unreadable_seq_store_refuses(self, trusted, tmp_path):
        s = M.SeqStore(root=str(tmp_path))
        os.makedirs(s.dir, exist_ok=True)
        with open(s._path("test-key"), "w") as fh:
            fh.write("{ not json")
        v = _verify(_signed(trusted, _payload()), s)
        assert v["reason"] == "MANDATE_SEQ_STORE_UNAVAILABLE"

    def test_a_malformed_seq_store_refuses(self, trusted, tmp_path):
        s = M.SeqStore(root=str(tmp_path))
        os.makedirs(s.dir, exist_ok=True)
        with open(s._path("test-key"), "w") as fh:
            json.dump({"highest_seq": "lots"}, fh)
        assert _verify(_signed(trusted, _payload()), s)["reason"] \
            == "MANDATE_SEQ_STORE_UNAVAILABLE"

    def test_a_missing_seq_file_is_zero_not_an_error(self, seq):
        assert seq.highest("never-seen") == 0

    def test_absent_and_empty_documents_refuse(self, trusted, seq):
        for raw in (None, "", "   "):
            assert _verify(raw, seq)["reason"] == "MANDATE_ABSENT"

    def test_non_json_refuses(self, trusted, seq):
        assert _verify("not json at all", seq)["reason"] == "MANDATE_NOT_JSON"


# ------------------------------------------------ binding (requirement 2)

class TestBinding:

    @pytest.mark.parametrize("field,bad", [
        ("venue", "KALSHI"),
        ("strategy", "l5-sports"),
        ("environment", "staging"),
        ("service_role", "l5-pmus-observer"),
        ("series", "cpc-eth-updown-15m-"),
        ("execution_mode", "TAKER_PERMITTED"),
    ])
    def test_a_mandate_for_another_deployment_is_refused(
            self, trusted, seq, field, bad):
        p = _payload(**{field: bad})
        v = _verify(_signed(trusted, p), seq)
        assert v["reason"] == "MANDATE_BINDING_MISMATCH"
        assert v["detail"]["field"] == field

    def test_taker_crossing_cannot_be_expressed(self, trusted, seq):
        """execution_mode must be MAKER_ONLY and no action names crossing, so
        no signed mandate can authorize a taker order."""
        assert M.EXPECTED_EXECUTION_MODE == "MAKER_ONLY"
        assert not any("TAKER" in a or "CROSS" in a for a in M.KNOWN_ACTIONS)
        assert _verify(_signed(trusted, _payload(
            allowed_actions=["OPEN", "CROSS"])), seq)["reason"] \
            == "MANDATE_ACTION_UNKNOWN"

    def test_duplicate_actions_are_refused(self, trusted, seq):
        assert _verify(_signed(trusted, _payload(
            allowed_actions=["OPEN", "OPEN"])), seq)["reason"] \
            == "MANDATE_ACTIONS_DUPLICATED"


# --------------------------------------------------- schema strictness

class TestStrictSchema:

    def test_a_missing_field_refuses(self, trusted, seq):
        p = _payload()
        del p["loss_limits"]
        v = _verify(_signed(trusted, p), seq)
        assert v["reason"] == "MANDATE_FIELD_MISSING"
        assert "loss_limits" in v["detail"]["missing"]

    def test_an_unknown_field_refuses_rather_than_being_ignored(
            self, trusted, seq):
        """A typo'd field that silently granted different scope than the
        owner read back is the failure this prevents."""
        p = _payload(max_windows_per_dey=400)
        v = _verify(_signed(trusted, p), seq)
        assert v["reason"] == "MANDATE_UNKNOWN_FIELD"
        assert "max_windows_per_dey" in v["detail"]["unknown"]

    def test_a_float_money_value_refuses(self, trusted, seq):
        p = _payload()
        p["capital"] = dict(p["capital"], max_cash_deployed_usd=1.65)
        v = _verify(_signed(trusted, p), seq)
        assert v["reason"] == "MANDATE_MONEY_INVALID"

    def test_an_unknown_schema_refuses(self, trusted, seq):
        assert _verify(_signed(trusted, _payload(schema="l5.mandate.v99")),
                       seq)["reason"] == "MANDATE_SCHEMA_UNKNOWN"


# ------------------------------------- requirement 7: promotion is a block

class TestPromotionCannotEnableLive:

    def test_a_mandate_opting_out_of_live_validation_is_refused(
            self, trusted, seq):
        p = _payload()
        p["promotion"] = dict(p["promotion"],
                              requires_bounded_live_validation=False)
        v = _verify(_signed(trusted, p), seq)
        assert v["reason"] == "MANDATE_PROMOTION_MUST_REQUIRE_LIVE_VALIDATION"

    def test_missing_oos_evidence_id_is_refused(self, trusted, seq):
        p = _payload()
        p["promotion"] = dict(p["promotion"], oos_evidence_id="")
        assert _verify(_signed(trusted, p), seq)["reason"] \
            == "MANDATE_PROMOTION_OOS_EVIDENCE_MISSING"

    def test_the_schema_has_no_field_that_could_auto_enable(self):
        """There is deliberately no 'auto_promote' or 'enable_on_positive'
        field, so shadow expectancy can only ever WITHHOLD arming."""
        assert not any("auto" in f or "enable" in f
                       for f in M.REQUIRED_PROMOTION_FIELDS)
        assert "requires_bounded_live_validation" in M.REQUIRED_PROMOTION_FIELDS


# ------------------------------------------------------------- telemetry

class TestTelemetry:

    def test_telemetry_never_carries_the_document_or_signature(
            self, trusted, seq):
        raw = _signed(trusted, _payload())
        t = M.telemetry(_verify(raw, seq))
        blob = json.dumps(t)
        assert "sig" not in t and "scope" not in t and "payload" not in t
        assert json.loads(raw)["sig"] not in blob

    def test_telemetry_states_phase_1_is_not_enforcing(self, trusted, seq):
        assert M.telemetry(_verify(_signed(trusted, _payload()), seq))[
            "enforcing"] is False


# --------------------------------- requirement 4: what must NOT reset

class TestWhatMustNotReset:

    def test_the_mandate_carries_no_mechanism_to_reset_losses(self):
        """Daily ACTION allowances may roll at UTC midnight. Cumulative
        realized losses, unresolved inventory and mandate-wide exposure must
        not -- so this module has no writer for any of them.

        Checked structurally rather than by grepping for words: the only
        thing it writes is the seq high-water mark, and the only thing that
        writes is SeqStore.record."""
        import inspect
        src = inspect.getsource(M)
        # the sole file-opening write path in the module
        assert src.count('open(tmp, "w")') == 1
        assert src.count(', "w")') == 1, "a second write path appeared"
        writers = [n for n, f in vars(M.SeqStore).items()
                   if callable(f) and not n.startswith("__")]
        assert sorted(writers) == ["_path", "highest", "record"]
        # and it imports no store that owns realized P&L or inventory
        for forbidden in ("l5_live_execution", "InventoryStore",
                          "l5_campaign_budget", "l5_halt_gate"):
            assert forbidden not in src

    def test_only_action_rates_are_per_day(self):
        per_day = [f for f in M.REQUIRED_PAYLOAD_FIELDS if "per_day" in f]
        assert sorted(per_day) == ["max_submissions_per_day",
                                   "max_windows_per_day"]
