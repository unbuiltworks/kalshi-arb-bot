"""The bounded live probe: one market, one authorization, then gone.

Ten properties, each of which is the reason this runs in its own one-shot
service instead of being switched on inside the 24/7 observer.
"""
from __future__ import annotations

def venue_window(slug):
    """The venue-confirmed bounds for the window this slug names.

    The arming boundary refuses an authorization it cannot bound by the
    venue's own close, so every probe run here carries one instead of
    reaching a gateway.
    """
    import datetime as _d
    from src import l5_pmus_auth_book as _AB
    ws = _AB.slug_start(slug)
    s = _d.datetime.fromtimestamp(ws, _d.timezone.utc)
    return {"source": "VENUE", "start": s.isoformat(),
            "end": (s + _d.timedelta(seconds=_AB.WINDOW_S)).isoformat()}



import datetime as dt
import json
import pathlib
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as P
import src.l5_live_authorization as AUTH
import src.l5_window_actions as WA
import src.l5_live_execution as EX
import src.pmus_auth as Q1
import tests.test_l5_live_execution as L


@pytest.fixture(autouse=True)
def _durability_precondition_opt_out(monkeypatch):
    """A pytest tmp_path ledger lives on the container root, which the
    arming boundary's durability precondition REFUSES by design -- a claim
    lock that cannot survive a restart is not a claim lock.

    These tests pin down the probe, not storage durability, so the
    precondition is neutralised here. It is NOT weakened: its own tests in
    tests/test_l5_arming_boundary.py assert that the default is strict, that
    the deployed service's default ledger path is refused, and that a
    multi-replica configuration is refused.
    """
    from src import l5_live_authorization as AUTH
    monkeypatch.setattr(AUTH, "durability_refusal", lambda p: None)
    # the declared-holder check is a real env contract, so it is SET rather
    # than neutralised: these tests are the holder.
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "test-auth-holder")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "test-auth-holder")

MARKET = "cpc-btc-updown-15m-2026-09-23-1615z"
START = P.window_start(MARKET)


def authorize(monkeypatch, sid="sess-001"):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)
    # The boundary refuses HALT_GATE_ABSENT when no independent halt is
    # configured, and this probe builds its gate from L5_HALT_URL. Set it so
    # these tests exercise the REAL wiring; HttpHaltStore performs no I/O at
    # construction, and the gate is only read on the submit path.
    monkeypatch.setenv("L5_HALT_URL", "http://halt.invalid:8080")
    # And the holder identity: the boundary refuses an actor that cannot name
    # its own deployment (DEPLOYMENT_IDENTITY_UNKNOWN), because such an actor
    # cannot be distinguished from one overlapping it.
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-test")


def deauthorize(monkeypatch):
    for k in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(k, raising=False)


class Venue:
    """A transport with the real shape: read-only until arm() is called."""

    name = "VENUE_DOUBLE"

    #: the account the credential in this double resolves to
    FINGERPRINT = "d51b44a3fa83"

    #: the venue's own quantity grid, as this market's preflight reported it
    #: on 2026-09-25. A double without it is not modelling the transport --
    #: and an unfaithful double is how an unquantized quantity reached the
    #: wire in the first place.
    quantity_increment = D("0.01")

    def __init__(self, *, preflight="PASSED", fill="0", reject=None,
                 positions=(), open_orders=(), fingerprint=None):
        self.armed = False
        self.posts, self.cancels = [], []
        self._preflight = preflight
        self._fill = fill
        self._reject = reject
        self._positions = list(positions)
        self._open = list(open_orders)
        self.fingerprint = fingerprint or self.FINGERPRINT
        self.preflight_report = None

    def preflight(self, *, expect_account=None, known_positions=None):
        """The real signature, including the account pin.

        The pin is honoured here rather than ignored, because a double that
        accepts the argument and does nothing with it would let the gate go
        inert again without a single test noticing -- which is exactly how
        it went inert in the first place.
        """
        checks = []
        matched = None
        if expect_account is not None:
            matched = self.fingerprint == expect_account
            checks.append({"check": "account_matches_expected",
                           "passed": matched,
                           "detail": "expected %s, got %s"
                                     % (expect_account, self.fingerprint)})
        verdict = "FAILED" if matched is False else self._preflight
        self.preflight_report = {
            "preflight": verdict, "checks": checks,
            "identity_probe": {"api_key_fingerprint": self.fingerprint}}
        if verdict != "PASSED":
            raise RuntimeError(json.dumps(self.preflight_report))
        return self.preflight_report

    def arm(self):
        if (self.preflight_report or {}).get("preflight") != "PASSED":
            raise EX.NotArmed("preflight has not passed")
        if not Q1.armed():
            raise EX.NotArmed("owner arm token absent")
        self.armed = True
        return {"armed": True}

    def submit(self, intent):
        if not self.armed:
            raise EX.NotArmed("DISARMED")
        if self._reject:
            raise self._reject
        self.posts.append(dict(intent))
        return {"order_id": "v%d" % len(self.posts), "filled_qty": self._fill,
                "fill_price": str(intent["price"])}

    def cancel(self, order_id):
        if not self.armed:
            raise EX.NotArmed("DISARMED")
        self.cancels.append(order_id)
        return {"cancelled": order_id}

    def positions(self):
        return list(self._positions)

    def open_orders(self):
        return list(self._open)




class Feed:
    """A book feed double in the shape of the authenticated one."""

    def __init__(self, clock, rows=None):
        self.clock = clock
        self._rows = rows
        self.started = self.stopped = False

    def books(self):
        if self._rows is not None:
            return list(self._rows)
        return [{"t": self.clock() - 0.2, "bid": "0.40", "ask": "0.60",
                 "bid_qty": "500", "ask_qty": "500", "status": "OK"}]

    def latest(self):
        b = self.books()
        return b[-1] if b else None

    def age_s(self):
        last = self.latest()
        return None if last is None else self.clock() - last["t"]

    def fresh(self):
        return self.age_s() is not None and self.age_s() <= 2.0

    def health(self):
        return {"market": MARKET, "connected": True,
                "book_states": len(self.books()), "fresh": self.fresh(),
                "source": "AUTHENTICATED_WS"}

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wait_ready(self, timeout_s=60.0, sleep=None):
        return self.latest() is not None


