"""THE TEST WE WERE MISSING: the ACTUAL PRODUCTION ARMED PATH, end to end.

Every previous suite started at `run_cohort` or below with a provider
injected. None started at `main()` ARMED -- which is exactly why
`e11fc1ab…` shipped with an armed branch that was a bare `raise
SystemExit`, no `snapshot_provider`, and a declared call graph naming a
`run_cohort` the code never reached. Twelve empty windows, and 110 green
tests.

So these tests invoke `runner.main()` with the COMPLETE arming environment
-- `PMUS_E1_LIVE=YES`, `PMUS_E1_SESSION_ID`, `PMUS_E1_ARM`,
`PMUS_E1_GATE_EVIDENCE`, and the fingerprint pin set to THIS build -- with
fake credentials, a fake selector, a fake two-token book and a FAKE
TRANSPORT, and drive it across MULTIPLE WINDOWS and MULTIPLE ORDERS.

NO REAL POST IS POSSIBLE HERE. The transport is a fake, the selector never
calls the venue, the book is a dict, and `PMUS_E1_*` is set only inside
`monkeypatch` for the duration of a test.

Run:
    PYTHONPATH=. python3 -m pytest analysis/l5/e1_cohort/test_e1_live_path.py -q
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import arming as AR
from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import progress as PG
from analysis.l5.e1_cohort import provider as PV
from analysis.l5.e1_cohort import recorder as R
from analysis.l5.e1_cohort import runner as RUN
from analysis.l5.e1_cohort import selector as SEL
from analysis.l5.e1_cohort import spec as SP
from analysis.l5.e1_cohort import stopping as ST
from analysis.l5.live_exec2 import orders as O

W = SEL.WINDOW_S                                                    # 900
BASE = 1800000000 - (1800000000 % W)         # a window start on the grid


# ----------------------------------------------------------------- fakes

class FakeTransport:
    """The only transport these tests ever construct. Echoes the preview,
    accepts one submission per instance exactly as the real one does."""

    posts = []                      # class-level: every POST, every instance

    def __init__(self, *, submit_raises=None):
        self.submit_raises = submit_raises
        self.calls = []
        self._n = 0

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if path == O.PREVIEW_PATH:
            return {"order": dict(body["request"])}
        if path == O.SUBMIT_PATH:
            self._n += 1
            if self._n > 1:
                raise O.MutationRefused("second POST refused")
            if self.submit_raises:
                raise self.submit_raises
            FakeTransport.posts.append({
                "n": len(FakeTransport.posts) + 1,
                "slug": body["marketSlug"],
                "quantity": body["quantity"],
                "price": body["price"]["value"],
                "post_only": body["participateDontInitiate"]})
            return {"id": "order-%d" % len(FakeTransport.posts)}
        return {}


def fake_pin(window_start, *, slug=None, up=None, down=None):
    ws = int(window_start)
    return {
        "series": SEL.SERIES,
        "slug": slug or ("btc-updown-15m-%d" % ws),
        "market_id": "0xcond%d" % ws,
        "condition_id": "0xcond%d" % ws,
        "token_up": up or ("tok-up-%d" % ws),
        "token_down": down or ("tok-down-%d" % ws),
        "tokens": {(up or "tok-up-%d" % ws): "UP",
                   (down or "tok-down-%d" % ws): "DOWN"},
        "window_start": ws, "window_end": ws + W,
        "question": "BTC up or down?",
        "selection_provenance": {"selector": "FAKE_FOR_TEST",
                                 "resolver": "FAKE_FOR_TEST",
                                 "series_pinned": SEL.SERIES,
                                 "window_grid_s": W},
    }


class Clock:
    def __init__(self, t): self.t = float(t)
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


def fake_selector(window_start, **kw):
    return fake_pin(window_start)


def fake_health(pin, *, now_fn=None, both=True, derived=False):
    t = (now_fn or (lambda: 0.0))()
    seen = {}
    if both:
        seen = {str(pin["token_up"]): t, str(pin["token_down"]): t}
    return {"market": pin["slug"],
            "timing_authority": "websocket_strict",
            "both_tokens_streaming": both,
            "tokens_last_seen_unix": seen,
            "frames": 11438, "ws_url": PV.WS.DEFAULT_WS_URL,
            "derived_complement": derived,
            "rest_role": "rest_same_second_reachability"}


def fake_book(pin, *, now_fn=None, up_token=None, market=None):
    t = (now_fn or (lambda: 0.0))()
    return {"market": market or pin["slug"],
            "book_unix": t, "recv_monotonic_ns": 1,
            "up": {"token_id": up_token or pin["token_up"],
                   "best_bid": "0.50", "best_ask": "0.52",
                   "size_at_our_price": "4.00",
                   "size_ahead_of_us": "3.87"},
            "down": {"token_id": pin["token_down"],
                     "best_bid": "0.46", "best_ask": "0.48"},
            "within_handover_tte": True,
            "our_outstanding_orders": 0, "our_open_residual": None}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Own state dir, own session, no real credential, clean POST log."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    for k in AR.LEGACY_XR2W_KEYS_NEVER_READ:
        monkeypatch.delenv(k, raising=False)
    yield


def arm(monkeypatch, sid="e1-live-test-session"):
    """THE COMPLETE ARMING ENVIRONMENT. Fake values, test-scoped only."""
    monkeypatch.setenv("PMUS_E1_LIVE", "YES")
    monkeypatch.setenv("PMUS_E1_SESSION_ID", sid)
    monkeypatch.setenv("PMUS_E1_ARM", sid)
    monkeypatch.setenv("PMUS_E1_GATE_EVIDENCE", AUTH.AUTHORIZED_SERIES)
    monkeypatch.setenv(AR.FINGERPRINT_PIN_ENV, SP.E1_FINGERPRINT)
    # THE PRODUCTION CREDENTIAL CONTRACT, with a THROWAWAY key.
    #
    # This used to set `PMUS_API_SECRET`, which NO CODE READS -- the real
    # name is `PMUS_API_SECRET_KEY` -- and it did not matter, because
    # FakeTransport never signs anything. That gap is how the 2026-10-07
    # incident stayed invisible to a green suite: the armed path was tested
    # end to end without ever exercising the signing contract.
    #
    # So the armed harness now satisfies the SAME contract production does,
    # with a key generated here and belonging to nobody.
    monkeypatch.setenv("PMUS_API_KEY_ID", "e1-test-key-id")
    monkeypatch.setenv("PMUS_API_SECRET_KEY", throwaway_secret_b64())
    monkeypatch.delenv("PMUS_API_SECRET", raising=False)
    return sid


def throwaway_secret_b64() -> str:
    """A real, usable, worthless Ed25519 seed. `pmus_auth` takes the first
    32 bytes of the decoded value as the private seed."""
    import base64
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    k = ed25519.Ed25519PrivateKey.generate()
    raw = k.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    return base64.b64encode(raw).decode()


def drive_main(monkeypatch, tmp_path, *, clock, starts, health=None,
               book=None, selector=None, sid="e1-live-test-session",
               transport=FakeTransport):
    """Invoke the REAL `runner.main()` on its ARMED branch, with fakes."""
    arm(monkeypatch, sid)
    state = str(tmp_path / "e1_cohort")
    captured = {}

    def provider_factory(*, progress, session_auth, recorder):
        p = PV.LiveWindowProvider(
            progress=progress, session_auth=session_auth,
            book_fn=book or (lambda pin: fake_book(pin, now_fn=clock)),
            health_fn=health or (lambda pin: fake_health(pin,
                                                         now_fn=clock)),
            recorder=recorder, selector_fn=selector or fake_selector,
            now_fn=clock, window_starts=list(starts))
        captured["provider"] = p
        captured["progress"] = progress
        captured["auth"] = session_auth
        captured["recorder"] = recorder
        return p

    monkeypatch.setattr(RUN, "run_cohort", _recording_run_cohort(captured))
    rc = RUN.main(
        [], provider_factory=provider_factory,
        progress_factory=lambda d: PG.CohortProgress(d),
        auth_factory=lambda d: AUTH.SessionAuthorization(d),
        recorder_factory=lambda d: R.Recorder(os.path.join(d, "tape")),
        transport_factory=transport)
    captured["rc"] = rc
    return captured


def _recording_run_cohort(captured):
    """Wrap the REAL run_cohort so we can prove main() reached it, without
    replacing what it does."""
    real = RUN.run_cohort

    def wrapper(**kw):
        captured["run_cohort_called"] = True
        captured["run_cohort_kwargs"] = {k: v for k, v in kw.items()
                                         if k != "snapshot_provider"}
        out = real(**kw)
        captured["run_cohort_out"] = out
        return out
    return wrapper


# ============== PROOF 1: ARMED main() ACTUALLY REACHES run_cohort =========

def test_armed_main_reaches_run_cohort(monkeypatch, tmp_path, capsys):
    clock = Clock(BASE + 10)
    cap = drive_main(monkeypatch, tmp_path, clock=clock, starts=[BASE])
    assert cap["rc"] == 0
    assert cap.get("run_cohort_called") is True, \
        "armed main() did NOT reach run_cohort -- the e11fc1ab defect"
    out = cap["run_cohort_out"]
    assert out["placement_count"] >= 2, out["placement_count"]
    assert len(FakeTransport.posts) >= 2
    assert FakeTransport.posts[1]["n"] == 2
    assert all(p["quantity"] == 0.13 for p in FakeTransport.posts)
    assert all(p["post_only"] is True for p in FakeTransport.posts)
    # it printed the ARMED banner and entered run_cohort
    printed = capsys.readouterr().out
    assert '"ARMED": true' in printed
    assert '"entering": "run_cohort"' in printed
    # and the armed branch is NOT a bare raise
    assert SP.SPEC["live_provider"]["armed_main_is_a_bare_raise"] is False
    assert SP.SPEC["live_provider"]["armed_main_reaches_run_cohort"] is True


def test_the_armed_branch_is_not_a_bare_raise_in_the_source():
    """The defect, asserted structurally: main()'s armed branch must build
    a provider and call run_cohort."""
    import inspect
    src = inspect.getsource(RUN.main)
    body = src.split('"""')[2] if src.count('"""') >= 2 else src
    assert "run_cohort(" in body
    assert "provider_factory(" in body
    assert "rehydrate(" in body
    # the old bare raise is gone
    assert "requires the owner's authorized manifest and session id" \
        not in body


# ====== PROOF 2: THE SECOND WINDOW GETS A DIFFERENT AUTHORIZED SLUG ======

def test_two_distinct_windows_get_two_distinct_authorized_slugs(
        monkeypatch, tmp_path):
    """Drive the armed path across TWO windows and prove the second is a
    DIFFERENT authorized market."""
    clock = Clock(BASE + 10)
    starts = [BASE, BASE + W]

    # the first window fills its 20 slots, then the clock rolls past its
    # end, so the provider must open window 2
    calls = {"n": 0}

    def book(pin):
        calls["n"] += 1
        if calls["n"] > 20:
            clock.t = BASE + W + 10        # roll into the second window
        return fake_book(pin, now_fn=clock)

    cap = drive_main(monkeypatch, tmp_path, clock=clock, starts=starts,
                     book=book)
    prog = cap["progress"]
    auth = cap["auth"]
    windows = prog.windows_opened()
    assert len(windows) == 2, [w["slug"] for w in windows]
    slugs = [w["slug"] for w in windows]
    assert slugs[0] != slugs[1], slugs
    assert slugs == ["btc-updown-15m-%d" % BASE,
                     "btc-updown-15m-%d" % (BASE + W)]
    # both are in the session authorization ledger
    assert sorted(auth.slugs()) == sorted(slugs)
    for w in windows:
        assert w["authorization_result"] == "AUTHORIZED"
    # the market ids and BOTH token ids differ too -- a different market,
    # not a renamed one
    assert windows[0]["market_id"] != windows[1]["market_id"]
    assert windows[0]["token_up"] != windows[1]["token_up"]
    assert windows[0]["token_down"] != windows[1]["token_down"]
    # orders went to BOTH slugs
    posted = {p["slug"] for p in FakeTransport.posts}
    assert posted == set(slugs), posted
    assert len(FakeTransport.posts) > 20


def test_the_raw_tape_records_per_window_identity_and_authorization(
        monkeypatch, tmp_path):
    clock = Clock(BASE + 10)
    calls = {"n": 0}

    def book(pin):
        calls["n"] += 1
        if calls["n"] > 20:
            clock.t = BASE + W + 10
        return fake_book(pin, now_fn=clock)

    cap = drive_main(monkeypatch, tmp_path, clock=clock,
                     starts=[BASE, BASE + W], book=book)
    rows = cap["recorder"].read(R.EVENTS)
    authz = [r for r in rows if r["event"] == "WINDOW_AUTHORIZED"]
    assert len(authz) == 2
    for r in authz:
        d = r["detail"]
        # selected slug, market ID, BOTH token IDs, selection provenance,
        # authorization result
        assert d["slug"].startswith("btc-updown-15m-")
        assert d["market_id"]
        assert d["token_up"] and d["token_down"]
        assert d["token_up"] != d["token_down"]
        assert d["selection_provenance"]["series_pinned"] == SEL.SERIES
        assert d["authorization_result"] == "AUTHORIZED"
        assert d["websocket_health"]["both_tokens_streaming"] is True
        assert d["websocket_health"]["timing_authority"] == "websocket_strict"
    assert authz[0]["detail"]["slug"] != authz[1]["detail"]["slug"]


# ======= PROOF 3: AN UNSELECTED / NON-BTC15m SLUG IS REFUSED ============

@pytest.mark.parametrize("bad_slug", [
    "will-trump-win-2028",                 # another market entirely
    "btc-updown-1h-1800000000",            # wrong series
    "eth-updown-15m-1800000000",           # wrong asset
    "btc-updown-15m-9999999999",           # right series, unselected window
])
def test_an_unselected_or_non_btc15m_slug_is_refused_before_transport(
        monkeypatch, tmp_path, bad_slug):
    sid = arm(monkeypatch)
    state = str(tmp_path / "e1_cohort")
    auth = AUTH.SessionAuthorization(state)
    good = fake_pin(BASE)
    auth.authorize_selected(good)

    # BOUNDARY 2 refuses it, and no transport is ever constructed
    with pytest.raises(AUTH.NotAuthorized) as ei:
        auth.enforce_before_submission(slug=bad_slug, pin=good)
    assert "Refused BEFORE transport" in str(ei.value)
    assert FakeTransport.posts == []

    # and through the real submission path, with a transport that would
    # EXPLODE if it were reached
    class Exploding:
        def request(self, *a, **k):
            raise AssertionError("TRANSPORT REACHED for an unauthorized slug")

    snap = dict(_snapshot_from(good), market=bad_slug)
    tr = ST.CohortTracker()
    monkeypatch.setattr(RUN.A, "armed_env_summary",
                        lambda: {"armed": True, "keys_present": {}})
    monkeypatch.setattr(O, "assert_frozen", lambda *a, **k: None)
    with pytest.raises(AUTH.NotAuthorized):
        RUN.submit_first_leg(snap, tr, now=BASE + 10,
                             transport_factory=Exploding,
                             session_auth=auth)
    # no latch slot was spent either
    assert L.window_state(window=0)["attempts_spent"] == 0


def test_a_non_btc15m_slug_cannot_even_be_authorized(monkeypatch, tmp_path):
    arm(monkeypatch)
    auth = AUTH.SessionAuthorization(str(tmp_path / "e1_cohort"))
    for bad in ("will-trump-win-2028", "eth-updown-15m-%d" % BASE,
                "btc-updown-1h-%d" % BASE):
        pin = fake_pin(BASE, slug=bad)
        with pytest.raises((AUTH.NotAuthorized, SEL.WindowRefused)):
            auth.authorize_selected(pin)
    assert auth.slugs() == []


def test_the_selector_refuses_a_non_btc15m_resolution():
    with pytest.raises(SEL.WindowRefused):
        SEL._check_series("will-trump-win-2028", BASE)
    with pytest.raises(SEL.WindowRefused):
        SEL._check_series("eth-updown-15m-%d" % BASE, BASE)
    # right series, WRONG window -- the resolver and the grid disagree
    with pytest.raises(SEL.WindowRefused):
        SEL._check_series("btc-updown-15m-%d" % (BASE + W), BASE)
    SEL._check_series("btc-updown-15m-%d" % BASE, BASE)


def test_a_complement_cannot_select_another_market(monkeypatch, tmp_path):
    arm(monkeypatch)
    auth = AUTH.SessionAuthorization(str(tmp_path / "e1_cohort"))
    a, b = fake_pin(BASE), fake_pin(BASE + W)
    auth.authorize_selected(a)
    auth.authorize_selected(b)
    # even though BOTH are authorized, a complement may only belong to its
    # OWN first leg's market
    with pytest.raises(AUTH.NotAuthorized) as ei:
        auth.authorize_complement(slug=b["slug"], first_leg_pin=a)
    assert "cannot select a market" in str(ei.value)
    ok = auth.authorize_complement(slug=a["slug"], first_leg_pin=a)
    assert ok["inherits_first_leg_authorization"] is True
    assert ok["complement_selected_its_own_market"] is False


def _snapshot_from(pin, *, window=0):
    return {"market": pin["slug"], "pin": pin, "window": window,
            "window_open": True, "within_handover_tte": True,
            "our_outstanding_orders": 0, "our_open_residual": None,
            "qty": SP.FIRST_LEG_QTY, "first_leg_price": "0.50",
            "first_leg": {"side": "UP", "token_id": pin["token_up"],
                          "best_bid": "0.50", "best_ask": "0.52"},
            "complement": {"side": "DOWN", "token_id": pin["token_down"],
                           "best_bid": "0.46", "best_ask": "0.48"},
            "fingerprint_pinned": {"matches_this_build": True},
            "ws_state": {"timing_authority": "websocket_strict",
                         "both_tokens_streaming": True,
                         "book_unix": BASE + 10,
                         "first_leg": {"best_bid": "0.50",
                                       "best_ask": "0.52"},
                         "complement": {"best_bid": "0.46",
                                        "best_ask": "0.48"}}}


# ============ PROOF 4: A RESTART RESUMES, IT DOES NOT RESET ==============

def test_a_restart_resumes_rather_than_resets_the_cohort(monkeypatch,
                                                         tmp_path):
    """Run armed main() twice against the SAME state dir and session. The
    second run must NOT get a fresh cohort."""
    sid = "restart-session"
    clock = Clock(BASE + 10)
    cap1 = drive_main(monkeypatch, tmp_path, clock=clock, starts=[BASE],
                      sid=sid)
    first_windows = cap1["progress"].windows_used()
    first_placements = cap1["progress"].placements()
    posts_after_1 = len(FakeTransport.posts)
    assert first_windows == 1 and first_placements >= 2

    # --- RESTART: a brand new process would build fresh objects, so we do
    clock2 = Clock(BASE + 20)
    cap2 = drive_main(monkeypatch, tmp_path, clock=clock2, starts=[BASE],
                      sid=sid)
    prog2 = cap2["progress"]
    # the SAME window is still the only one used -- not a second one
    assert prog2.windows_used() == 1, prog2.slugs_used()
    assert prog2.windows_remaining() == SP.WINDOWS - 1
    # placements CARRIED OVER rather than resetting to zero
    assert prog2.placements() >= first_placements
    rehydrated = cap2["run_cohort_kwargs"]["tracker"]
    assert rehydrated.placements >= first_placements, \
        "the restarted tracker reset its placement counter"
    # the window's 20 named latch slots were already spent, so the restart
    # adds no further orders in it
    assert L.window_state(window=0)["exhausted"] is True
    assert len(FakeTransport.posts) == posts_after_1
    assert prog2.report()["a_restart_grants_another_12_windows"] is False


def test_twelve_restarts_do_not_grant_twelve_cohorts(monkeypatch, tmp_path):
    sid = "many-restarts"
    for i in range(12):
        clock = Clock(BASE + 10 + i)
        cap = drive_main(monkeypatch, tmp_path, clock=clock, starts=[BASE],
                         sid=sid)
    prog = cap["progress"]
    assert prog.windows_used() == 1
    assert prog.windows_remaining() == SP.WINDOWS - 1
    # and the placement bound held across all twelve runs
    assert prog.placements() <= SP.MAX_ATTEMPTS_PER_WINDOW
    assert len(FakeTransport.posts) <= SP.MAX_ATTEMPTS_PER_WINDOW


def test_progress_rehydrates_risk_counters_not_just_windows(tmp_path,
                                                            monkeypatch):
    arm(monkeypatch)
    state = str(tmp_path / "e1_cohort")
    p1 = PG.CohortProgress(state)
    p1.record_window_opened(window_index=0, window_start=BASE,
                            slug="btc-updown-15m-%d" % BASE,
                            market_id="m", token_up="u", token_down="d")
    for i in range(5):
        p1.record_placement(window_index=0, slot=i, notional_usd="0.078",
                            slug="btc-updown-15m-%d" % BASE)
    p1.record_residual_opened(window_index=0, slot=0, filled_qty="0.13",
                              fill_price_usd="0.60")
    p1.record_confirmed_cash(amount_usd="-1.25",
                             source_identity={"sufficient": True})
    # RESTART
    p2 = PG.CohortProgress(state)
    tr = ST.CohortTracker()
    rep = p2.rehydrate(tr)
    assert rep["A_RESTART_RESUMES_IT_DOES_NOT_RESET"] is True
    assert tr.placements == 5
    assert tr.window_exposure_usd[0] == D("0.390")
    assert tr.realized_loss_usd == D("1.25")
    assert tr.unresolved_residual_max_loss_usd() == D("0.0780")
    # the forward-looking invariant survives the restart
    fwd = tr.forward_looking_report(prospective_attempts=1)
    assert D(fwd["forward_looking_total_usd"]) == D("1.25") + D("0.078") \
        + D("0.078")
    assert p2.report()["reset_path_exists"] is False


def test_progress_has_no_reset_path():
    """CODE only -- the docstring legitimately says the words 'truncates'
    and 'rewinds' in order to promise it does neither."""
    import ast
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "progress.py")
    src = open(path, encoding="utf8").read()
    tree = ast.parse(src)
    code = src
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                code = code.replace(d, "")
    code = "\n".join(ln for ln in code.splitlines()
                     if not ln.strip().startswith("#"))
    for destructive in ("os.remove", "os.unlink", "shutil.rmtree",
                        "os.rmdir", "os.rename", "truncate", '"w"'):
        assert destructive not in code, destructive
    assert '"a"' in code                      # append only


# ================= THE THREE FAIL-CLOSED PROOFS =========================

def test_FAILS_CLOSED_stale_window_rollover(monkeypatch, tmp_path):
    """A pinned window whose end has passed is CLOSED, never reused."""
    arm(monkeypatch)
    state = str(tmp_path / "e1_cohort")
    clock = Clock(BASE + 10)
    prog = PG.CohortProgress(state)
    auth = AUTH.SessionAuthorization(state)
    p = PV.LiveWindowProvider(
        progress=prog, session_auth=auth,
        book_fn=lambda pin: fake_book(pin, now_fn=clock),
        health_fn=lambda pin: fake_health(pin, now_fn=clock),
        selector_fn=fake_selector, now_fn=clock,
        window_starts=[BASE, BASE + W])
    s1 = p()
    assert s1 is not None and s1["market"] == "btc-updown-15m-%d" % BASE
    first_slug = s1["market"]
    # the window ENDS
    clock.t = BASE + W + 1
    s2 = p()
    assert s2 is not None
    assert s2["market"] != first_slug, "a stale window was reused"
    assert s2["market"] == "btc-updown-15m-%d" % (BASE + W)
    assert any(r["refused"] == "STALE_WINDOW_ROLLED_OVER"
               for r in p.refusals)
    closed = [e for e in prog.events if e["event"] == PG.EV_WINDOW_CLOSED]
    assert closed and closed[0]["reason"] == "WINDOW_END_PASSED"
    # and with NO successor window available, it yields NOTHING rather than
    # a stale snapshot
    clock.t = BASE + 3 * W
    p._explicit_starts = []
    assert p() is None


def test_FAILS_CLOSED_websocket_loss_before_submission(monkeypatch,
                                                       tmp_path):
    """Both-token health must be PROVEN for the SELECTED market. Lose it and
    there is NO snapshot -- not a degraded one."""
    arm(monkeypatch)
    state = str(tmp_path / "e1_cohort")
    clock = Clock(BASE + 10)
    healthy = {"v": True}
    p = PV.LiveWindowProvider(
        progress=PG.CohortProgress(state),
        session_auth=AUTH.SessionAuthorization(state),
        book_fn=lambda pin: fake_book(pin, now_fn=clock),
        health_fn=lambda pin: fake_health(pin, now_fn=clock,
                                          both=healthy["v"]),
        selector_fn=fake_selector, now_fn=clock, window_starts=[BASE])
    assert p() is not None
    healthy["v"] = False                      # the websocket drops
    assert p() is None, "a snapshot was produced without both-token health"
    assert any("WEBSOCKET_LOST_BEFORE_SUBMISSION" in r["refused"]
               for r in p.refusals)
    # a SILENT token also fails closed
    healthy["v"] = True
    clock.t = BASE + 10 + PV.MAX_TOKEN_SILENCE_S + 1
    stale = fake_health(fake_pin(BASE), now_fn=lambda: BASE + 10)
    p.health_fn = lambda pin: stale
    assert p() is None
    # REST is NOT sufficient timing authority
    p.health_fn = lambda pin: dict(
        fake_health(pin, now_fn=clock),
        timing_authority="rest_same_second_reachability")
    assert p() is None
    # and a DERIVED complement is not a second token
    p.health_fn = lambda pin: fake_health(pin, now_fn=clock, derived=True)
    assert p() is None


def test_FAILS_CLOSED_slug_or_token_mismatch(monkeypatch, tmp_path):
    """A book for another market, or a token that is not this window's,
    yields no snapshot."""
    arm(monkeypatch)
    state = str(tmp_path / "e1_cohort")
    clock = Clock(BASE + 10)

    # each sub-case gets its OWN state dir, so a window consumed by one
    # does not make the next refuse for the wrong reason
    def mk(book_fn, tag):
        d = str(tmp_path / ("e1_" + tag))
        os.makedirs(d, exist_ok=True)
        return PV.LiveWindowProvider(
            progress=PG.CohortProgress(d),
            session_auth=AUTH.SessionAuthorization(d),
            book_fn=book_fn,
            health_fn=lambda pin: fake_health(pin, now_fn=clock),
            selector_fn=fake_selector, now_fn=clock,
            window_starts=[BASE])

    # the book is for ANOTHER market
    p = mk(lambda pin: fake_book(pin, now_fn=clock,
                                 market="btc-updown-15m-%d" % (BASE + W)),
           "othermarket")
    assert p() is None
    assert any(r["refused"] == "BOOK_IS_FOR_ANOTHER_MARKET"
               for r in p.refusals)

    # the UP token is not this window's
    p2 = mk(lambda pin: fake_book(pin, now_fn=clock, up_token="tok-wrong"),
            "tokmismatch")
    assert p2() is None
    assert any(r["refused"] == "TOKEN_MISMATCH_UP" for r in p2.refusals)

    # health claims a DIFFERENT market than the pin
    d3 = str(tmp_path / "e1_healthmarket")
    os.makedirs(d3, exist_ok=True)
    p3 = PV.LiveWindowProvider(
        progress=PG.CohortProgress(d3),
        session_auth=AUTH.SessionAuthorization(d3),
        book_fn=lambda pin: fake_book(pin, now_fn=clock),
        health_fn=lambda pin: dict(fake_health(pin, now_fn=clock),
                                   market="btc-updown-15m-%d" % (BASE + W)),
        selector_fn=fake_selector, now_fn=clock, window_starts=[BASE])
    assert p3() is None

    # and the per-submission boundary catches a token mismatch too
    auth = AUTH.SessionAuthorization(state)
    pin = fake_pin(BASE)
    auth.authorize_selected(pin)
    with pytest.raises(AUTH.NotAuthorized) as ei:
        auth.enforce_before_submission(slug=pin["slug"], pin=pin,
                                       token_up="tok-wrong")
    assert "token_up mismatch" in str(ei.value)
    with pytest.raises(AUTH.NotAuthorized):
        auth.enforce_before_submission(slug=pin["slug"], pin=pin,
                                       token_down="tok-wrong")


def test_FAILS_CLOSED_selector_refusal_yields_no_window(monkeypatch,
                                                        tmp_path):
    arm(monkeypatch)
    state = str(tmp_path / "e1_cohort")

    def refusing(ws, **kw):
        raise SEL.WindowRefused("the venue would not confirm window %d" % ws)

    p = PV.LiveWindowProvider(
        progress=PG.CohortProgress(state),
        session_auth=AUTH.SessionAuthorization(state),
        book_fn=lambda pin: fake_book(pin),
        health_fn=lambda pin: fake_health(pin),
        selector_fn=refusing, now_fn=Clock(BASE + 10),
        window_starts=[BASE, BASE + W])
    assert p() is None
    assert all("SELECTOR_REFUSED" in r["refused"] or
               "NO_CANDIDATE" in r["refused"] for r in p.refusals)


def test_FAILS_CLOSED_the_window_budget_is_never_exceeded(monkeypatch,
                                                          tmp_path):
    arm(monkeypatch)
    state = str(tmp_path / "e1_cohort")
    clock = Clock(BASE + 10)
    prog = PG.CohortProgress(state)
    p = PV.LiveWindowProvider(
        progress=prog, session_auth=AUTH.SessionAuthorization(state),
        book_fn=lambda pin: fake_book(pin, now_fn=clock),
        health_fn=lambda pin: fake_health(pin, now_fn=clock),
        selector_fn=fake_selector, now_fn=clock,
        window_starts=[BASE + i * W for i in range(30)])
    for i in range(30):
        clock.t = BASE + i * W + 10
        p()
    assert prog.windows_used() == SP.WINDOWS == 12
    assert prog.windows_remaining() == 0
    clock.t = BASE + 40 * W
    assert p() is None
    assert any(r["refused"] == "COHORT_WINDOW_BUDGET_SPENT"
               for r in p.refusals)


# =================== the frozen parameters, unchanged ===================

def test_the_live_wiring_changed_no_frozen_parameter():
    assert SP.FIRST_LEG_QTY == D("0.13")
    assert SP.MAX_FIRST_LEG_PRICE == D("0.60")
    assert SP.PAIR_COST_CEILING == D("0.96")
    assert SP.MAX_ATTEMPTS_PER_WINDOW == 20
    assert SP.MAX_EXPOSURE_PER_WINDOW_USD == D("1.56")
    assert SP.WINDOWS == 12
    assert SP.MAX_COHORT_REALIZED_LOSS_USD == D("5.00")
    assert SP.MAX_COMPLEMENT_TRANCHES == 13
    assert ST.CohortTracker.VENUE_ERROR_STORM == 5
    assert ST.AUTHORIZED_VERDICTS == (
        "FAIL", "COMPLETION_SPRT_POSITIVE_CROSSING",
        "CONTINUE_INSUFFICIENT_EVIDENCE")
    assert not hasattr(ST, "VERDICT_PASS")


def test_the_selector_and_book_are_reused_not_rebuilt():
    for mod, want in (("selector.py", "l5_market_binding"),
                      ("live_book.py", "l5_clob_ws")):
        src = open(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), mod),
            encoding="utf8").read()
        assert want in src, (mod, want)
    assert SP.SPEC["live_provider"]["built_another_discovery_stack"] is False
    assert SP.SPEC["live_provider"]["built_another_book_stack"] is False
    assert "src.l5_market_binding (BTC15m market + token resolution)" in \
        SP.SPEC["live_provider"]["reuses_not_rebuilds"]


def test_no_real_post_is_reachable_from_these_tests():
    src = open(os.path.abspath(__file__), encoding="utf8").read()
    assert "import " + "url" + "lib" not in src
    # the real transport class is never NAMED as a factory here
    assert "Bounded" + "OrderTransport" not in src.replace(
        '"Bounded" + "OrderTransport"', "")
    # every drive_main call uses the fake transport by default
    assert "transport=Fake" + "Transport" in src


def test_disarmed_main_still_refuses(monkeypatch, capsys):
    for k in AR.ARM_KEYS + (AR.FINGERPRINT_PIN_ENV,):
        monkeypatch.delenv(k, raising=False)
    rc = RUN.main([])
    assert rc == 0
    assert '"REFUSED": "DISARMED"' in capsys.readouterr().out


def test_an_armed_build_with_the_wrong_pin_is_inert(monkeypatch, tmp_path,
                                                    capsys):
    """Arming without pinning THIS fingerprint must not run the cohort."""
    sid = arm(monkeypatch)
    for old in SP.SPEC["supersedes_e1"]["must_not_be_rearmed"]:
        monkeypatch.setenv(AR.FINGERPRINT_PIN_ENV, old)
        called = {"v": False}
        monkeypatch.setattr(RUN, "run_cohort",
                            lambda **k: called.__setitem__("v", True))
        rc = RUN.main([])
        assert rc == 2
        assert called["v"] is False
        assert "FINGERPRINT_NOT_PINNED_TO_THIS_BUILD" in \
            capsys.readouterr().out


def test_an_armed_build_without_the_series_gate_is_inert(monkeypatch,
                                                         capsys):
    arm(monkeypatch)
    monkeypatch.delenv("PMUS_E1_GATE_EVIDENCE", raising=False)
    called = {"v": False}
    monkeypatch.setattr(RUN, "run_cohort",
                        lambda **k: called.__setitem__("v", True))
    rc = RUN.main([])
    assert rc == 2
    assert called["v"] is False
    assert "SERIES_GATE_NOT_SET" in capsys.readouterr().out


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
