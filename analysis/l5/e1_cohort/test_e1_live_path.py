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
from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import pmus_provider as PROV
from analysis.l5.e1_cohort import pmus_identity as PI
from analysis.l5.e1_cohort import pmus_provider as PV
from analysis.l5.e1_cohort import pmus_selector as SEL
from analysis.l5.e1_cohort import recorder as R
from analysis.l5.e1_cohort import runner as RUN
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


def fake_pin(window_start, *, slug=None):
    """A PMUS pin: slug + outcome sides, and NO token ids.

    The slug is built with the real `pmus_identity.slug_for`, so a test
    cannot accidentally authorize a shape the venue would reject.
    """
    ws = int(window_start)
    return {
        "series": SEL.SERIES,
        "venue": "polymarket_us",
        "slug": slug or PI.slug_for(ws),
        "market_id": "pmus-market-%d" % ws,
        "outcome_up": PI.outcome_sides({})["up_is"],
        "outcome_down": PI.outcome_sides({})["down_is"],
        "window_start": ws, "window_end": ws + W,
        "identity": "slug_plus_outcome_side",
        "token_ids_in_pin": False,
        "question": "BTC up or down?",
        "selection_provenance": {"selector": "FAKE_FOR_TEST",
                                 "resolver": "FAKE_FOR_TEST",
                                 "series_pinned": SEL.SERIES,
                                 "gamma_used": False,
                                 "clob_ws_used": False,
                                 "window_grid_s": W},
    }


class Clock:
    def __init__(self, t): self.t = float(t)
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


def fake_selector(window_start, **kw):
    return fake_pin(window_start)