def drive(tmp_path, monkeypatch, *, venue=None, actions=None, ledger=None,
          market=MARKET, books=None):
    """Run one probe over a simulated window, with no real clock or network."""
    venue = venue or Venue()
    rows = list(actions or [])
    monkeypatch.setenv(AUTH.MARKET_ENV, market)

    t = {"now": START + P.PREFLIGHT_AT_S}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += s

    def once(slug, seen, act=None, health=None):
        """Deliver the window's actions the first time, then nothing."""
        if "done" in seen:
            return [], True
        seen.add("done")
        stamp = dt.datetime.fromtimestamp(t["now"], dt.timezone.utc).isoformat()
        return ([dict(r, action_key="k%d" % i, receipt_utc=stamp)
                 for i, r in enumerate(rows)], True)

    monkeypatch.setattr(P, "new_l5_actions", once)
    rep = P.run(window_bounds=venue_window(MARKET), clock=clock, sleep=sleep,
                transport_factory=lambda slug: venue,
                book_feed=Feed(clock, books),
                state_path=str(tmp_path / "inv.json"),
                journal_path=str(tmp_path / "j.jsonl"),
                ledger_path=str(ledger or (tmp_path / "auth.jsonl")))
    return rep, venue


def l5row(**kw):
    r = L.row(**kw)
    r["timestamp"] = START + 1
    return r


# 1 ------------------------------------------- zero authorization = zero POST

def test_without_authorization_nothing_can_post(tmp_path, monkeypatch):
    deauthorize(monkeypatch)
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row()])
    assert rep["refused"] == "OWNER_AUTHORIZATION_ABSENT"
    assert rep["armed"] is False
    assert venue.posts == [] and venue.cancels == []
    assert venue.armed is False


def test_a_partial_token_is_not_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "sess-x")
    monkeypatch.delenv(Q1.ARM_ENV, raising=False)      # the match is missing
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row()])
    assert rep["refused"] == "OWNER_AUTHORIZATION_ABSENT"
    assert venue.posts == []


def test_a_mismatched_arm_token_is_not_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "sess-a")
    monkeypatch.setenv(Q1.ARM_ENV, "sess-b")
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row()])
    assert rep["refused"] == "OWNER_AUTHORIZATION_ABSENT"
    assert venue.posts == []


# 2 ------------------------------------- authorization enables exactly one market

def test_authorization_enables_one_market(tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)])
    assert rep["armed"] is True
    assert venue.armed is True
    assert rep["market"] == MARKET
    assert rep["submitted"] == 1
    assert len(venue.posts) == 1


# 3 --------------------------------------------- a second market cannot trade

def test_the_same_authorization_cannot_trade_a_second_market(
        tmp_path, monkeypatch):
    authorize(monkeypatch)
    ledger = tmp_path / "auth.jsonl"
    first, v1 = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)],
                      ledger=ledger)
    assert first["submitted"] == 1

    # the NEXT window, same environment, same session id
    nxt = "cpc-btc-updown-15m-2026-09-23-1630z"
    second, v2 = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)],
                       ledger=ledger, market=nxt)
    assert second["refused"] == "AUTHORIZATION_ALREADY_USED"
    assert second["armed"] is False
    assert v2.posts == []
    assert v2.armed is False


def test_a_claim_is_spent_even_if_the_process_died(tmp_path, monkeypatch):
    # the claim is written BEFORE arming, so a crash spends it too
    authorize(monkeypatch)
    ledger = str(tmp_path / "auth.jsonl")
    AUTH.Authorization(ledger).claim(MARKET)
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row()], ledger=ledger)
    assert rep["refused"] == "AUTHORIZATION_ALREADY_USED"
    assert venue.posts == []


def test_a_claim_is_bound_to_the_market_it_names(tmp_path, monkeypatch):
    authorize(monkeypatch)
    a = AUTH.Authorization(str(tmp_path / "auth.jsonl"))
    a.claim(MARKET)
    with pytest.raises(AUTH.Refused) as e:
        a.check_market("cpc-btc-updown-15m-2026-09-23-1630z")
    assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"


def test_an_unreadable_ledger_line_still_refuses(tmp_path, monkeypatch):
    # a ledger we cannot parse is not an empty one
    authorize(monkeypatch)
    led = tmp_path / "auth.jsonl"
    led.write_text("{not json at all sess-001}\n")
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row()], ledger=led)
    assert rep["refused"] == "AUTHORIZATION_ALREADY_USED"
    assert venue.posts == []


# 4 ------------------------------------------------- cannot auto-restart

def test_the_service_manifest_never_restarts():
    m = json.loads(pathlib.Path("deploy/l5_live_probe/railway.json").read_text())
    assert m["deploy"]["restartPolicyType"] == "NEVER"
    assert "l5_pmus_live_oneshot" in m["deploy"]["startCommand"]


def test_run_returns_rather_than_looping():
    """`run` has no outer loop over windows: one market, then it returns."""
    import ast
    import inspect
    src = inspect.getsource(P.run)
    tree = ast.parse(src.lstrip())
    fn = tree.body[0]
    loops = [n for n in fn.body if isinstance(n, (ast.For, ast.While))]
    assert not loops, "run() must not iterate over markets"


def test_main_runs_the_probe_once(monkeypatch):
    calls = []
    monkeypatch.setattr(P, "run", lambda **kw: calls.append(kw) or {"ok": 1})
    assert P.main([]) == 0
    assert len(calls) == 1


# 5 --------------------- concurrent live orders / $5 reservation via partials

def test_a_partial_fill_keeps_consuming_the_envelope(tmp_path, monkeypatch):
    """What this has always been about: a partial keeps counting.

    It used to be asserted through the ONE-order limit -- two follow-on
    actions refused MAX_LIVE_ORDERS proved the first was still live. Probe 07
    allows eight concurrent orders, because replaying 12 windows showed the
    count excluded 143 of 217 maker intents while the dollars excluded none.
    So the same property is now asserted directly: the partially filled order
    is still live, and its REMAINING quantity is still in the risk bound.
    """
    authorize(monkeypatch)
    # lambda-normalised size 2.0 translates to well under one share, so the
    # partial has to be smaller still: this fills a fraction and leaves the
    # rest resting, which is the case the envelope has to keep counting.
    venue = Venue(fill="0.001")
    rep, venue = drive(tmp_path, monkeypatch, venue=venue,
                       actions=[l5row(key="a", size=2.0),
                                l5row(key="b", size=2.0),
                                l5row(key="c", size=2.0)])
    # all three are now allowed to rest at once
    assert len(venue.posts) == 3
    assert rep["refused_reason"].get("MAX_LIVE_ORDERS") is None
    # close-out then enumerated and cancelled every one of them, which is
    # the other half of allowing eight: they must all be retired.
    assert len(rep["close_out"]["cancelled"]) == 3


def test_the_five_dollar_envelope_is_the_probe_envelope(tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, _ = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)])
    lim = rep["live_limits"]
    assert lim["max_usd_per_market"] == "5"
    assert lim["max_total_exposure_usd"] == "5"
    assert lim["max_abs_residual"] == "10"
    assert lim["max_concurrent_markets"] == 1
    # a CONCURRENCY bound, not a money bound: every dollar limit above is
    # unchanged and the worst case below is still 5.1738
    assert lim["max_live_orders"] == 8
    assert lim["daily_realized_loss_halt_usd"] == "5"
    assert rep["worst_case"]["worst_case_total_usd"] == "5.1738"


