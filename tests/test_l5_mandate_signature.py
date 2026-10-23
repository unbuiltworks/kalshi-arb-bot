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
def seq():
    """The watchdog-published replay floor, as the enforcing caller would
    have fetched it. 0 means "the watchdog has seen nothing for this key"."""
    return 0


NOW = dt.datetime(2026, 10, 23, 12, 30, tzinfo=dt.timezone.utc)


def _verify(raw, seq_floor=0, **kw):
    return M.verify(raw, now=kw.pop("now", NOW), seq_floor=seq_floor,
                    revoked=kw.pop("revoked", []), **{**ENV, **kw})


# ------------------------------------------------------- the happy path

class TestValidMandate:

    def test_a_correctly_signed_mandate_verifies(self, trusted):
        v = _verify(_signed(trusted, _payload()))
        assert v["ok"], v
        assert v["mandate_id"] == "l5-standing-001"
        assert v["seq"] == 1

    def test_verification_alone_authorizes_nothing(self, trusted):
        v = _verify(_signed(trusted, _payload()))
        assert "authorizes nothing by itself" in v["labels"]

    def test_reporting_cannot_consume_the_mandate(self, trusted):
        """`verify` is pure: no store, no write, no advancing mark. Verifying
        the same document repeatedly is idempotent, so the reporting path
        cannot lock the owner out of their own mandate."""
        raw = _signed(trusted, _payload())
        assert _verify(raw, 1)["ok"]
        assert _verify(raw, 1)["ok"]
        assert _verify(raw, 1)["ok"]


# ------------------------------------------------- forgery and tampering

class TestForgery:

    def test_a_forged_signature_is_refused(self, trusted):
        doc = json.loads(_signed(trusted, _payload()))
        doc["sig"] = "00" * 64
        assert _verify(json.dumps(doc))["reason"] \
            == "MANDATE_SIGNATURE_INVALID"

    def test_a_mandate_signed_by_an_untrusted_key_is_refused(self, trusted):
        other, _ = _keypair()
        assert _verify(_signed(other, _payload()))["reason"] \
            == "MANDATE_SIGNATURE_INVALID"

    def test_an_unknown_key_id_is_refused(self, trusted):
        doc = json.loads(_signed(trusted, _payload()))
        doc["key_id"] = "not-a-key"
        assert _verify(json.dumps(doc))["reason"] == "MANDATE_KEY_UNKNOWN"

    @pytest.mark.parametrize("field,value", [
        ("max_windows_per_day", 400),
        ("residual_cap_contracts", 999),
        ("series", "cpc-eth-updown-15m-"),
        ("mandate_id", "someone-elses"),
    ])
    def test_tampering_with_any_signed_field_breaks_the_signature(
            self, trusted, field, value):
        """The payload is signed as a whole, so editing ANY field after
        signing invalidates it -- including widening a limit."""
        doc = json.loads(_signed(trusted, _payload()))
        doc["payload"][field] = value
        r = _verify(json.dumps(doc))["reason"]
        assert r in ("MANDATE_SIGNATURE_INVALID", "MANDATE_BINDING_MISMATCH")

    def test_an_empty_key_registry_refuses_a_valid_mandate(
            self, trusted, monkeypatch):
        """The shipped state. An empty registry trusts nothing."""
        raw = _signed(trusted, _payload())
        monkeypatch.setattr(M, "MANDATE_PUBKEYS", {})
        assert _verify(raw)["reason"] == "MANDATE_NO_TRUSTED_KEY"


# ----------------------------------------------------------- expiry

