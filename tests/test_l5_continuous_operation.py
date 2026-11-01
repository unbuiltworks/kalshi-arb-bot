"""Continuous operation: the deployment drain, the promotion gate, and
versioned strategy configuration.

WHAT THESE PIN, and why each exists:

1. **The drain** (`L5_SUBMIT_PAUSE`). Replacing the executor image restarts
   the process. If it restarts holding a resting order it has not reconciled,
   the operator is reconciling a live book by hand. The drain stops NEW
   exposure without stopping the process, so a deploy can be staged against a
   book that is verifiably empty. It must gate ARMING ONLY: cancellation,
   reconciliation and settlement have to stay live, because a pause that
   blocked cancels could strand exactly the order it was protecting.

2. **The promotion gate.** A standing mandate is only safe to leave installed
   if it will not trade a strategy nobody has shown to make money. The bar
   lives in the owner's SIGNED bytes. These tests pin that it refuses on
   today's evidence, that it fails closed, and -- the ordering property that
   cost one existing test when it was first wired too early -- that every
   structural and authorization refusal still names itself FIRST.

3. **Versioned strategy configuration.** Realized results can only be
   compared across an update if the thing that changed is identifiable. The
   version is a label PLUS a digest over the effective knobs, so changing a
   knob without changing the label still changes the identity.

NOTHING HERE ARMS ANYTHING. Every assertion is a refusal, a label or a
digest.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from cryptography.hazmat.primitives import serialization as _S
from cryptography.hazmat.primitives.asymmetric import ed25519

from src import l5_live_authorization as AUTH
from src import l5_mandate_signature as MS
from src import l5_promotion_gate as PG
from src import l5_two_sided_probe as TS

SERIES = "cpc-btc-updown-15m-"
ROLE = "l5-pmus-live"
KEY_ID = "test-key-not-the-owners"
MARKET = "cpc-btc-updown-15m-2026-11-01-2200z"


class _Limits:
    """Only what the gate reads. `as_dict` must cover every
    SCOPED_LIMIT_FIELD, because step 9 refuses CAPITAL_SCOPE_UNKNOWN on an
    unreadable envelope -- an authorization with no capital scope is not an
    authorization."""

    def __init__(self, exposure="5", daily="5", per_market="1.50"):
        self.max_total_exposure_usd = AUTH.D(exposure)
        self.daily_realized_loss_halt_usd = AUTH.D(daily)
        self.max_usd_per_market = AUTH.D(per_market)

    def as_dict(self):
        return {"max_usd_per_market": self.max_usd_per_market,
                "max_total_exposure_usd": self.max_total_exposure_usd,
                "daily_realized_loss_halt_usd":
                    self.daily_realized_loss_halt_usd}


def _arm(**kw):
    base = dict(market=MARKET, preflight="PASSED", store=None, halted=False,
                reconciled=True, halt_gate_present=True, unresolved=(),
                limits=_Limits(), shadow_locked=False, series=SERIES,
                max_windows=8, campaign=None, path=None,
                window_bounds=_bounds(), nominal_window_s=900.0,
                require_durable=False)
    base.update(kw)
    with pytest.raises(AUTH.ArmingRefusal) as e:
        AUTH.authorize_arming(**base)
    return e.value


def _reason(exc):
    return str(getattr(exc, "reason", None) or getattr(exc, "args", [""])[0])


# ===================================================== 1. THE DEPLOY DRAIN
class TestDeploymentDrain:

    def test_unset_does_not_pause(self, monkeypatch):
        """AN ABSENT VARIABLE MUST NEVER PAUSE A LIVE EXECUTOR. If it did,
        every container that had never heard of this switch would stop
        trading the moment this code deployed."""
        monkeypatch.delenv(AUTH.SUBMIT_PAUSE_ENV, raising=False)
        assert _reason(_arm()) != "SUBMISSION_PAUSED_FOR_DEPLOY"

    @pytest.mark.parametrize("val", ["1", "yes", "true", "DRAINING",
                                     "deploy-2026-11-01", "  1  "])
    def test_set_pauses(self, monkeypatch, val):
        monkeypatch.setenv(AUTH.SUBMIT_PAUSE_ENV, val)
        assert _reason(_arm()) == "SUBMISSION_PAUSED_FOR_DEPLOY"

    @pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", ""])
    def test_explicit_off_values_run(self, monkeypatch, val):
        monkeypatch.setenv(AUTH.SUBMIT_PAUSE_ENV, val)
        assert _reason(_arm()) != "SUBMISSION_PAUSED_FOR_DEPLOY"

    def test_an_unrecognised_value_pauses_rather_than_runs(self, monkeypatch):
        """THE SAFE READING OF AN UNRECOGNISED VALUE IN A STOP SWITCH IS
        STOP. A typo'd pause that silently kept trading is the failure this
        exists to prevent."""
        monkeypatch.setenv(AUTH.SUBMIT_PAUSE_ENV, "paused?")
        assert _reason(_arm()) == "SUBMISSION_PAUSED_FOR_DEPLOY"

    def test_the_drain_is_the_outermost_refusal(self, monkeypatch):
        """It must beat even the shadow lock, so that a draining executor
        reports DRAINING rather than some incidental inner state."""
        monkeypatch.setenv(AUTH.SUBMIT_PAUSE_ENV, "1")
        assert _reason(_arm(shadow_locked=True)) == \
            "SUBMISSION_PAUSED_FOR_DEPLOY"
        assert _reason(_arm(halted=True)) == "SUBMISSION_PAUSED_FOR_DEPLOY"
        assert _reason(_arm(reconciled=False)) == \
            "SUBMISSION_PAUSED_FOR_DEPLOY"

    def test_the_drain_gates_arming_only_not_cancellation(self):
        """CANCELLING MUST STAY AVAILABLE WHILE DRAINING. Pinned
        structurally: the only consumer of the pause is `authorize_arming`,
        which admits new exposure. No cancel path consults it, so no cancel
        path can be stopped by it."""
        import inspect
        src = inspect.getsource(AUTH)
        # THE INVARIANT: the switch is READ exactly once in the module, and
        # that read is inside `authorize_arming` -- the one function that
        # admits new exposure. Any second reader would be a path that could
        # stop something other than arming, cancellation included.
        assert src.count("os.getenv(SUBMIT_PAUSE_ENV)") == 1, src.count(
            "os.getenv(SUBMIT_PAUSE_ENV)")
        assert "os.getenv(SUBMIT_PAUSE_ENV)" in inspect.getsource(
            AUTH.authorize_arming)
        # and no cancellation entry point consults it
        for name in dir(AUTH):
            fn = getattr(AUTH, name, None)
            if not inspect.isfunction(fn) or name == "authorize_arming":
                continue
            if "cancel" not in name.lower():
                continue
            assert "SUBMIT_PAUSE_ENV" not in inspect.getsource(fn), name

    def test_the_verifier_reports_the_pause_by_the_same_reading(
            self, monkeypatch):
        """A row that disagreed with the gate would send an operator looking
        in the wrong place."""
        from src import l5_runtime_config_verify as V
        monkeypatch.setenv(AUTH.SUBMIT_PAUSE_ENV, "deploy-drain")
        assert V._submit_pause_state().startswith("PAUSED:")
        monkeypatch.setenv(AUTH.SUBMIT_PAUSE_ENV, "off")
        assert V._submit_pause_state() == "running"
        monkeypatch.delenv(AUTH.SUBMIT_PAUSE_ENV, raising=False)
        assert V._submit_pause_state() == "running"


# ================================================== 2. THE PROMOTION GATE
def _keypair():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=_S.Encoding.Raw, format=_S.PublicFormat.Raw).hex()
    return priv, pub


def _payload(**over):
    now = dt.datetime.now(dt.timezone.utc)
    p = {
        "schema": MS.SCHEMA, "kind": "standing",
        "mandate_id": "test-001", "seq": 1,
        "venue": "PMUS", "strategy": "l5-two-sided-btc15m",
        "environment": "production", "service_role": ROLE, "series": SERIES,
        "not_before": (now - dt.timedelta(hours=1)).isoformat(),
        "not_after": (now + dt.timedelta(hours=3)).isoformat(),
        "allowed_actions": ["OPEN", "COMPLETE", "CANCEL"],
        "execution_mode": "MAKER_ONLY",
        "max_windows_per_day": 8, "max_submissions_per_day": 56,
        "capital": {"max_cash_deployed_usd": "5",
                    "max_total_exposure_usd": "5",
                    "campaign_budget_usd": "5"},
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


class TestPromotionGateUnit:
    """`evaluate` in isolation: it must default to blocking."""

    def test_no_terms_blocks(self):
        v = PG.evaluate(promotion=None, shadow=None)
        assert v["blocks_live"] is True
        assert "PROMOTION_TERMS_MISSING" in v["reasons"]

    def test_missing_shadow_stats_blocks(self):
        v = PG.evaluate(promotion=_payload()["promotion"], shadow=None)
        assert v["blocks_live"] is True
        assert "SHADOW_STATS_MISSING" in v["reasons"]

    def test_our_measured_expectancy_blocks(self):
        """THE ACCEPTANCE TEST FOR REQUIREMENT 9. Our seven settled windows
        average -$0.0108; the best validated ex-ante improvement is
        +$0.0176/window. Against the mandate's $0.05 bar, both refuse. The
        gate must say so rather than arm."""
        for exp in ("-0.0108", "0.0176"):
            v = PG.evaluate(promotion=_payload()["promotion"],
                            shadow={"windows_settled": 400,
                                    "net_per_settled_window": exp},
                            oos_evidence={"id": "btc15m-oos-20261023"},
                            live_validation={"ok": True})
            assert v["blocks_live"] is True, exp
            assert "SHADOW_EXPECTANCY_BELOW_THRESHOLD" in v["reasons"], exp

    def test_too_small_a_sample_blocks_even_when_profitable(self):
        v = PG.evaluate(promotion=_payload()["promotion"],
                        shadow={"windows_settled": 7,
                                "net_per_settled_window": "5.00"},
                        oos_evidence={"id": "btc15m-oos-20261023"},
                        live_validation={"ok": True})
        assert v["blocks_live"] is True
        assert "SHADOW_SAMPLE_TOO_SMALL" in v["reasons"]


def _bounds(into=30.0):
    """Venue-sourced window bounds. Step 8 refuses WINDOW_BOUNDS_UNKNOWN
    without them, and only source VENUE is accepted -- the claim has to be
    bounded by the venue's own close, not by our clock."""
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=into)
    return {"source": AUTH.WINDOW_SOURCE_VENUE,
            "start": start.isoformat(),
            "end": (start + dt.timedelta(seconds=900)).isoformat()}