# 6 -------------------------------------- uncertain submit blocks what follows

def test_an_unknown_submit_outcome_blocks_every_later_order(
        tmp_path, monkeypatch):
    authorize(monkeypatch)
    venue = Venue(reject=TimeoutError("no answer"))
    rep, venue = drive(tmp_path, monkeypatch, venue=venue,
                       actions=[l5row(key="a", size=2.0),
                                l5row(key="b", size=2.0)])
    assert venue.posts == []
    assert rep["refused_reason"].get("SUBMIT_OUTCOME_UNKNOWN") == 1
    assert rep["refused_reason"].get("ORDER_STATE_UNCERTAIN") == 1


def test_a_failed_reconciliation_leaves_the_probe_uncertain(
        tmp_path, monkeypatch):
    authorize(monkeypatch)

    class Blind(Venue):
        def positions(self):
            raise ConnectionError("venue unreachable")

    rep, _ = drive(tmp_path, monkeypatch, venue=Blind(),
                   actions=[l5row(size=2.0)])
    recon = rep["close_out"]["reconciliation"]
    assert recon["reconciled"] is False
    assert rep["close_out"]["orders_uncertain"]


# 7 ------------------------------------------ preview mismatch prevents submit

def test_a_preview_mismatch_refuses_the_order():
    """The gate the real transport calls between preview and submit."""
    from src import l5_order_body as OB
    from src import l5_preview_gate as PG

    body = OB.build(slug=MARKET, side="UP", price=D("0.60"), qty=D("2"),
                    post_only=True)
    normalized = dict(body, price={"value": "0.61", "currency": "USD"})
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body, {"order": normalized})


def test_the_transport_previews_before_it_submits():
    """submit() must POST the preview first and send the SAME body."""
    import ast
    import inspect
    from src import l5_pmus_transport as TP

    src = inspect.getsource(TP.PmusTransport.submit)
    assert "/v1/order/preview" in src
    assert "require_echo" in src
    # the same `body` object goes to both calls: nothing is rebuilt between
    assert src.count("OB.from_intent") == 1


# 8 ------------------------------------ terminal reconciliation before exit

def test_reconciliation_happens_even_when_the_window_errors(
        tmp_path, monkeypatch):
    authorize(monkeypatch)

    class Exploding(Venue):
        def book(self, slug):
            raise RuntimeError("boom")

    rep, venue = drive(tmp_path, monkeypatch, venue=Exploding(),
                       actions=[l5row(size=2.0)])
    assert rep["close_out"]["reconciliation"]["authority"] == "VENUE"
    assert rep["authorization"]["consumed"] is True


def test_a_working_order_is_cancelled_then_reconciled(tmp_path, monkeypatch):
    authorize(monkeypatch)
    venue = Venue(fill="0")            # rests, unfilled
    rep, venue = drive(tmp_path, monkeypatch, venue=venue,
                       actions=[l5row(size=2.0)])
    assert venue.posts, "an order was placed"
    assert venue.cancels == ["v1"], "and cancelled by its VENUE id"
    assert rep["close_out"]["reconciliation"]["reconciled"] is True
    assert rep["inventory"]["live_orders"] == 0


def test_the_authorization_is_consumed_on_every_path(tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, _ = drive(tmp_path, monkeypatch, actions=[])
    assert rep["authorization"]["consumed"] is True
    led = pathlib.Path(str(tmp_path / "auth.jsonl")).read_text().splitlines()
    events = [json.loads(x)["event"] for x in led]
    # CLAIMED by `auth.claim` before any POST-capable object exists, SCOPED
    # by the arming boundary with its expiry and capital envelope, CONSUMED
    # at the end. The SCOPED row used to be absent here, and that absence was
    # the symptom of a real defect: the boundary was called without `path`, so
    # it read and wrote the DEFAULT ledger while the claim lived in this one.
    assert events == ["CLAIMED", "SCOPED", "CONSUMED"]


def test_nothing_is_reported_as_realized(tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, _ = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)])
    assert rep["realized"] == "NOT_REALIZED"
    assert rep["settlement"] == "NOT_OBSERVED_IN_THIS_PROCESS"
    assert "submitted" in rep and "filled" in rep


# 9 ------------------------------------------------- frozen L5 hash unchanged

def test_the_frozen_engine_is_untouched():
    from src import l5_session_integrity as IG
    out = IG.frozen_ok()
    assert out["ok"], out["mismatches"]


def test_this_probe_adds_no_decision_logic():
    """The decision comes from the frozen rule, not from here."""
    import ast
    import pathlib as _p
    tree = ast.parse(_p.Path("scripts/l5_pmus_live_oneshot.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for banned in ("l5_native", "pmus_translate"):
        assert banned not in names, "the probe must not re-implement %s" % banned


# 10 ------------------------------------- the observer stays structurally GET-only

def test_the_observer_image_gains_no_order_path():
    df = pathlib.Path("deploy/pmus_ws_recorder/Dockerfile").read_text()
    copies = [l for l in df.splitlines()
              if l.strip().upper().startswith("COPY ")]
    for banned in ("l5_pmus_live_oneshot", "l5_live_authorization",
                   "pmus_q1_executor", "pmus_micro_executor",
                   "l5_preview_session"):
        assert not [l for l in copies if banned in l], banned


def test_nothing_in_the_observer_calls_arm():
    import ast
    import pathlib as _p
    for f in ("scripts/l5_pmus_realtime_probe.py",
              "src/pmus_ws_evidence_recorder_enriched.py"):
        p = _p.Path(f)
        if not p.exists():
            continue
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "arm"):
                pytest.fail("%s calls arm()" % f)


def test_every_arming_path_goes_through_the_one_shared_boundary():
    """RESTATED 2026-10-14, and strictly STRONGER than what it replaced.

    It used to assert that every zero-argument `.arm()` call site lived in
    `l5_pmus_live_oneshot`. That was a LOCATION test, and it was protecting
    the wrong thing: `src/l5_pmus_live_runner.py` armed too, and its
    preconditions were a SUPERSET of this module's -- it checked the halt
    latch, unresolved accounting, venue reconciliation and uncertain orders,
    none of which the blessed path checked. The invariant was blessing the
    weaker path, and it could only be satisfied by deleting a deployed
    production workflow.

    What actually matters is that there is ONE place the decision is taken.
    So the assertion is now that every module able to arm also calls
    `l5_live_authorization.authorize_arming`, which is the union of both
    paths' preconditions and the only claimant of the durable single-use
    authorization. Two call sites with one boundary is safe; one call site
    with no boundary was not.

    AST, not grep: docstrings that explain arm() are not calls to it.
    """
    import ast
    import pathlib as _p

    armers, guarded, callers = set(), set(), []
    for f in list(_p.Path("src").glob("*.py")) + list(_p.Path("scripts").glob("*.py")):
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:                                    # pragma: no cover
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            if (isinstance(n.func, ast.Attribute)
                    and n.func.attr == "arm" and not n.args):
                armers.add(f.name)
                callers.append("%s:%d" % (f, n.lineno))
            if getattr(n.func, "attr", None) == "authorize_arming":
                guarded.add(f.name)
    assert callers, "something must be able to arm, or the probe cannot run"
    assert armers <= guarded, (
        "these modules can arm but never reach the shared boundary: %s"
        % sorted(armers - guarded))


# ------------------------------- the book the decision is actually taken against

def test_the_probe_decides_against_the_authenticated_feed(
        tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, _ = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)])
    assert rep["book_feed"]["source"] == "AUTHENTICATED_WS"
    assert rep["book_feed"]["fresh"] is True