class TestValidityWindow:

    def test_an_expired_mandate_is_refused(self, trusted):
        v = _verify(_signed(trusted, _payload()),
                    now=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc))
        assert v["reason"] == "MANDATE_EXPIRED"

    def test_a_not_yet_valid_mandate_is_refused(self, trusted):
        v = _verify(_signed(trusted, _payload()),
                    now=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))
        assert v["reason"] == "MANDATE_NOT_YET_VALID"

    def test_a_naive_timestamp_is_refused_not_assumed_utc(self, trusted):
        p = _payload(not_before="2026-10-23T12:00:00")
        assert _verify(_signed(trusted, p))["reason"] \
            == "MANDATE_NOT_BEFORE_INVALID"

    def test_a_duration_above_the_ceiling_is_refused(self, trusted):
        now = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
        p = _payload(not_after=(now + dt.timedelta(days=400)).isoformat())
        v = _verify(_signed(trusted, p))
        assert v["reason"] == "MANDATE_DURATION_ABOVE_CEILING"
        assert v["detail"]["ceiling_s"] == M.MAX_MANDATE_S

    def test_an_inverted_window_is_refused(self, trusted):
        now = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
        p = _payload(not_after=(now - dt.timedelta(days=1)).isoformat())
        assert _verify(_signed(trusted, p))["reason"] \
            == "MANDATE_WINDOW_INVERTED"


# --------------------------------------------- replay / rollback / restart

class TestReplayAndRollback:
    """Floor semantics: the watchdog records the highest seq it has OBSERVED,
    so the executor requires `seq >= floor` and writes nothing."""

    def test_the_current_mandate_passes_its_own_floor(self, trusted):
        """`>=`, not `>`. The watchdog sees the live mandate first, so its
        seq is already in the floor; strict inequality would reject it."""
        assert _verify(_signed(trusted, _payload(seq=5)), 5)["ok"]

    def test_replaying_below_the_floor_is_refused(self, trusted):
        v = _verify(_signed(trusted, _payload(seq=4)), 5)
        assert v["reason"] == "MANDATE_REPLAYED_OR_ROLLED_BACK"
        assert v["detail"] == {"seq": 4, "floor": 5}

    def test_rolling_back_to_an_older_genuine_mandate_is_refused(self,
                                                                 trusted):
        """The dangerous case: an OLDER document, validly signed, whose
        limits were looser."""
        loose = _payload(seq=8, max_windows_per_day=99)
        assert _verify(_signed(trusted, loose), 9)["reason"] \
            == "MANDATE_REPLAYED_OR_ROLLED_BACK"

    def test_a_higher_seq_supersedes(self, trusted):
        assert _verify(_signed(trusted, _payload(seq=12)), 9)["ok"]

    def test_an_unknown_floor_refuses(self, trusted):
        """None is NOT ESTABLISHED. An executor that cannot find out whether
        its mandate was superseded is not authorized."""
        v = M.verify(_signed(trusted, _payload()), now=NOW, seq_floor=None,
                     revoked=[], **ENV)
        assert v["reason"] == "MANDATE_SEQ_FLOOR_UNKNOWN"

    def test_verify_performs_no_io_and_writes_nothing(self, trusted):
        """Requirement 5 auditability: the verifier is pure, so reporting
        cannot consume or advance the owner's mandate."""
        import ast, inspect, textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(M.verify)))
        # strip docstrings so prose about purity cannot satisfy or break it
        called = {
            n.func.attr if isinstance(n.func, ast.Attribute) else
            getattr(n.func, "id", "")
            for n in ast.walk(tree) if isinstance(n, ast.Call)
        }
        for forbidden in ("open", "urlopen", "makedirs", "replace", "record",
                          "observe", "fsync", "dump"):
            assert forbidden not in called, forbidden

    def test_seq_must_be_a_positive_int_not_a_string(self, trusted):
        assert _verify(_signed(trusted, _payload(seq="3")))["reason"] \
            == "MANDATE_SEQ_INVALID"

    def test_seq_zero_is_refused(self, trusted):
        assert _verify(_signed(trusted, _payload(seq=0)))["reason"] \
            == "MANDATE_SEQ_INVALID"