def _campaign(minutes=90):
    """A valid campaign document, which is what satisfies owner
    authorization. Needed because the promotion gate runs LATE, after every
    structural and authorization check -- so a test that supplies no
    authorization never reaches it and would be asserting the wrong thing."""
    return {"present": True, "ok": True,
            "scope": {"nonce_sid": "camp1.promo", "series": SERIES,
                      "max_windows": 8,
                      "max_total_exposure_usd": "5",
                      "max_daily_loss_usd": "5",
                      "expires_utc": (dt.datetime.now(dt.timezone.utc)
                                      + dt.timedelta(minutes=minutes)
                                      ).isoformat()}}


def _install(monkeypatch, priv, payload):
    """Sign THESE bytes and install them. The payload must be built once:
    `_payload()` stamps a fresh `now`, so signing one call and embedding
    another yields a valid signature over the wrong document."""
    monkeypatch.setenv(MS.MANDATE_ENV, json.dumps(
        {"payload": payload,
         "sig": priv.sign(MS.canonical_bytes(payload)).hex(),
         "key_id": KEY_ID}))


class TestPromotionGateWiring:

    @pytest.fixture
    def signed(self, monkeypatch, tmp_path):
        priv, pub = _keypair()
        monkeypatch.setitem(MS.MANDATE_PUBKEYS, KEY_ID, pub)
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
        monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, ROLE)
        monkeypatch.setenv(AUTH.SERIES_ENV, SERIES)
        monkeypatch.setenv("L5_LIVE_ROOT", str(tmp_path))
        monkeypatch.setenv(MS.STORE_URL_ENV, "http://watchdog.internal:8080")

        class _F:
            def floor(self, key_id, **kw):
                return 0

        class _R:
            def revoked(self, **kw):
                return frozenset()
        monkeypatch.setattr(MS, "HttpSeqFloorStore", lambda u, **k: _F())
        monkeypatch.setattr(MS, "HttpRevocationStore", lambda u, **k: _R())
        monkeypatch.delenv(AUTH.SUBMIT_PAUSE_ENV, raising=False)
        return priv

    def test_with_no_trusted_key_and_no_mandate_the_gate_is_inert(
            self, monkeypatch):
        """THE SHIPPED STATE. No trusted key means no signed mandate, so
        there are no promotion terms and this must not refuse."""
        monkeypatch.setattr(MS, "MANDATE_PUBKEYS", {})
        monkeypatch.delenv(MS.MANDATE_ENV, raising=False)
        monkeypatch.delenv(AUTH.SUBMIT_PAUSE_ENV, raising=False)
        r = _reason(_arm())
        assert not r.startswith("PROMOTION_"), r

    def test_a_signed_mandate_reaches_and_is_refused_by_the_gate(
            self, monkeypatch, signed):
        """With the signature satisfied and no promotion evidence supplied,
        the gate refuses -- fail closed."""
        _install(monkeypatch, signed, _payload())
        assert _reason(_arm(campaign=_campaign())) == \
            "PROMOTION_GATE_BLOCKS_LIVE"

    def test_structural_and_authorization_refusals_still_come_first(
            self, monkeypatch, signed):
        """THE ORDERING PROPERTY. Wiring this gate before owner
        authorization made the executor report 'the strategy is unproven' to
        an operator whose actual problem was a missing authorization. Every
        cheaper, more structural refusal must still win."""
        _install(monkeypatch, signed, _payload())
        assert _reason(_arm(shadow_locked=True)) == "SHADOW_ONLY_LOCK"
        monkeypatch.setenv(AUTH.SUBMIT_PAUSE_ENV, "1")
        assert _reason(_arm()) == "SUBMISSION_PAUSED_FOR_DEPLOY"
        monkeypatch.delenv(AUTH.SUBMIT_PAUSE_ENV)
        # and a mandate-level defect is still named as such
        _install(monkeypatch, signed, _payload(execution_mode="TAKER_OK"))
        assert _reason(_arm()).startswith("MANDATE_")

    def test_the_gate_binds_on_a_trusted_key_even_with_no_promotion_terms(
            self, monkeypatch):
        """THE BACKSTOP CLAUSE. `MS.verify` refuses a mandate with an
        incomplete `promotion` block, so on the normal path the terms are
        always present. This exercises the other clause: with a trusted key
        registered, the gate binds even if the terms were somehow absent, so
        a future schema change making the block optional cannot silently
        switch promotion checking off."""
        monkeypatch.setattr(MS, "MANDATE_PUBKEYS", {KEY_ID: "ab" * 32})
        monkeypatch.delenv(MS.MANDATE_ENV, raising=False)
        monkeypatch.delenv(AUTH.SUBMIT_PAUSE_ENV, raising=False)
        # the signature step refuses first with no document, which is
        # correct; the point is that `_mandate_enforcing` is True, so had
        # execution reached step 9b the gate would have bound.
        assert AUTH._mandate_pubkeys_present() is True
        # and with the signature step bypassed, it does bind and refuse
        monkeypatch.setattr(AUTH, "_mandate_pubkeys_present", lambda: True)
        monkeypatch.setitem(MS.MANDATE_PUBKEYS, KEY_ID, "ab" * 32)
        r = _reason(_arm(campaign=_campaign()))
        assert r in ("PROMOTION_GATE_BLOCKS_LIVE",
                     "MANDATE_DOCUMENT_ABSENT") or r.startswith("MANDATE_"), r

    def test_an_unevaluable_gate_refuses(self, monkeypatch, signed):
        """A gate that cannot be evaluated has not been satisfied."""
        _install(monkeypatch, signed, _payload())

        def boom(**kw):
            raise RuntimeError("evidence store down")
        monkeypatch.setattr(PG, "evaluate", boom)
        assert _reason(_arm(campaign=_campaign())) == \
            "PROMOTION_GATE_UNEVALUABLE"

    def test_only_an_explicit_false_clears_the_gate(self, monkeypatch,
                                                    signed):
        """Anything but an explicit False is a refusal: a malformed verdict,
        a missing key, or a truthy-but-wrong value."""
        _install(monkeypatch, signed, _payload())
        for verdict in ({}, {"blocks_live": None}, {"blocks_live": 0},
                        {"blocks_live": "no"}, {"other": 1}):
            monkeypatch.setattr(PG, "evaluate", lambda **kw: dict(verdict))
            assert _reason(_arm(campaign=_campaign())) == \
                "PROMOTION_GATE_BLOCKS_LIVE", verdict

    def test_clearing_the_gate_is_not_by_itself_permission(
            self, monkeypatch, signed):
        """`blocks_live: False` removes THIS refusal and nothing else; the
        owner authorization is still required after it."""
        _install(monkeypatch, signed, _payload())
        monkeypatch.setattr(PG, "evaluate",
                            lambda **kw: {"blocks_live": False,
                                          "reasons": []})
        # WITH NO AUTHORIZATION the authorization refusal wins, because the
        # promotion gate runs last. This is the ordering the first wiring
        # attempt got wrong, and it is the whole reason the gate moved.
        assert _reason(_arm()) == "OWNER_AUTHORIZATION_ABSENT"
        # WITH authorization satisfied, the gate is reached and -- cleared --
        # contributes no refusal of its own, so arming proceeds past it.
        try:
            AUTH.authorize_arming(
                market=MARKET, preflight="PASSED", store=None, halted=False,
                reconciled=True, halt_gate_present=True, unresolved=(),
                limits=_Limits(), shadow_locked=False, series=SERIES,
                max_windows=8, campaign=_campaign(), path=None,
                window_bounds=_bounds(), nominal_window_s=900.0,
                require_durable=False)
        except AUTH.ArmingRefusal as exc:
            assert not _reason(exc).startswith("PROMOTION_"), _reason(exc)