def test_a_stale_book_produces_no_order(tmp_path, monkeypatch):
    """A book older than the bound is not a book you may trade against."""
    authorize(monkeypatch)
    stale = [{"t": START - 600.0, "bid": "0.40", "ask": "0.60",
              "bid_qty": "500", "ask_qty": "500", "status": "OK"}]
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)],
                       books=stale)
    assert venue.posts == []
    assert rep["submitted"] == 0


def test_no_book_at_all_produces_no_order(tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)],
                       books=[])
    assert venue.posts == []
    assert rep["submitted"] == 0
    assert rep["refused_reason"].get("NO_BOOK") == 1


def test_the_probe_refuses_to_arm_without_an_authenticated_book(
        tmp_path, monkeypatch):
    """Started for real, a feed that never produces a book stops the probe."""
    authorize(monkeypatch)

    class Dead(Feed):
        def latest(self):
            return None

        def books(self):
            return []

        def wait_ready(self, timeout_s=60.0, sleep=None):
            return False

    t = {"now": START + P.PREFLIGHT_AT_S}
    venue = Venue()
    monkeypatch.setenv(AUTH.MARKET_ENV, MARKET)
    monkeypatch.setattr(P, "new_l5_actions",
                        lambda slug, seen, act=None, health=None: ([], True))
    monkeypatch.setattr(P.FEED, "AuthenticatedBookFeed",
                        lambda market, clock=None: Dead(lambda: t["now"]))
    rep = P.run(window_bounds=venue_window(MARKET), clock=lambda: t["now"],
                sleep=lambda s: t.__setitem__("now", t["now"] + s),
                transport_factory=lambda slug: venue,
                state_path=str(tmp_path / "inv.json"),
                journal_path=str(tmp_path / "j.jsonl"),
                ledger_path=str(tmp_path / "auth.jsonl"))
    assert venue.armed is False, "never armed without a book"
    assert venue.posts == []
    assert rep["authorization"]["consumed"] is True


# ------------------------------- the row shape the frozen rule actually needs
#
# Probe 02 armed, connected an authenticated websocket, took a book fresh to
# 0.199s -- and handled zero L5 actions. The runner built its action rows by
# hand:
#
#     out.append(dict(r, action_key=key, receipt_utc=...))
#
# which passes the RAW venue activity row through. FT.l5_native returns None
# for that shape, so every action would have been refused UNREADABLE_ROW.
# The continuous recorder, which has processed tens of thousands of actions,
# builds the row with O.action_key and O.l5_to_pmus_action under an "l5" key.
# The probe now does the same.

# A real activity row carries the CTF token it traded, and the identity gate
# admits an action only if that token is one of the two the venue publishes
# for the window. These fixtures carry one, and the binding is injected, so
# what they exercise is the probe path -- not the network, and not the
# admission policy, which is tested in test_l5_market_identity.py.
TOKEN_UP, TOKEN_DOWN = "11111111", "22222222"

RAW_ACTIVITY = {"timestamp": None, "outcome": "Up", "side": "BUY",
                "price": 0.47, "size": 12.0, "usdcSize": 5.64,
                "transactionHash": "0xabc", "asset": TOKEN_UP,
                "conditionId": "0xcond"}


def bind(market=None):
    """Bind the window under test to a market carrying these tokens."""
    from src import l5_market_binding as MB
    from src import l5_window_actions as WA
    ws = P.window_start(market or MARKET)
    if ws is None:          # an unparseable slug binds to nothing, on purpose
        return None
    WA._BINDINGS[int(ws)] = MB.Binding(
        window_start=int(ws), condition_id="0xcond",
        token_up=TOKEN_UP, token_down=TOKEN_DOWN, slug="bound")
    return ws


def raw_at(t, **kw):
    bind()
    return dict(RAW_ACTIVITY, timestamp=t, **kw)


def test_a_built_action_is_readable_by_the_frozen_rule():
    """The property Probe 02 lacked, asserted end to end."""
    import src.l5_faithful_translation as FT

    # the row is built lazily inside the feed lambda, which runs AFTER the
    # binding lookup, so the window is bound up front here
    ws = bind()
    acts, ok = P.new_l5_actions(MARKET, set(),
                                activity=lambda limit=100: [raw_at(ws + 10)])
    assert ok is True
    assert len(acts) == 1
    row = acts[0]
    assert "l5" in row, "the frozen rule reads row['l5']"
    dec = FT.l5_native(row)
    assert dec is not None, "Probe 02's row shape returned None here"
    assert dec["side"] == "UP"
    assert str(dec["price"]) == "0.47"


def test_the_raw_venue_row_is_exactly_what_the_frozen_rule_rejects():
    """States the defect directly, so the fix cannot silently regress."""
    import src.l5_faithful_translation as FT
    assert FT.l5_native(dict(RAW_ACTIVITY, timestamp=1, action_key="k",
                             receipt_utc="2026-09-24T20:00:00+00:00")) is None


def test_the_probe_builds_rows_the_same_way_the_recorder_does():
    ws = P.window_start(MARKET)
    raw = raw_at(ws + 10)
    acts, _ = P.new_l5_actions(MARKET, set(), activity=lambda limit=100: [raw])
    import src.l5_pmus_realtime_observer as O
    assert acts[0]["action_key"] == O.action_key(raw)
    assert acts[0]["l5"] == O.l5_to_pmus_action(raw)
    assert acts[0]["pmus"] == {"slug": MARKET}


def test_an_action_with_no_readable_side_is_dropped():
    ws = P.window_start(MARKET)
    acts, ok = P.new_l5_actions(
        MARKET, set(),
        activity=lambda limit=100: [raw_at(ws + 10, outcome="Sideways")])
    assert ok is True and acts == []