class TestWatchdogSeqWriter:
    """The WATCHDOG's side. The executor never calls this."""

    def test_the_floor_never_lowers(self, tmp_path):
        w = M.WatchdogSeqWriter(root=str(tmp_path))
        w.observe("k", 10)
        w.observe("k", 3)
        assert w.read()["floors"]["k"] == 10

    def test_the_floor_survives_restart(self, tmp_path):
        M.WatchdogSeqWriter(root=str(tmp_path)).observe("k", 7)
        assert M.WatchdogSeqWriter(root=str(tmp_path)
                                   ).read()["floors"]["k"] == 7

    def test_a_malformed_floor_file_raises(self, tmp_path):
        w = M.WatchdogSeqWriter(root=str(tmp_path))
        os.makedirs(w.dir, exist_ok=True)
        with open(w._path(), "w") as fh:
            fh.write("{ not json")
        with pytest.raises(M.MandateUnavailable):
            w.read()

    def test_the_executor_module_has_no_writer_it_can_reach(self):
        """Requirement 3: the executor's path is GET-only. The only writer
        is the watchdog's, and the HTTP clients expose no put."""
        for cls in (M.HttpSeqFloorStore, M.HttpRevocationStore):
            assert not hasattr(cls, "put")
            assert not hasattr(cls, "observe")
            assert not hasattr(cls, "record")


# ------------------------------------------------------------ revocation

class TestRevocation:

    def test_a_revoked_mandate_id_is_refused(self, trusted):
        v = _verify(_signed(trusted, _payload()),
                    revoked=["l5-standing-001"])
        assert v["reason"] == "MANDATE_REVOKED"

    def test_a_revoked_specific_seq_is_refused(self, trusted):
        v = _verify(_signed(trusted, _payload(seq=4)),
                    revoked=["l5-standing-001#4"])
        assert v["reason"] == "MANDATE_REVOKED"

    def test_unknown_revocation_state_refuses(self, trusted):
        """None is NOT ESTABLISHED, never 'nothing revoked' -- the same rule
        HaltGate applies to the latch."""
        v = M.verify(_signed(trusted, _payload()), now=NOW, seq_floor=0,
                     revoked=None, **ENV)
        assert v["reason"] == "MANDATE_REVOCATION_UNKNOWN"

    def test_an_unrelated_revocation_does_not_block(self, trusted):
        assert _verify(_signed(trusted, _payload()),
                       revoked=["some-other-mandate"])["ok"]


# -------------------------------------------------------- missing storage