class FakeFeed:
    """Stands in for `PmusDecisionFeed` with the same contract."""

    def __init__(self, *, ok=True, market=None, age=0.4, bid="0.50",
                 ask="0.52", size="4.00", clock=None,
                 why="NO_CONNECTED_TWO_SIDED_BOOK"):
        self.ok, self.market, self.age = ok, market, age
        self.bid, self.ask, self.size, self.why = bid, ask, size, why
        #: the book timestamp comes from the INJECTED clock, as the old
        #: harness's did. BASE is deliberately in the future, so a fake
        #: book is never judged stale by real wall-clock time.
        self.clock = clock or (lambda: BASE + 10)
        self.started = False

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.started = False

    def health(self, pin):
        slug = self.market or pin["slug"]
        if not self.ok:
            return {"ok": False, "why": self.why, "market": slug,
                    "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS}
        return {"ok": True, "market": slug,
                "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                "rest_is_sufficient_timing_authority": False,
                "ctf_token_ids_used": False,
                "stream_age_s": self.age,
                "source": "authenticated_ws",
                "proven_for_the_selected_market": True}

    def book(self, pin):
        slug = self.market or pin["slug"]
        comp = PBK.complement_of(self.bid, self.ask)
        return {"market": slug, "ok": True,
                "book_unix": self.clock(),
                "first_leg": {"side": "UP", "slug": slug,
                              "outcome_side": pin["outcome_up"],
                              "best_bid": self.bid, "best_ask": self.ask,
                              "size_at_our_price": self.size},
                "complement": {"side": "DOWN", "slug": slug,
                               "outcome_side": pin["outcome_down"],
                               "best_bid": str(comp["bid"]),
                               "best_ask": str(comp["ask"])}}


class RollingFeed(FakeFeed):
    """A FakeFeed that rolls the injected clock into the NEXT window once
    the first window's attempt slots are spent, so the provider has to
    open a second window."""

    def __init__(self, *, clock, roll_after=20, roll_to=None, **kw):
        super().__init__(clock=clock, **kw)
        self._clk = clock
        self.roll_after = int(roll_after)
        self.roll_to = roll_to
        self.reads = 0

    def book(self, pin):
        self.reads += 1
        if self.reads > self.roll_after:
            self._clk.t = (BASE + W + 10 if self.roll_to is None
                           else float(self.roll_to))
        return super().book(pin)


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


def drive_main(monkeypatch, tmp_path, *, clock, starts, feed=None,
               selector=None, sid="e1-live-test-session",
               transport=FakeTransport):
    """Invoke the REAL `runner.main()` on its ARMED branch, with fakes."""
    arm(monkeypatch, sid)
    state = str(tmp_path / "e1_cohort")
    captured = {}

    def provider_factory(*, progress, session_auth, recorder):
        p = PV.PmusWindowProvider(
            progress=progress, session_auth=session_auth,
            feed=feed if feed is not None else FakeFeed(clock=clock),
            recorder=recorder, selector_fn=selector or fake_selector,
            now_fn=clock, sleep_fn=lambda s: None,
            window_starts=list(starts))
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
    cap = drive_main(monkeypatch, tmp_path, clock=clock, starts=starts,
                     feed=RollingFeed(clock=clock))
    prog = cap["progress"]
    auth = cap["auth"]
    windows = prog.windows_opened()
    assert len(windows) == 2, [w["slug"] for w in windows]
    slugs = [w["slug"] for w in windows]
    assert slugs[0] != slugs[1], slugs
    assert slugs == [PI.slug_for(BASE), PI.slug_for(BASE + W)]
    # both are in the session authorization ledger
    assert sorted(auth.slugs()) == sorted(slugs)
    for w in windows:
        assert w["authorization_result"] == "AUTHORIZED"
    # the market ids differ too -- a different market, not a renamed one.
    #
    # THE OUTCOME SIDES DO NOT DIFFER, and must not: on PMUS identity is
    # slug + outcomeSide, and OUTCOME_SIDE_YES is the same constant in
    # every window. The per-window identity lives entirely in the slug,
    # which is why the slug assertion above is the one that matters. No
    # CTF token id is recorded at all.
    assert windows[0]["market_id"] != windows[1]["market_id"]
    for w in windows:
        assert w["outcome_up"] != w["outcome_down"]
        assert w["token_up"] is None and w["token_down"] is None
    # orders went to BOTH slugs
    posted = {p["slug"] for p in FakeTransport.posts}
    assert posted == set(slugs), posted
    assert len(FakeTransport.posts) > 20


def test_the_raw_tape_records_per_window_identity_and_authorization(
        monkeypatch, tmp_path):
    clock = Clock(BASE + 10)
    cap = drive_main(monkeypatch, tmp_path, clock=clock,
                     starts=[BASE, BASE + W],
                     feed=RollingFeed(clock=clock))
    rows = cap["recorder"].read(R.EVENTS)
    authz = [r for r in rows if r["event"] == "WINDOW_AUTHORIZED"]
    assert len(authz) == 2
    for r in authz:
        d = r["detail"]
        # selected PMUS slug, market ID, BOTH outcome sides, selection
        # provenance, authorization result
        assert PI.is_pmus_btc15m_slug(d["slug"]), d["slug"]
        assert d["market_id"]
        assert d["outcome_up"] and d["outcome_down"]
        assert d["outcome_up"] != d["outcome_down"]
        assert d["identity"] == "slug_plus_outcome_side"
        assert d["ctf_token_ids_used"] is False
        assert d["selection_provenance"]["series_pinned"] == SEL.SERIES
        assert d["authorization_result"] == "AUTHORIZED"
        h = d["pmus_book_health"]
        assert h["proven_for_the_selected_market"] is True
        assert h["timing_authority"] == PBK.TIMING_PMUS_AUTHENTICATED_WS
        assert h["rest_is_sufficient_timing_authority"] is False
        # the book that authorized this window names THIS slug
        assert h["market"] == d["slug"]
    assert authz[0]["detail"]["slug"] != authz[1]["detail"]["slug"]


# ======= PROOF 3: AN UNSELECTED / NON-BTC15m SLUG IS REFUSED ============

@pytest.mark.parametrize("bad_slug", [
    "will-trump-win-2028",                 # another market entirely
    "btc-updown-1h-1800000000",            # wrong series
    "eth-updown-15m-1800000000",           # wrong asset
    # A POLYMARKET.COM BTC15m SLUG. Right asset, right duration, WRONG
    # VENUE -- this exact shape is what PMUS answered "Market not found"
    # about on 2026-10-07.
    "btc-updown-15m-1800000000",
    # A WELL-FORMED PMUS SLUG FOR A WINDOW THIS SESSION NEVER SELECTED.
    # This one gets past the identity assertion and must be stopped by
    # the pin comparison instead.
    PI.slug_for(BASE + 4 * W),
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
    # A NON-PMUS SLUG IS REFUSED BY IDENTITY, NOT BY THE PIN COMPARISON.
    # `assert_pmus_slug` runs first and names the venue confusion
    # explicitly, which is the more precise refusal; the pin comparison
    # (which does say "Refused BEFORE transport") is what catches a
    # well-formed PMUS slug for the WRONG window, proven below.
    msg = str(ei.value)
    assert ("not a PMUS BTC15m slug" in msg
            or "Refused BEFORE transport" in msg), msg
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
    """The selector BUILDS the slug itself and then requires the venue to
    echo that exact slug back. There is no path by which another market's
    metadata becomes a pin.

    This replaces the old `_check_series` unit: on PMUS the slug is not
    discovered and then validated, it is CONSTRUCTED by `slug_for` and the
    resolver's answer is checked against it.
    """
    want = PI.slug_for(BASE)

    def answering(slug, http=None, **kw):
        return {"slug": slug, "id": "m-1", "active": True, "closed": False}

    # the happy path: the venue echoes the constructed slug
    pin = SEL.select_window(BASE, resolver=answering)
    assert pin["slug"] == want
    assert PI.is_pmus_btc15m_slug(pin["slug"])
    assert pin["token_ids_in_pin"] is False

    # the venue answers about ANOTHER market -- an identity fault
    for wrong in ("will-trump-win-2028",
                  "eth-updown-15m-%d" % BASE,
                  "btc-updown-15m-%d" % BASE,          # Polymarket.com
                  PI.slug_for(BASE + W)):              # wrong window
        with pytest.raises(SEL.WindowRefused) as ei:
            SEL.select_window(
                BASE, resolver=lambda s, h=None, _w=wrong: {
                    "slug": _w, "id": "m-9", "active": True,
                    "closed": False})
        assert "disagree" in str(ei.value) or "identity" in str(ei.value)

    # closed / inactive are refused
    for bad in ({"closed": True}, {"active": False}):
        with pytest.raises(SEL.WindowRefused):
            SEL.select_window(
                BASE, resolver=lambda s, h=None, _b=bad: dict(
                    {"slug": s, "id": "m-1", "active": True,
                     "closed": False}, **_b))

    # a start off the 900s grid is refused before any network call
    with pytest.raises(SEL.WindowRefused):
        SEL.select_window(BASE + 7, resolver=answering)


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
    """A PMUS snapshot: slug + outcome sides, one quoted book, the
    complement derived by the venue's own arithmetic."""
    comp = PBK.complement_of("0.50", "0.52")
    return {"market": pin["slug"], "pin": pin, "window": window,
            "venue": "polymarket_us",
            "window_open": True, "within_handover_tte": True,
            # the two pacing deadlines, derived from the pin exactly as
            # `pmus_provider` derives them, so the suite exercises the real
            # deadline-aware pacing rather than a deadline-free fallback.
            "trading_cutoff_unix": (float(pin["window_end"])
                                    - PROV.HANDOVER_TTE_S),
            "window_end_unix": float(pin["window_end"]),
            "our_outstanding_orders": 0, "our_open_residual": None,
            "qty": SP.FIRST_LEG_QTY, "first_leg_price": "0.50",
            "first_leg": {"side": "UP",
                          "outcome_side": pin["outcome_up"],
                          "slug": pin["slug"],
                          "best_bid": "0.50", "best_ask": "0.52",
                          "size_at_our_price": "4.00"},
            "complement": {"side": "DOWN",
                           "outcome_side": pin["outcome_down"],
                           "slug": pin["slug"],
                           "best_bid": str(comp["bid"]),
                           "best_ask": str(comp["ask"])},
            "fingerprint_pinned": {"matches_this_build": True},
            "ws_state": {
                "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                "pinned_market_streaming": True,
                "rest_is_sufficient_timing_authority": False,
                "ctf_token_ids_used": False,
                "market": pin["slug"],
                "book_unix": BASE + 10,
                "first_leg": {"best_bid": "0.50", "best_ask": "0.52"},
                "complement": {"best_bid": str(comp["bid"]),
                               "best_ask": str(comp["ask"])}}}


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

def _provider(tmp_path, tag, *, clock, feed=None, selector=None,
              starts=None):
    """A PmusWindowProvider on its OWN state dir, so a window consumed by
    one sub-case does not make the next refuse for the wrong reason."""
    d = str(tmp_path / ("e1_" + tag))
    os.makedirs(d, exist_ok=True)
    return PV.PmusWindowProvider(
        progress=PG.CohortProgress(d),
        session_auth=AUTH.SessionAuthorization(d),
        feed=feed if feed is not None else FakeFeed(clock=clock),
        selector_fn=selector or fake_selector,
        now_fn=clock, sleep_fn=lambda s: None,
        window_starts=list(starts if starts is not None else [BASE]))


def test_FAILS_CLOSED_stale_window_rollover(monkeypatch, tmp_path):
    """A pinned window whose end has passed is CLOSED, never reused."""
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    p = _provider(tmp_path, "rollover", clock=clock,
                  starts=[BASE, BASE + W])
    prog = p.progress
    s1 = p()
    assert s1 is not None and s1["market"] == PI.slug_for(BASE)
    first_slug = s1["market"]
    # the window ENDS
    clock.t = BASE + W + 1
    s2 = p()
    assert s2 is not None
    assert s2["market"] != first_slug, "a stale window was reused"
    assert s2["market"] == PI.slug_for(BASE + W)
    assert any(r["refused"] == "STALE_WINDOW_ROLLED_OVER"
               for r in p.refusals)
    closed = [e for e in prog.events if e["event"] == PG.EV_WINDOW_CLOSED]
    assert closed and closed[0]["reason"] == "WINDOW_END_PASSED"
    # and with NO successor window available, it yields NOTHING rather
    # than a stale snapshot
    clock.t = BASE + 3 * W
    p.window_starts = []
    assert p() is None


def test_FAILS_CLOSED_a_dead_window_is_never_opened(monkeypatch, tmp_path):
    """A candidate whose window has ALREADY ENDED must not consume one of
    the twelve. The budget is on tradeable windows."""
    arm(monkeypatch)
    clock = Clock(BASE + 5 * W)            # long past BASE and BASE+W
    p = _provider(tmp_path, "deadwindow", clock=clock,
                  starts=[BASE, BASE + W, BASE + 5 * W])
    snap = p()
    assert snap is not None
    assert snap["market"] == PI.slug_for(BASE + 5 * W)
    # exactly ONE window was spent, and it is the live one
    assert p.progress.windows_used() == 1
    assert p.progress.slugs_used() == [PI.slug_for(BASE + 5 * W)]
    dead = [r for r in p.refusals if r["refused"] == "WINDOW_ALREADY_ENDED"]
    assert len(dead) == 2, p.refusals


def test_FAILS_CLOSED_the_same_start_cannot_be_opened_twice(monkeypatch,
                                                            tmp_path):
    """A repeated candidate start spends ONE window, not twelve."""
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    p = _provider(tmp_path, "repeatstart", clock=clock,
                  starts=[BASE] * 12)
    for _ in range(12):
        p()
    assert p.progress.windows_used() == 1
    assert p.progress.slugs_used() == [PI.slug_for(BASE)]


def test_FAILS_CLOSED_feed_loss_before_submission(monkeypatch, tmp_path):
    """The PMUS book must be PROVEN usable for the SELECTED market on
    EVERY snapshot. Lose it and there is NO snapshot -- not a degraded
    one."""
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    feed = FakeFeed(clock=clock)
    p = _provider(tmp_path, "feedloss", clock=clock, feed=feed)
    assert p() is not None

    # the authenticated socket drops
    feed.ok = False
    feed.why = "NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG"
    assert p() is None, "a snapshot was produced without a proven book"
    assert any("PMUS_BOOK_UNUSABLE_BEFORE_SUBMISSION" in r["refused"]
               for r in p.refusals)

    # a SILENT book also fails closed: AuthBook's own staleness verdict is
    # what the provider asks for, and it answers not-ok
    feed.ok = False
    feed.why = "BOOK_STALE_%0.3fs" % (PBK.MAX_BOOK_AGE_S + 1)
    assert p() is None

    # REST IS NOT SUFFICIENT TIMING AUTHORITY. A health record that claims
    # it is gets no snapshot, whatever else it says.
    feed.ok = True
    real_health = feed.health
    feed.health = lambda pin: dict(
        real_health(pin),
        timing_authority="rest_same_second_reachability",
        rest_is_sufficient_timing_authority=True)
    assert p() is None

    # and a book sourced from anything but the authenticated socket is
    # refused by the feed itself, which is where that check belongs
    real_feed = PBK.PmusDecisionFeed(clock=clock, book=_StubBook(
        PI.slug_for(BASE), source="rest_poll", age=0.1))
    real_feed.start()
    h = real_feed.health(fake_pin(BASE))
    assert h["ok"] is False
    assert "SOURCE_IS_NOT_THE_AUTHENTICATED_WS" in h["why"]


class _StubBook:
    """The minimum AuthBook surface `PmusDecisionFeed` consumes."""

    def __init__(self, slug, *, source="authenticated_ws", age=0.1,
                 bid="0.50", ask="0.52"):
        self.slug, self.source, self.age = slug, source, age
        self.bid, self.ask = bid, ask
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def snapshot(self, slug):
        if str(slug) != str(self.slug):
            return None
        return {"source": self.source, "stream_age_s": self.age,
                "bid": self.bid, "ask": self.ask, "bid_shares": "4.0",
                "ask_shares": "4.0", "book_updates": 17,
                "market_state": "open", "last_update_t": 0.0}


def test_FAILS_CLOSED_wrong_market_or_wrong_outcome(monkeypatch, tmp_path):
    """A book for another market, or a leg whose outcome side is not this
    window's, yields no snapshot.

    THIS IS THE REPLACEMENT FOR THE TOKEN-MISMATCH PROOF. PMUS identity is
    slug + outcomeSide, so the thing that must match is the slug on the
    book and the outcome side on each leg -- not a CTF token id, which a
    PMUS order does not carry at all.
    """
    arm(monkeypatch)
    clock = Clock(BASE + 10)

    # the book is for ANOTHER market
    p = _provider(tmp_path, "othermarket", clock=clock,
                  feed=FakeFeed(clock=clock, market=PI.slug_for(BASE + W)))
    assert p() is None
    # the health record names the other market first, so that is the
    # refusal; either way NO snapshot is produced
    assert any(r["refused"].startswith("BOOK_IS_FOR_ANOTHER_MARKET")
               or "UNUSABLE_BEFORE_SUBMISSION" in r["refused"]
               for r in p.refusals), p.refusals

    # health is for the pinned market but the BOOK is not
    feed2 = FakeFeed(clock=clock)
    real_book = feed2.book
    feed2.book = lambda pin: dict(real_book(pin),
                                  market=PI.slug_for(BASE + W))
    p2 = _provider(tmp_path, "bookmarket", clock=clock, feed=feed2)
    assert p2() is None
    assert any(r["refused"] == "BOOK_IS_FOR_ANOTHER_MARKET"
               for r in p2.refusals), p2.refusals

    # a LEG that names a different slug than the book
    feed3 = FakeFeed(clock=clock)
    rb3 = feed3.book
    def _legmismatch(pin):
        b = rb3(pin)
        b["first_leg"] = dict(b["first_leg"], slug=PI.slug_for(BASE + W))
        return b
    feed3.book = _legmismatch
    p3 = _provider(tmp_path, "legslug", clock=clock, feed=feed3)
    assert p3() is None
    assert any(r["refused"] == "LEG_SLUG_MISMATCH_FIRST_LEG"
               for r in p3.refusals), p3.refusals

    # the UP leg carries the WRONG OUTCOME SIDE
    feed4 = FakeFeed(clock=clock)
    rb4 = feed4.book
    def _outcomemismatch(pin):
        b = rb4(pin)
        b["first_leg"] = dict(b["first_leg"],
                              outcome_side=pin["outcome_down"])
        return b
    feed4.book = _outcomemismatch
    p4 = _provider(tmp_path, "outcomeup", clock=clock, feed=feed4)
    assert p4() is None
    assert any(r["refused"] == "OUTCOME_MISMATCH_FIRST_LEG"
               for r in p4.refusals), p4.refusals

    # the DOWN leg carries the wrong outcome side
    feed5 = FakeFeed(clock=clock)
    rb5 = feed5.book
    def _compmismatch(pin):
        b = rb5(pin)
        b["complement"] = dict(b["complement"],
                               outcome_side=pin["outcome_up"])
        return b
    feed5.book = _compmismatch
    p5 = _provider(tmp_path, "outcomedown", clock=clock, feed=feed5)
    assert p5() is None
    assert any(r["refused"] == "OUTCOME_MISMATCH_COMPLEMENT"
               for r in p5.refusals), p5.refusals


def test_FAILS_CLOSED_the_per_submission_boundary_checks_outcome_sides(
        monkeypatch, tmp_path):
    """Boundary 2 enforces the exact slug and BOTH outcome sides."""
    arm(monkeypatch)
    auth = AUTH.SessionAuthorization(str(tmp_path / "e1_cohort"))
    pin = fake_pin(BASE)
    auth.authorize_selected(pin)

    ok = auth.enforce_before_submission(slug=pin["slug"], pin=pin)
    assert ok["ok"] is True
    assert ok["ctf_token_ids_used"] is False
    assert ok["identity"] == "slug_plus_outcome_side"
    assert ok["boundary"] == "PER_SUBMISSION_EXACT_SLUG_AND_OUTCOMES"

    with pytest.raises(AUTH.NotAuthorized) as ei:
        auth.enforce_before_submission(slug=pin["slug"], pin=pin,
                                       outcome_up="OUTCOME_SIDE_WRONG")
    assert "outcome_up mismatch" in str(ei.value)
    with pytest.raises(AUTH.NotAuthorized) as ei2:
        auth.enforce_before_submission(slug=pin["slug"], pin=pin,
                                       outcome_down="OUTCOME_SIDE_WRONG")
    assert "outcome_down mismatch" in str(ei2.value)


def test_FAILS_CLOSED_selector_refusal_yields_no_window(monkeypatch,
                                                        tmp_path):
    arm(monkeypatch)

    def refusing(ws, **kw):
        raise SEL.WindowRefused("the venue would not confirm window %d" % ws)

    p = _provider(tmp_path, "selrefuse", clock=Clock(BASE + 10),
                  selector=refusing, starts=[BASE, BASE + W])
    assert p() is None
    assert all("SELECTOR_REFUSED" in r["refused"] or
               "NO_CANDIDATE" in r["refused"] for r in p.refusals)


def test_FAILS_CLOSED_the_window_budget_is_never_exceeded(monkeypatch,
                                                          tmp_path):
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    p = _provider(tmp_path, "budget", clock=clock,
                  starts=[BASE + i * W for i in range(30)])
    prog = p.progress
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


def _code_only(src: str) -> str:
    """`src` with every docstring and comment removed.

    These files explain at length what they REFUSE to do, so a bare
    substring search over the whole text would fire on the prose that
    promises the absence of the thing being searched for.
    """
    import ast
    tree = ast.parse(src)
    out = src
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                out = out.replace(d, "")
    return "\n".join(ln for ln in out.splitlines()
                      if not ln.strip().startswith("#"))


def test_the_selector_and_book_are_reused_not_rebuilt():
    """E1 must reuse the PROVEN PMUS modules, not re-implement them.

    `pmus_identity` delegates resolution to `src.l5_pmus_transport`, and
    `pmus_book` wraps `src.l5_pmus_auth_book.AuthBook` UNMODIFIED. Neither
    may name a Polymarket.com module.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for mod, want in (("pmus_identity.py", "l5_pmus_transport"),
                      ("pmus_book.py", "l5_pmus_auth_book")):
        src = open(os.path.join(here, mod), encoding="utf8").read()
        assert want in src, (mod, want)

    # AuthBook is USED, not vendored: the wrapper holds no websocket core
    # of its own. CODE ONLY -- the prose legitimately discusses websockets
    # in order to explain that it does not implement one.
    book_src = open(os.path.join(here, "pmus_book.py"),
                    encoding="utf8").read()
    assert "AB.AuthBook(" in book_src
    for vendored in ("WebSocketApp", "wss://", "import websocket",
                     "create_connection"):
        assert vendored not in _code_only(book_src), vendored

    # and no E1 production module names a Polymarket.com venue module
    for mod in ("pmus_identity.py", "pmus_book.py", "pmus_selector.py",
                "pmus_provider.py", "runner.py", "authorization.py"):
        code = _code_only(open(os.path.join(here, mod),
                               encoding="utf8").read())
        for foreign in ("l5_clob_ws", "l5_market_binding",
                        "l5_book_shadow"):
            assert foreign not in code, (mod, foreign)
        # Polymarket.com HOSTS. `pmus_identity` is exempt because it OWNS
        # the denylist: `FORBIDDEN_HOSTS` must name the hosts in order to
        # forbid them.
        if mod == "pmus_identity.py":
            continue
        for host in ("gamma-api.polymarket.com",
                     "ws-subscriptions-clob.polymarket.com",
                     "clob.polymarket.com"):
            assert host not in code, (mod, host)

    assert SP.SPEC["live_provider"]["built_another_discovery_stack"] is False
    assert SP.SPEC["live_provider"]["built_another_book_stack"] is False
    reuses = SP.SPEC["live_provider"]["reuses_not_rebuilds"]
    assert any("l5_pmus_transport" in r for r in reuses), reuses
    assert any("l5_pmus_auth_book" in r for r in reuses), reuses


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