def test_two_fills_in_one_transaction_are_both_kept():
    """The dedupe key is the economic identity, not the transaction hash."""
    ws = P.window_start(MARKET)
    rows = [raw_at(ws + 10, price=0.47), raw_at(ws + 10, price=0.48)]
    acts, _ = P.new_l5_actions(MARKET, set(), activity=lambda limit=100: rows)
    assert len(acts) == 2, "hashing by tx would have dropped one"


def test_an_action_outside_the_window_is_dropped():
    ws = P.window_start(MARKET)
    acts, _ = P.new_l5_actions(
        MARKET, set(), activity=lambda limit=100: [raw_at(ws - 5),
                                                   raw_at(ws + 2000)])
    assert acts == []


def test_an_unparseable_window_admits_nothing(monkeypatch):
    """Fail CLOSED: an unknown window must not admit L5's whole history."""
    from src import l5_authenticated_observation as OBS
    monkeypatch.setattr(OBS, "window_start_epoch", lambda slug: None)
    acts, ok = P.new_l5_actions(MARKET, set(),
                                activity=lambda limit=100: [raw_at(1790280000)])
    assert ok is True and acts == []


def test_a_readable_action_reaches_a_decision_in_the_probe(
        tmp_path, monkeypatch):
    """The whole path: raw venue row -> probe -> frozen rule -> a record."""
    authorize(monkeypatch)
    ws = P.window_start(MARKET)
    monkeypatch.setattr(AUTH, "MARKET_ENV", AUTH.MARKET_ENV)
    monkeypatch.setenv(AUTH.MARKET_ENV, MARKET)

    t = {"now": START + P.PREFLIGHT_AT_S}
    venue = Venue(fill="0")

    real = P.new_l5_actions          # capture BEFORE patching, or it recurses

    def once(slug, seen, act=None, health=None):
        if "done" in seen:
            return [], True
        seen.add("done")
        return real(slug, set(), activity=lambda limit=100: [raw_at(ws + 10)])

    monkeypatch.setattr(P, "new_l5_actions", once)
    rep = P.run(window_bounds=venue_window(MARKET), clock=lambda: t["now"],
                sleep=lambda s: t.__setitem__("now", t["now"] + s),
                transport_factory=lambda slug: venue,
                book_feed=Feed(lambda: t["now"]),
                state_path=str(tmp_path / "inv.json"),
                journal_path=str(tmp_path / "j.jsonl"),
                ledger_path=str(tmp_path / "auth.jsonl"))
    assert rep["armed"] is True
    assert rep["l5_actions"] == 1, "the action reached a decision"
    # and it was NOT rejected for being the wrong shape
    assert "UNREADABLE_ROW" not in rep["refused_reason"]


# ------------------------------------------------- diagnostics, not guesswork
#
# Probe 02 exited 9.5 minutes early with zero L5 actions and the reason went
# only to the journal on a volume that cannot be read once a one-shot
# container exits. These make the same failure legible from outside.

def test_a_probe_error_reaches_stdout(tmp_path, monkeypatch, capsys):
    authorize(monkeypatch)
    monkeypatch.setenv(AUTH.MARKET_ENV, MARKET)

    def boom(slug, seen, act=None, health=None):
        raise RuntimeError("the window loop fell over")

    monkeypatch.setattr(P, "new_l5_actions", boom)
    t = {"now": START + P.PREFLIGHT_AT_S}
    P.run(window_bounds=venue_window(MARKET), clock=lambda: t["now"],
          sleep=lambda s: t.__setitem__("now", t["now"] + s),
          transport_factory=lambda slug: Venue(),
          book_feed=Feed(lambda: t["now"]),
          state_path=str(tmp_path / "inv.json"),
          journal_path=str(tmp_path / "j.jsonl"),
          ledger_path=str(tmp_path / "auth.jsonl"))
    out = capsys.readouterr().out
    assert "L5_PROBE_ERROR" in out
    line = [l for l in out.splitlines() if l.startswith("L5_PROBE_ERROR")][0]
    err = json.loads(line[len("L5_PROBE_ERROR "):])
    assert err["error"] == "RuntimeError"
    assert "fell over" in err["detail"]
    assert err["stage"] == "working_window", "it names where it got to"
    assert "l5_feed" in err and "book_feed" in err


def test_a_probe_error_carries_no_credential(tmp_path, monkeypatch, capsys):
    """The scrubber is the transport's own, not a second one."""
    authorize(monkeypatch)
    monkeypatch.setenv(AUTH.MARKET_ENV, MARKET)
    monkeypatch.setenv("PMUS_API_SECRET_KEY", "SUPERSECRETVALUE12345")

    def boom(slug, seen, act=None, health=None):
        raise RuntimeError("failed with X-PM-Signature: abc123 and "
                           "secret SUPERSECRETVALUE12345")

    monkeypatch.setattr(P, "new_l5_actions", boom)
    t = {"now": START + P.PREFLIGHT_AT_S}
    P.run(window_bounds=venue_window(MARKET), clock=lambda: t["now"],
          sleep=lambda s: t.__setitem__("now", t["now"] + s),
          transport_factory=lambda slug: Venue(),
          book_feed=Feed(lambda: t["now"]),
          state_path=str(tmp_path / "inv.json"),
          journal_path=str(tmp_path / "j.jsonl"),
          ledger_path=str(tmp_path / "auth.jsonl"))
    out = capsys.readouterr().out
    assert "SUPERSECRETVALUE12345" not in out
    assert "abc123" not in out


def test_the_report_distinguishes_a_broken_feed_from_a_quiet_market():
    """Probe 02 could not tell these apart. Now they read differently."""
    ws = P.window_start(MARKET)

    quiet = WA.FeedHealth()
    WA.new_l5_actions(MARKET, set(), activity=lambda limit=100: [],
                      health=quiet)
    broken = WA.FeedHealth()
    WA.new_l5_actions(MARKET, set(), activity=lambda limit=100: None,
                      health=broken)
    busy = WA.FeedHealth()
    WA.new_l5_actions(MARKET, set(),
                      activity=lambda limit=100: [raw_at(ws + 10)],
                      health=busy)

    assert quiet.as_dict()["healthy"] is True
    assert quiet.as_dict()["raw_rows"] == 0
    assert broken.as_dict()["healthy"] is False
    assert broken.as_dict()["polls_failed"] == 1
    assert broken.as_dict()["last_error"]
    assert busy.as_dict()["raw_rows"] == 1
    assert busy.as_dict()["rows_in_window"] == 1
    assert busy.as_dict()["new_actions"] == 1