class TestWatchdogStores:
    """Missing, stale, unreadable or wrong-key all REFUSE."""

    FRESH = dt.datetime(2026, 10, 23, 12, 29, tzinfo=dt.timezone.utc)

    def _opener(self, body):
        def o(url, timeout):
            return body
        return o

    def _floor(self, body, **kw):
        return M.HttpSeqFloorStore("http://wd:8080",
                                   opener=self._opener(body), **kw)

    def test_a_fresh_floor_document_reads(self):
        body = json.dumps({"asof_utc": self.FRESH.isoformat(),
                           "floors": {"test-key": 4}}).encode()
        assert self._floor(body).floor("test-key", now=NOW) == 4

    def test_a_key_absent_from_a_fresh_document_is_a_real_zero(self):
        """A positive statement by the watchdog, not a guess."""
        body = json.dumps({"asof_utc": self.FRESH.isoformat(),
                           "floors": {}}).encode()
        assert self._floor(body).floor("test-key", now=NOW) == 0

    def test_an_absent_document_refuses_rather_than_reading_as_empty(self):
        """Unlike the halt latch, absence here is NOT a positive 'no floor'."""
        with pytest.raises(M.MandateUnavailable):
            self._floor(None).floor("test-key", now=NOW)

    def test_a_stale_document_refuses(self):
        old = NOW - dt.timedelta(seconds=M.DEFAULT_MAX_AGE_S + 60)
        body = json.dumps({"asof_utc": old.isoformat(),
                           "floors": {"test-key": 4}}).encode()
        with pytest.raises(M.MandateUnavailable) as e:
            self._floor(body).floor("test-key", now=NOW)
        assert "stale" in str(e.value)

    def test_a_future_dated_document_refuses(self):
        fut = NOW + dt.timedelta(hours=2)
        body = json.dumps({"asof_utc": fut.isoformat(),
                           "floors": {}}).encode()
        with pytest.raises(M.MandateUnavailable):
            self._floor(body).floor("test-key", now=NOW)

    def test_a_document_with_no_timestamp_refuses(self):
        body = json.dumps({"floors": {}}).encode()
        with pytest.raises(M.MandateUnavailable):
            self._floor(body).floor("test-key", now=NOW)

    def test_unparseable_refuses(self):
        with pytest.raises(M.MandateUnavailable):
            self._floor(b"{ not json").floor("test-key", now=NOW)

    def test_a_non_integer_floor_refuses(self):
        body = json.dumps({"asof_utc": self.FRESH.isoformat(),
                           "floors": {"test-key": "lots"}}).encode()
        with pytest.raises(M.MandateUnavailable):
            self._floor(body).floor("test-key", now=NOW)

    def test_asking_a_store_for_a_key_it_does_not_serve_raises(self):
        """Not-served and absent must not arrive as the same None."""
        body = json.dumps({"asof_utc": self.FRESH.isoformat(),
                           "floors": {}}).encode()
        with pytest.raises(M.MandateUnavailable) as e:
            self._floor(body).fetch("something_else.json", now=NOW)
        assert "unreadable here, not" in str(e.value)

    def test_a_transport_failure_refuses(self):
        def boom(url, timeout):
            raise OSError("connection refused")
        st = M.HttpSeqFloorStore("http://wd:8080", opener=boom)
        with pytest.raises(M.MandateUnavailable):
            st.floor("test-key", now=NOW)

    def test_a_fresh_revocation_list_reads(self):
        body = json.dumps({"asof_utc": self.FRESH.isoformat(),
                           "revoked": ["l5-standing-001"]}).encode()
        st = M.HttpRevocationStore("http://wd:8080",
                                   opener=self._opener(body))
        assert st.revoked(now=NOW) == frozenset({"l5-standing-001"})

    def test_an_absent_revocation_list_refuses(self):
        st = M.HttpRevocationStore("http://wd:8080",
                                   opener=self._opener(None))
        with pytest.raises(M.MandateUnavailable):
            st.revoked(now=NOW)

    def test_a_stale_revocation_list_refuses(self):
        old = NOW - dt.timedelta(seconds=M.DEFAULT_MAX_AGE_S + 1)
        body = json.dumps({"asof_utc": old.isoformat(),
                           "revoked": []}).encode()
        st = M.HttpRevocationStore("http://wd:8080",
                                   opener=self._opener(body))
        with pytest.raises(M.MandateUnavailable):
            st.revoked(now=NOW)

    def test_a_non_string_revocation_entry_refuses(self):
        body = json.dumps({"asof_utc": self.FRESH.isoformat(),
                           "revoked": [42]}).encode()
        st = M.HttpRevocationStore("http://wd:8080",
                                   opener=self._opener(body))
        with pytest.raises(M.MandateUnavailable):
            st.revoked(now=NOW)


class TestAbsentDocuments:

    def test_absent_and_empty_documents_refuse(self, trusted):
        for raw in (None, "", "   "):
            assert _verify(raw)["reason"] == "MANDATE_ABSENT"

    def test_non_json_refuses(self, trusted):
        assert _verify("not json at all")["reason"] == "MANDATE_NOT_JSON"


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
            self, trusted, field, bad):
        p = _payload(**{field: bad})
        v = _verify(_signed(trusted, p))
        assert v["reason"] == "MANDATE_BINDING_MISMATCH"
        assert v["detail"]["field"] == field

    def test_taker_crossing_cannot_be_expressed(self, trusted):
        """execution_mode must be MAKER_ONLY and no action names crossing, so
        no signed mandate can authorize a taker order."""
        assert M.EXPECTED_EXECUTION_MODE == "MAKER_ONLY"
        assert not any("TAKER" in a or "CROSS" in a for a in M.KNOWN_ACTIONS)
        assert _verify(_signed(trusted, _payload(
            allowed_actions=["OPEN", "CROSS"])))["reason"] \
            == "MANDATE_ACTION_UNKNOWN"

    def test_duplicate_actions_are_refused(self, trusted):
        assert _verify(_signed(trusted, _payload(
            allowed_actions=["OPEN", "OPEN"])))["reason"] \
            == "MANDATE_ACTIONS_DUPLICATED"