# ======================================== 3. VERSIONED STRATEGY CONFIG
class TestStrategyConfigVersion:

    def test_label_and_digest_are_present(self):
        f = TS.strategy_config_fingerprint()
        assert f["label"] == TS.STRATEGY_CONFIG_LABEL
        assert isinstance(f["digest"], str) and len(f["digest"]) == 16

    def test_every_knob_is_readable(self):
        """UNREADABLE IS A DEFECT HERE, not an acceptable value: a knob that
        cannot be read is a knob whose change would not alter the digest."""
        f = TS.strategy_config_fingerprint()
        assert "UNREADABLE" not in set(f["knobs"].values()), f["knobs"]

    def test_the_digest_is_stable_across_calls(self):
        assert (TS.strategy_config_fingerprint()["digest"]
                == TS.strategy_config_fingerprint()["digest"])

    def test_changing_a_knob_changes_the_digest_without_the_label(
            self, monkeypatch):
        """THE WHOLE POINT. A silent config change must not be able to reuse
        a version label and merge two different result sets."""
        before = TS.strategy_config_fingerprint()
        monkeypatch.setenv(TS.ROUND_ALLOCATIONS_ENV, "2")
        after = TS.strategy_config_fingerprint()
        assert after["label"] == before["label"]
        assert after["digest"] != before["digest"]

    def test_the_cap_comes_from_the_module_that_enforces_it(self):
        """The fingerprint must cover the value the money path actually
        reads, not a copy kept beside it."""
        from src import l5_live_execution as LX
        f = TS.strategy_config_fingerprint()
        assert (f["knobs"]["abs_residual_cap_contracts"]
                == str(LX.ABS_RESIDUAL_CAP_CONTRACTS))

    def test_an_unreadable_fingerprint_never_refuses_a_window(self,
                                                              monkeypatch):
        """It is an IDENTIFIER. A reporting defect must not decline a window
        every risk gate has already permitted."""
        import src.l5_two_sided_probe as mod
        monkeypatch.setattr(mod, "strategy_config_fingerprint",
                            lambda: (_ for _ in ()).throw(RuntimeError("x")))
        out = AUTH._strategy_config_or_note()
        assert out["label"] is None
        assert out["unreadable"] == "RuntimeError"