def test_rows_outside_the_window_are_counted_apart_from_new_actions():
    ws = P.window_start(MARKET)
    h = WA.FeedHealth()
    WA.new_l5_actions(MARKET, set(),
                      activity=lambda limit=100: [raw_at(ws + 10),
                                                   raw_at(ws - 500)],
                      health=h)
    d = h.as_dict()
    assert d["raw_rows"] == 2 and d["rows_in_window"] == 1
    assert d["new_actions"] == 1


def test_the_final_report_carries_feed_health_and_stage(tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, _ = drive(tmp_path, monkeypatch, actions=[])
    assert "l5_feed" in rep and "stage_reached" in rep
    assert rep["book_feed"]["source"] == "AUTHENTICATED_WS"


# ------------------------------------------- one implementation, not two

def test_the_probe_has_no_private_copy_of_the_action_feed():
    """The Probe 02 defect class: a second implementation of a proven one."""
    import ast
    import pathlib as _p
    tree = ast.parse(_p.Path("scripts/l5_pmus_live_oneshot.py").read_text())
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)}
    for shared in ("new_l5_actions", "current_slug", "window_start"):
        assert shared not in defined, (
            "%s is defined here as well as in l5_window_actions" % shared)


def test_both_runners_use_the_same_action_feed():
    import scripts.l5_pmus_realtime_probe as R
    assert P.new_l5_actions is WA.new_l5_actions
    assert R.new_l5_actions is WA.new_l5_actions
    assert P.current_slug is WA.current_slug is R.current_slug


# ------------------------------------------------------------- observe only
#
# Probes 01 and 02 both died in the pre-execution path and both spent a money
# authorization to find out. Observe-only exercises that same production path
# with no capability to place an order. The impossibility must be structural.

def observe(tmp_path, monkeypatch, *, rows=None, books=None, transport=None,
            market_now=None):
    ws_market = MARKET
    ws = P.window_start(ws_market)
    t = {"now": (market_now if market_now is not None else ws + 1.0)}
    monkeypatch.setattr(P, "current_slug", lambda now=None: ws_market)
    served = {"done": False}

    def activity(limit=100):
        if served["done"]:
            return []
        served["done"] = True
        return list(rows or [])

    def tick(s):
        t["now"] += 60.0            # burn the window quickly

    return P.observe(clock=lambda: t["now"], sleep=tick,
                     book_feed=Feed(lambda: t["now"], books),
                     activity=activity, transport=transport,
                     state_path=str(tmp_path / "obs.json"),
                     journal_path=str(tmp_path / "obs.jsonl"))