# --------------------------------------------------- schema strictness

class TestStrictSchema:

    def test_a_missing_field_refuses(self, trusted):
        p = _payload()
        del p["loss_limits"]
        v = _verify(_signed(trusted, p))
        assert v["reason"] == "MANDATE_FIELD_MISSING"
        assert "loss_limits" in v["detail"]["missing"]

    def test_an_unknown_field_refuses_rather_than_being_ignored(
            self, trusted):
        """A typo'd field that silently granted different scope than the
        owner read back is the failure this prevents."""
        p = _payload(max_windows_per_dey=400)
        v = _verify(_signed(trusted, p))
        assert v["reason"] == "MANDATE_UNKNOWN_FIELD"
        assert "max_windows_per_dey" in v["detail"]["unknown"]

    def test_a_float_money_value_refuses(self, trusted):
        p = _payload()
        p["capital"] = dict(p["capital"], max_cash_deployed_usd=1.65)
        v = _verify(_signed(trusted, p))
        assert v["reason"] == "MANDATE_MONEY_INVALID"

    def test_an_unknown_schema_refuses(self, trusted):
        assert _verify(_signed(trusted, _payload(schema="l5.mandate.v99")),
                       seq)["reason"] == "MANDATE_SCHEMA_UNKNOWN"


# ------------------------------------- requirement 7: promotion is a block

class TestPromotionCannotEnableLive:

    def test_a_mandate_opting_out_of_live_validation_is_refused(
            self, trusted):
        p = _payload()
        p["promotion"] = dict(p["promotion"],
                              requires_bounded_live_validation=False)
        v = _verify(_signed(trusted, p))
        assert v["reason"] == "MANDATE_PROMOTION_MUST_REQUIRE_LIVE_VALIDATION"

    def test_missing_oos_evidence_id_is_refused(self, trusted):
        p = _payload()
        p["promotion"] = dict(p["promotion"], oos_evidence_id="")
        assert _verify(_signed(trusted, p))["reason"] \
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
            self, trusted):
        raw = _signed(trusted, _payload())
        t = M.telemetry(_verify(raw))
        blob = json.dumps(t)
        assert "sig" not in t and "scope" not in t and "payload" not in t
        assert json.loads(raw)["sig"] not in blob

    def test_telemetry_states_phase_1_is_not_enforcing(self, trusted):
        assert M.telemetry(_verify(_signed(trusted, _payload())))[
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
        # EXACTLY ONE write path in the whole module, and it belongs to the
        # WATCHDOG's floor writer -- not to anything the executor calls.
        assert src.count(', "w")') == 1, "a second write path appeared"
        assert 'open(tmp, "w")' in inspect.getsource(M.WatchdogSeqWriter)
        writers = sorted(n for n, f in vars(M.WatchdogSeqWriter).items()
                         if callable(f) and not n.startswith("__"))
        assert writers == ["_path", "observe", "read"]
        # the executor-facing stores are GET-only
        for cls in (M.HttpSeqFloorStore, M.HttpRevocationStore):
            assert sorted(n for n in vars(cls) if not n.startswith("__")) \
                in (["__init__"], ["floor"], ["revoked"], [])
        # and nothing here IMPORTS a store owning realized P&L, inventory,
        # the campaign budget or the halt. Checked through the import graph
        # rather than by substring, because the comments legitimately
        # reference l5_halt_gate as the asymmetry this mirrors.
        import ast
        imported = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                imported.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                imported.add(n.module or "")
                imported.update("%s.%s" % (n.module or "", a.name)
                                for a in n.names)
        for forbidden in ("l5_live_execution", "l5_campaign_budget",
                          "l5_halt_gate", "l5_campaign_approval"):
            assert not any(forbidden in m for m in imported), forbidden

    def test_only_action_rates_are_per_day(self):
        per_day = [f for f in M.REQUIRED_PAYLOAD_FIELDS if "per_day" in f]
        assert sorted(per_day) == ["max_submissions_per_day",
                                   "max_windows_per_day"]