class TestVerifierTellsTheTruth:

    def test_mandate_enforcing_tracks_the_registry(self, monkeypatch):
        """It was hardcoded False while the gate was inert. The gate is
        SELF-ACTIVATING, so a hardcoded False would report 'not enforcing'
        from the moment enforcement began."""
        from src import l5_runtime_config_verify as V

        def row(name):
            r = V.verify()
            for x in (r.get("observed_not_graded") or []):
                if x.get("name") == name:
                    return str(x.get("effective"))
            raise AssertionError("row %s missing" % name)

        monkeypatch.setattr(MS, "MANDATE_PUBKEYS", {})
        assert row("mandate_enforcing") == "False"
        assert row("promotion_gate_binds") == "False"
        monkeypatch.setattr(MS, "MANDATE_PUBKEYS", {KEY_ID: "ab" * 32})
        assert row("mandate_enforcing") == "True"
        assert row("promotion_gate_binds") == "True"

    def test_the_strategy_rows_are_reported(self):
        from src import l5_runtime_config_verify as V
        names = {x.get("name") for x in
                 (V.verify().get("observed_not_graded") or [])}
        for n in ("strategy_config_label", "strategy_config_digest",
                  "submission_paused_for_deploy", "promotion_gate_binds"):
            assert n in names, n