def test_observe_only_never_claims_an_authorization(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(AUTH.Authorization, "claim",
                        lambda self, m: calls.append(m))
    rep = observe(tmp_path, monkeypatch)
    assert calls == [], "observe-only must not claim"
    assert rep["authorization_claimed"] is False
    led = tmp_path / "auth.jsonl"
    assert not led.exists(), "no ledger entry is written"


def test_observe_only_works_without_any_owner_token(tmp_path, monkeypatch):
    deauthorize(monkeypatch)
    ws = P.window_start(MARKET)
    rep = observe(tmp_path, monkeypatch, rows=[raw_at(ws + 10)])
    assert rep["LABEL"] == "LIVE_OBSERVE_ONLY"
    assert rep["actions_handled"] >= 1


def test_observe_only_constructs_no_post_capable_transport(
        tmp_path, monkeypatch):
    """Construction-level impossibility, not a flag."""
    from src import l5_pmus_transport as TP

    def forbidden(*a, **kw):
        raise AssertionError("observe-only constructed PmusTransport")

    monkeypatch.setattr(TP, "PmusTransport", forbidden)
    ws = P.window_start(MARKET)
    rep = observe(tmp_path, monkeypatch, rows=[raw_at(ws + 10)])
    assert rep["post_capable"] is False
    assert rep["transport"] == "NULL"


def test_the_observe_transport_cannot_send_even_if_asked(tmp_path):
    """The object the executor holds has no way to transmit."""
    import src.l5_live_execution as E
    tp = E.NullTransport()
    assert tp.armed is False
    assert not hasattr(tp, "http")
    with pytest.raises(E.NotArmed):
        tp.submit({"market": "m", "side": "UP", "price": D("0.5"),
                   "qty": D("1"), "client_order_id": "x"})
    with pytest.raises(E.NotArmed):
        tp.cancel("x")


def test_an_executable_action_still_cannot_reach_a_venue(
        tmp_path, monkeypatch):
    """The required property: a would-submit is as far as it goes."""
    ws = P.window_start(MARKET)
    rep = observe(tmp_path, monkeypatch, rows=[raw_at(ws + 10, size=2.0)])
    assert rep["submitted_live"] == 0
    assert rep["armed"] is False
    # whatever the decision path concluded, nothing left the process
    assert rep["would_submit"] >= 0


def test_observe_only_reaches_the_frozen_rule_and_the_translation(
        tmp_path, monkeypatch):
    """The exact path Probes 01 and 02 never exercised live."""
    ws = P.window_start(MARKET)
    rep = observe(tmp_path, monkeypatch, rows=[raw_at(ws + 10, size=2.0)])
    assert rep["actions_handled"] == 1
    assert rep["l5_native_decisions"] == 1, "the row was readable"
    assert "UNREADABLE_ROW" not in (rep["refused_reason"] or {})


def test_observe_only_carries_the_diagnostics(tmp_path, monkeypatch):
    ws = P.window_start(MARKET)
    rep = observe(tmp_path, monkeypatch, rows=[raw_at(ws + 10)])
    f = rep["l5_feed"]
    for k in ("polls", "polls_ok", "polls_failed", "raw_rows",
              "rows_in_window", "new_actions", "last_ok_utc", "last_error"):
        assert k in f, k
    b = rep["book_feed"]
    assert b["source"] == "AUTHENTICATED_WS"
    assert "stop_reason" in b or "fresh" in b
    assert rep["stage_reached"] == "done"


def test_observe_only_surfaces_its_error_on_stdout(
        tmp_path, monkeypatch, capsys):
    def boom(slug, seen, act=None, health=None):
        raise RuntimeError("observe loop fell over")

    monkeypatch.setattr(P, "new_l5_actions", boom)
    rep = observe(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "L5_PROBE_ERROR" in out
    err = json.loads([l for l in out.splitlines()
                      if l.startswith("L5_PROBE_ERROR")][0][len("L5_PROBE_ERROR "):])
    assert err["mode"] == "OBSERVE_ONLY"
    assert err["stage"] == "observing"
    assert rep["stage_reached"] == "error"


def test_observe_mode_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv(P.OBSERVE_ENV, raising=False)
    assert P.observe_only_enabled() is False
    monkeypatch.setenv(P.OBSERVE_ENV, "YES")
    assert P.observe_only_enabled() is True


def test_observe_mode_does_not_read_the_arming_variables(monkeypatch):
    """It must neither require nor accept the money token."""
    import ast
    import inspect
    src = inspect.getsource(P.observe)
    for env in ("PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID", "PMUS_Q1_ARM"):
        assert env not in src
    tree = ast.parse(src.lstrip())
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("claim", "arm", "consume")):
            pytest.fail("observe-only calls %s" % n.func.attr)


# ------------------------------------- the final report, on a NON-ZERO run
#
# 2026-09-25T00:30:06Z: observe() ran a whole window and then died writing
# its summary -- `len(summary.get("would_submit") or [])`, where summarize()
# already returns a count. At zero the falsy int became [] and the
# expression gave the right answer, so every test passed; at one it raised
# TypeError. The report could only be written when there was nothing to
# report, and a window's evidence went to the volume with it.
#
# The lesson is not "assert on that expression". It is that observe()'s
# reporting tail had never once run against real numbers. So this drives a
# realistic window -- marketable and unmarketable actions, rows in and out
# of the window -- and validates the WHOLE report dict, then the journal
# record written from it.

class RichFeed:
    """A book feed double in the AUTHENTICATED feed's real report shape.

    The plain `Feed` above omits frames, last_book_age_s, errors and
    stop_reason, so a report built from it cannot show the fields an
    operator actually reads.
    """

    def __init__(self, clock, bid="0.40", ask="0.60"):
        self.clock = clock
        self.bid, self.ask = bid, ask
        self.frames = 0
        self.started = self.stopped = False

    def books(self):
        self.frames += 1
        now = self.clock()
        return [{"t": now - 1.0, "bid": self.bid, "ask": self.ask,
                 "bid_qty": "500", "ask_qty": "500", "status": "OK"},
                {"t": now - 0.2, "bid": self.bid, "ask": self.ask,
                 "bid_qty": "500", "ask_qty": "500", "status": "OK"}]

    def latest(self):
        return self.books()[-1]

    def age_s(self):
        return self.clock() - self.latest()["t"]

    def fresh(self):
        return self.age_s() <= 2.0

    def health(self):
        return {"market": MARKET, "connected": self.started and not self.stopped,
                "frames": self.frames, "book_states": len(self.books()),
                "last_book_age_s": round(self.age_s(), 3),
                "fresh": self.fresh(), "errors": [],
                "stop_reason": ("local_cancellation" if self.stopped
                                else "running"),
                "source": "AUTHENTICATED_WS"}

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wait_ready(self, timeout_s=60.0, sleep=None):
        return True


class _FakeTime:
    """`time` as l5_window_actions sees it, on the simulated clock.

    new_l5_actions stamps receipt_utc from time.time(), and the translation
    prices an action against the book's age at that receipt. On a real clock
    beside a simulated book every action is refused STALE_OBSERVATION, which
    is correct behaviour and not what this is testing.
    """

    def __init__(self, t):
        self._t = t

    def time(self):
        return self._t["now"]


def busy_observe(tmp_path, monkeypatch):
    """One window with real traffic through it. Returns (report, paths)."""
    ws = P.window_start(MARKET)
    t = {"now": ws + 1.0}
    monkeypatch.setattr(P, "current_slug", lambda now=None: MARKET)
    monkeypatch.setattr(WA, "time", _FakeTime(t))

    #: 0.65 against an ask of 0.60 is marketable, so it translates and the
    #: transport refuses it NOT_ARMED -- a would-submit. 0.47 rests strictly
    #: inside the spread, which the frozen rule refuses STRICT_NOT_EVALUABLE
    #: without an observed interaction. One row sits outside the window.
    rows = [raw_at(ws + 10, price=0.65, size=12.0, transactionHash="0xtake"),
            raw_at(ws + 20, price=0.47, size=12.0, transactionHash="0xrest"),
            raw_at(ws - 500, price=0.65, size=12.0, transactionHash="0xold")]
    served = {"n": 0}

    def activity(limit=100):
        served["n"] += 1
        return list(rows) if served["n"] == 1 else []

    def tick(s):
        t["now"] += 60.0

    feed = RichFeed(lambda: t["now"])
    journal = tmp_path / "obs.jsonl"
    rep = P.observe(clock=lambda: t["now"], sleep=tick, book_feed=feed,
                    activity=activity, state_path=str(tmp_path / "obs.json"),
                    journal_path=str(journal))
    return rep, journal


def test_the_whole_report_survives_a_window_that_actually_did_something(
        tmp_path, monkeypatch):
    """Every field, on a run with non-zero counts. This is the regression.

    Reinstating `len(summary.get("would_submit") or [])` makes this raise
    TypeError, because would_submit is 1 here and not 0.
    """
    rep, _ = busy_observe(tmp_path, monkeypatch)

    # --- the safety properties, which must hold on a busy window too
    assert rep["LABEL"] == "LIVE_OBSERVE_ONLY"
    assert rep["market"] == MARKET
    assert rep["stage_reached"] == "done", "no early exit"
    assert rep["authorization_claimed"] is False
    assert rep["post_capable"] is False
    assert rep["transport"] == "NULL"
    assert rep["armed"] is False
    assert rep["submitted_live"] == 0

    # --- the feed counted a real poll sequence, not a single lucky one
    f = rep["l5_feed"]
    assert f["polls"] > 1 and f["polls_ok"] == f["polls"]
    assert f["polls_failed"] == 0
    assert f["raw_rows"] == 3, "all three rows were seen"
    assert f["rows_in_window"] == 2, "the third is outside the window"
    assert f["new_actions"] == 2
    assert f["last_ok_utc"] and f["last_error"] is None
    assert f["healthy"] is True

    # --- the book feed reported its own statistics, not a stub
    b = rep["book_feed"]
    assert b["source"] == "AUTHENTICATED_WS"
    assert b["frames"] > 0 and b["book_states"] == 2
    assert b["last_book_age_s"] is not None and b["fresh"] is True
    assert b["errors"] == [] and b["stop_reason"]

    # --- the decision chain, end to end, with both outcomes present
    assert rep["actions_handled"] == 2
    assert rep["l5_native_decisions"] == 2, "both rows were readable"
    assert rep["pmus_translations_ok"] == 1, "only the marketable one"
    assert rep["would_submit"] == 1, "and it got as far as the transport"
    assert rep["overlay_refusals"] == 0

    # --- the refusal that did happen is attributed and counted
    assert rep["refused_by_layer"] == {"PMUS_TRANSLATION": 1}
    assert sum(rep["refused_reason"].values()) == 1
    assert "UNREADABLE_ROW" not in rep["refused_reason"]

    assert rep["live_limits"]["max_usd_per_market"] == "5"
    assert rep["live_limits"]["max_live_orders"] == 8
    assert "OBSERVE ONLY" in rep["labels"]


def test_would_submit_is_the_count_summarize_already_produced(
        tmp_path, monkeypatch):
    """Producer and consumer agree on the type, which is what broke."""
    import src.l5_pmus_live_wiring as W

    produced = W.summarize([{"ORDER": {"submitted": False,
                                       "reason": "NOT_ARMED",
                                       "intent": {"mode": "TAKER"}}}])
    assert isinstance(produced["would_submit"], int)
    with pytest.raises(TypeError):
        len(produced["would_submit"])        # the line that shipped

    rep, _ = busy_observe(tmp_path, monkeypatch)
    assert isinstance(rep["would_submit"], int) and rep["would_submit"] == 1


def test_the_busy_report_is_written_to_the_journal(tmp_path, monkeypatch):
    """The crash was one line BEFORE journal.write, so nothing persisted."""
    rep, journal = busy_observe(tmp_path, monkeypatch)
    recs = [json.loads(l) for l in
            journal.read_text().splitlines() if l.strip()]
    kinds = [r["kind"] for r in recs]
    assert "observe_book_feed" in kinds
    assert kinds.count("observe_action") == 2, "each action journaled"

    written = [r for r in recs if r["kind"] == "observe_report"]
    assert len(written) == 1, "the report reaches the journal"
    got = written[0]
    for k, v in rep.items():
        assert k in got, "the journal record dropped %s" % k
        assert got[k] == json.loads(json.dumps(v, default=str)), k


def test_every_report_field_survives_json(tmp_path, monkeypatch):
    """main() prints this. A field the serializer cannot take is a crash."""
    rep, _ = busy_observe(tmp_path, monkeypatch)
    line = json.dumps(rep, default=str)
    back = json.loads(line)
    assert set(back) == set(rep), "a field was lost in serialization"
    for k, v in back.items():
        assert not isinstance(v, (bytes, set)), k
    # no Decimal reaches the wire as a bare repr: limits pre-stringify
    assert all(isinstance(x, (str, int, type(None), list))
               for x in back["live_limits"].values())


def test_an_unreadable_row_does_not_count_as_a_decision(tmp_path):
    """The convention is CODE[:detail]; the comparison must honour it."""
    import src.l5_pmus_live_wiring as W
    recs = [{"refused_reason": "UNREADABLE_ROW"},
            {"refused_reason": "UNREADABLE_ROW:mangled qty"},
            {"refused_reason": "STALE_ACTION:12.4s"},
            {"refused_reason": None}]
    readable = sum(1 for r in recs if P.l5_native_was_readable(r))
    assert readable == 2, "detail on the code must not make it readable"
    assert P.l5_native_was_readable({"refused_reason": "UNREADABLE_ROW:x"}) \
        is False
    assert W.summarize([]) ["refused_reason"] == {}


# ------------------------------------------- the account this actually ran on
#
# PmusTransport has carried an account_matches_expected gate all along, but
# the probe called preflight() with no expect_account, so the gate never ran
# -- and report() collapsed the whole preflight to the single word "PASSED",
# sending the fingerprint only to the journal on a volume that cannot be read
# once a one-shot container exits. The account could therefore be neither
# pinned before arming nor checked afterwards.

def pin(monkeypatch, value):
    monkeypatch.setenv(P.ACCOUNT_ENV, value)


def test_a_fingerprint_mismatch_fails_preflight_and_never_arms(
        tmp_path, monkeypatch):
    """The required property: fail closed BEFORE arm(), with no order."""
    authorize(monkeypatch)
    pin(monkeypatch, "000000000000")
    venue = Venue()                      # answers as d51b44a3fa83
    rep, venue = drive(tmp_path, monkeypatch, venue=venue,
                       actions=[l5row()])

    assert rep["preflight"] == "FAILED"
    assert rep["account_matches_expected"] is False
    assert rep["expected_account"] == "000000000000"
    assert rep["api_key_fingerprint"] == "d51b44a3fa83"
    assert rep["armed"] is False, "arm() must never be reached"
    assert venue.armed is False
    assert venue.posts == [], "nothing may reach the venue"
    assert rep["submitted"] == 0


def test_the_matching_fingerprint_passes_and_the_report_says_so(
        tmp_path, monkeypatch):
    authorize(monkeypatch)
    pin(monkeypatch, Venue.FINGERPRINT)
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row()])

    assert rep["preflight"] == "PASSED"
    assert rep["account_matches_expected"] is True
    assert rep["expected_account"] == Venue.FINGERPRINT
    assert rep["api_key_fingerprint"] == Venue.FINGERPRINT
    assert rep["armed"] is True


def test_an_unpinned_run_reports_not_checked_rather_than_matched(
        tmp_path, monkeypatch):
    """None and False are different answers and must not be collapsed."""
    authorize(monkeypatch)
    monkeypatch.delenv(P.ACCOUNT_ENV, raising=False)
    rep, venue = drive(tmp_path, monkeypatch, actions=[l5row()])

    assert rep["expected_account"] is None
    assert rep["account_matches_expected"] is None, "not checked, not matched"
    assert rep["preflight"] == "PASSED"
    assert rep["armed"] is True, "behaviour without the pin is as before"


def test_the_fingerprint_reaches_stdout(tmp_path, monkeypatch):
    """main() prints this dict; the account must survive the serializer."""
    authorize(monkeypatch)
    pin(monkeypatch, Venue.FINGERPRINT)
    rep, _ = drive(tmp_path, monkeypatch, actions=[l5row()])
    back = json.loads(json.dumps(rep, default=str))
    assert back["api_key_fingerprint"] == Venue.FINGERPRINT
    assert back["account_matches_expected"] is True
    assert back["expected_account"] == Venue.FINGERPRINT


def test_the_probe_actually_passes_the_pin_to_the_transport(monkeypatch):
    """Guard the wiring itself: an ignored argument is an inert gate."""
    import inspect
    src = inspect.getsource(P.Probe.run_preflight)
    assert "expect_account=expected_account()" in src, (
        "run_preflight must pass the pin into PmusTransport.preflight")
    monkeypatch.setenv(P.ACCOUNT_ENV, "  abc123  ")
    assert P.expected_account() == "abc123"
    monkeypatch.setenv(P.ACCOUNT_ENV, "   ")
    assert P.expected_account() is None


def test_gate_result_keeps_absent_and_failed_apart():
    assert P.gate_result({"checks": []}, "account_matches_expected") is None
    assert P.gate_result(None, "account_matches_expected") is None
    assert P.gate_result(
        {"checks": [{"check": "account_matches_expected", "passed": False}]},
        "account_matches_expected") is False
    assert P.gate_result(
        {"checks": [{"check": "account_matches_expected", "passed": True}]},
        "account_matches_expected") is True
