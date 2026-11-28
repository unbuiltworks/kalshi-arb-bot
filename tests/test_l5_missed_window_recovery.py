"""The two windows of 2026-10-23 that were lost without a risk gate firing.

THE CAMPAIGN. Four windows were approved. 2015z and 2030z traded and settled
at +$0.2470 and +$0.3630. 2045z and 2100z submitted nothing, and neither was
stopped by a limit, a loss halt or an exposure ceiling. Both were lost to the
same two mechanics, and this file pins the repair of each.

ONE -- THE CLAIM WAS TAKEN BEFORE THE WINDOW WAS JUDGED WORTH TAKING.
`authorize_arming` writes the irreversible single-use claim lock. The question
"can this window still open a leg?" was asked AFTERWARDS. So at 20:49:16 the
lock was written for 2045z, at 20:49:23 usability refused it with 284s of
entry runway against the 300s floor, and the remaining 180 ticks of that
window were refused AUTHORIZATION_ALREADY_USED against a lock nothing can
release. The market was unreachable for the rest of its life.

TWO -- A FRESH MARKET'S REST BOOK 404s, AND THE SOCKET'S DOES NOT.
`book_readable` 404ed through its whole retry budget on both windows. 2045z
spent 256 seconds there and emerged 16 seconds past the entry floor; 2100z
never emerged at all. Meanwhile the authenticated socket had served that same
slug a two-sided book from +0.8s and 7,800 book updates. The socket book is
the same venue, the same market and the same credentials, so it is evidence
for the gate rather than a way around it -- and executability is still decided
at the decision point by l5_preview_gate.require_fresh_book.

Nothing here submits an order, uses a credential or opens a socket.
"""
from __future__ import annotations

import ast
import collections
import datetime as dt
import inspect
import pathlib
import tempfile
import textwrap
import urllib.error
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CAMP
from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_pmus_transport as T
from src import pmus_auth as Q1


# ======================================================== ONE: claim ordering

class _Transport:
    def __init__(self, slug: str) -> None:
        self.armed = False
        self.market_slug = slug
        self.tick_size = "0.01"
        self.arms = 0

    def arm(self):
        self.arms += 1
        self.armed = True
        return {"armed": True, "market": self.market_slug}


class _Store:
    def orders_uncertain(self):
        return None

    def residual(self, market):
        return D(0)


class _Limits:
    def as_dict(self):
        return {"max_usd_per_market": "5", "max_total_exposure_usd": "2",
                "daily_realized_loss_halt_usd": "5"}


class _Executor:
    limits = _Limits()
    halt_gate = object()


class _Runner:
    """Only what `try_arm` touches, driving the real code object.

    `authorize_arming` and `reserve_window` are replaced by spies in the
    fixture below: this file is about the ORDER in which they are reached,
    and the boundary's own refusals have their own tests in
    tests/test_l5_arming_boundary.py.
    """

    try_arm = R.Runner.try_arm
    campaign_window_is_usable = R.Runner.campaign_window_is_usable

    def __init__(self, *, offset_s: float) -> None:
        self.root = tempfile.mkdtemp(prefix="missed-window-")
        self.stats = collections.Counter()
        self.win = collections.Counter()
        self.transport = _Transport("cpc-btc-updown-15m-2026-10-23-2045z")
        self.store = _Store()
        self.executor = _Executor()
        self.preflight_status = "PASSED"
        self.reconciled = True
        self._window_start = 1792788300.0        # 2045z
        self.now_t = self._window_start + float(offset_s)

    def venue_window_bounds(self):
        s = dt.datetime.fromtimestamp(self._window_start, dt.timezone.utc)
        return {"source": "VENUE", "start": s.isoformat(),
                "end": (s + dt.timedelta(seconds=900)).isoformat()}

    def halted(self) -> bool:
        return False

    def reconcile_if_needed(self, now: float) -> None:
        pass


@pytest.fixture()
def spies(monkeypatch, tmp_path):
    """A satisfied boundary, an approved campaign, and a record of both."""
    sid = "missed-window-session"
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    seen = {"claims": 0, "reserves": 0}

    def _claim(**kw):
        seen["claims"] += 1
        return {"event": "WINDOW", "market": kw.get("market")}

    def _reserve(market, root=None):
        seen["reserves"] += 1
        return {"ok": True, "reason": None}

    monkeypatch.setattr(AUTH, "authorize_arming", _claim)
    monkeypatch.setattr(CAMP, "reserve_window", _reserve)
    monkeypatch.setattr(CAMP, "campaign_decision",
                        lambda market, root=None, reserve=False: {
                            "present": True, "ok": True, "reason": None})
    return seen


def test_a_window_past_the_entry_floor_never_takes_the_claim(spies):
    """2045z. 284s of runway against the 300s floor: refuse, claim nothing."""
    r = _Runner(offset_s=316.0)
    assert r.campaign_window_is_usable(r.transport.market_slug)["reason"] == (
        "PAST_ENTRY_WITH_NO_INVENTORY")
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_window_not_usable"] == 1
    assert spies["claims"] == 0, (
        "the irreversible single-use claim must not be written for a window "
        "that is already known to be unable to open a leg")
    assert spies["reserves"] == 0


def test_the_whole_rest_of_the_window_keeps_naming_the_real_reason(spies):
    """The 180 AUTHORIZATION_ALREADY_USED refusals become impossible.

    They were a consequence of the claim existing, so a path that never
    writes it cannot produce them however many times it is driven.
    """
    r = _Runner(offset_s=316.0)
    for i in range(1, 25):
        r.now_t += 3.0
        r.try_arm()
        assert r.win["arm_refused_window_not_usable"] == i
    assert spies["claims"] == 0
    assert not [k for k in r.win if "already_used" in k]


def test_a_window_with_runway_still_claims_and_reserves(spies):
    """The repair may only ever ADD a refusal, never remove arming."""
    r = _Runner(offset_s=100.0)
    r.try_arm()
    assert spies["claims"] == 1
    assert spies["reserves"] == 1
    assert r.transport.armed is True
    assert r.win["arm_refused_window_not_usable"] == 0


def test_unreadable_window_bounds_refuse_before_claiming_too(spies,
                                                             monkeypatch):
    """Fail closed keeps its direction, and now costs no claim either."""
    r = _Runner(offset_s=100.0)
    monkeypatch.setattr(type(r), "venue_window_bounds",
                        lambda self: None, raising=False)
    r.try_arm()
    assert r.transport.armed is False
    assert spies["claims"] == 0


def test_the_pre_claim_check_precedes_the_claim_in_the_source():
    """Structural, by AST: the ordering IS the fix, so pin it directly.

    Read off the parsed call sites rather than the text, so reformatting,
    comments and renamed locals cannot make this pass or fail spuriously.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(R.Runner.try_arm)))
    usable, claim = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "campaign_window_is_usable":
            usable.append(node.lineno)
        if isinstance(f, ast.Attribute) and f.attr == "authorize_arming":
            claim.append(node.lineno)
    assert usable and claim, "both call sites must still exist"
    assert min(usable) < min(claim), (
        "usability is decided BEFORE the irreversible claim is taken")


def test_the_post_claim_recheck_is_kept():
    """Runway shrinks monotonically, so the second call closes the gap."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(R.Runner.try_arm)))
    usable = [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute)
              and n.func.attr == "campaign_window_is_usable"]
    assert len(usable) == 2, (
        "one check before the claim and one after; dropping the second would "
        "let a window that expired mid-arm trade")


# ================================================= TWO: the socket book gate

SLUG = "cpc-btc-updown-15m-2026-09-23-2115z"
NOW = lambda: dt.datetime(2026, 9, 23, 21, 0, tzinfo=dt.timezone.utc)
BOOK_PATH = "/v1/markets/%s/book" % SLUG


class _HTTP:
    """The transport harness, with the book route raising a chosen status."""

    def __init__(self, book_status=None):
        self.book_status = book_status
        self.calls = []
        self.data = {
            "/v1/account/balances": {"balances": [{"buyingPower": "42.50"}]},
            "/v1/market/slug/%s" % SLUG: {"market": {
                "slug": SLUG, "seriesSlug": "btc-updown-15m",
                "active": True, "closed": False, "archived": False,
                "outcomes": '["Up", "Down"]',
                "orderPriceMinTickSize": "0.01",
                "minimumTradeQty": "1"}},
            BOOK_PATH: {"marketData": {
                "bid": "0.40", "ask": "0.60",
                "bid_shares": "100", "ask_shares": "100"}},
            "/v1/portfolio/positions": {"positions": []},
            "/v1/orders/open": {"orders": []},
        }

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if method != "GET":
            return {"order_id": "v1", "filled_qty": "0"}
        if path == BOOK_PATH and self.book_status is not None:
            raise urllib.error.HTTPError(
                "https://venue%s" % path, self.book_status,
                "Not Found" if self.book_status == 404 else "Error",
                None, None)
        if path not in self.data:
            raise RuntimeError("404 %s" % path)
        return self.data[path]


class _Socket:
    """The authenticated book observer, in its real two-method shape."""

    def __init__(self, *, bid="0.42", ask="0.58", age=0.4,
                 state="MARKET_STATE_OPEN", pre_open=True, updates=7800):
        self._snap = {"bid": bid, "ask": ask, "bid_shares": "35",
                      "ask_shares": "70", "stream_age_s": age,
                      "book_updates": updates, "market_state": state,
                      "source": "authenticated_ws"}
        self._pre_open = pre_open

    def snapshot(self, slug):
        if self._snap["bid"] is None or self._snap["ask"] is None:
            return None                     # the real one returns None too
        return dict(self._snap)

    def window_report(self, slug):
        return {"slug": slug, "subscribed_before_open": self._pre_open}


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")
    monkeypatch.setattr(T, "BOOK_READ_BACKOFF_S", 0.0)
    # THE LIMITER IS PROCESS-WIDE AND SURVIVES A TEST. Several preflights in
    # one session exhaust its burst and the next one fails on
    # account_readable -- a real property of the live process, and noise
    # here. Each transport gets its own generous bucket instead.
    T.reset_shared_limiter()
    monkeypatch.setenv("PMUS_RPS", "10000")
    monkeypatch.setenv("PMUS_BURST", "10000")


def tp(book_status=None, socket=None):
    t = T.PmusTransport(market_slug=SLUG, http=_HTTP(book_status),
                        now_fn=NOW)
    t.ws_book = socket
    return t


def check(rep, name):
    for c in rep.get("checks") or ():
        if c.get("check") == name:
            return c
    return None


def test_a_404_with_a_fresh_pre_opened_socket_book_passes(creds):
    """2100z's exact shape, and what should have happened."""
    rep = tp(404, _Socket()).preflight()
    assert rep["preflight"] == "PASSED"
    sub = check(rep, "book_readable_via_authenticated_ws")
    assert sub is not None and sub["passed"] is True
    assert sub["blocking"] is False
    assert sub["detail"]["source"] == "authenticated_ws"
    assert sub["detail"]["book_updates"] == 7800
    assert "404" in sub["detail"]["rest_error"]
    # and the substituted book is what the later informational gates read
    assert check(rep, "book_readable_now")["passed"] is True
    assert check(rep, "book_two_sided_at_preflight")["passed"] is True


def test_the_rest_route_is_still_tried_first_and_wins_when_it_answers(creds):
    """The fallback is never consulted on a route that works."""
    sock = _Socket(bid="0.99", ask="0.99")
    rep = tp(None, sock).preflight()
    assert rep["preflight"] == "PASSED"
    assert check(rep, "book_readable_via_authenticated_ws") is None
    assert "0.40" in str(check(rep, "book_readable_now")["detail"])


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_only_a_404_qualifies(creds, status):
    """Any other status is a genuine read failure and still fails closed."""
    with pytest.raises(T.PreflightFailed):
        tp(status, _Socket()).preflight()


def test_a_404_with_no_socket_at_all_still_fails(creds):
    with pytest.raises(T.PreflightFailed):
        tp(404, None).preflight()


@pytest.mark.parametrize("kw, why", [
    ({"bid": None}, "a one-sided book is not evidence of a readable book"),
    ({"ask": None}, "a one-sided book is not evidence of a readable book"),
    ({"age": 31.0}, "a connected but silent socket is not fresh evidence"),
    ({"age": None}, "an unmeasurable age is not permission"),
    ({"state": "MARKET_STATE_CLOSED"}, "a closed market is never tradable"),
    ({"state": "MARKET_STATE_UNOPENED"}, "nor is one that has not opened"),
    ({"pre_open": False}, "a late subscription has only a partial view"),
])
def test_the_socket_must_actually_be_equivalent_evidence(creds, kw, why):
    with pytest.raises(T.PreflightFailed):
        tp(404, _Socket(**kw)).preflight()


def test_a_socket_that_raises_is_no_substitute(creds):
    class Broken:
        def snapshot(self, slug):
            raise RuntimeError("socket thread died")

        def window_report(self, slug):
            return {}

    with pytest.raises(T.PreflightFailed):
        tp(404, Broken()).preflight()


def test_the_fallback_is_at_the_boundary_of_its_own_freshness_rule(creds):
    """Equality passes; one tick past it does not."""
    edge = T.WS_BOOK_FALLBACK_MAX_AGE_S
    assert tp(404, _Socket(age=edge)).preflight()["preflight"] == "PASSED"
    with pytest.raises(T.PreflightFailed):
        tp(404, _Socket(age=edge + 0.001)).preflight()


def test_the_runner_hands_the_transport_the_real_observer():
    """Structural: the wiring is what makes the fallback reachable live."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(R.Runner.roll)))
    wired = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "ws_book"]
    assert wired, "roll() must install the socket on the window's transport"


# ===================================== the residual ceiling, in runtime shape

def test_the_preflight_reports_the_effective_residual_ceiling(tmp_path,
                                                              monkeypatch):
    """The owner asked to read the applied cap without waiting for an order.

    Production's durable store already holds a clip of 37.73 from an earlier
    xrp market. Under the relative rule alone that permitted 75.46 contracts;
    the approved figure is 3, and this reports which one binds.
    """
    monkeypatch.setenv("PMUS_RESIDUAL_CAP_K", "2")
    (tmp_path / "inventory.json").write_text(
        '{"max_clip": {"cpcxrp-xrp-updown-15m-2026-10-16-0330z": "37.73",'
        ' "cpc-btc-updown-15m-2026-10-23-2015z": "1.3"}}')
    rep = AUTH.authorization_preflight("", root=str(tmp_path))
    row = [r for r in rep["rows"] if r["check"] == "residual_ceiling"][0]
    assert row["verdict"] == "PASS"
    assert row["largest_recorded_clip"] == "37.73"
    assert row["relative_cap_at_that_clip"] == "75.46"
    assert row["effective_cap_contracts"] == "5"
    assert row["which_binds"] == "ABSOLUTE"
    # the outer SafetyLimits bound is reported separately and is NOT the
    # approved figure -- conflating them is what produced the mismatch.
    assert row["outer_bound_contracts"] == "10"
    assert D(row["effective_cap_contracts"]) <= LX.ABS_RESIDUAL_CAP_CONTRACTS


def test_a_small_clip_still_lets_the_relative_rule_decide(tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv("PMUS_RESIDUAL_CAP_K", "2")
    (tmp_path / "inventory.json").write_text(
        '{"max_clip": {"cpc-btc-updown-15m-2026-10-23-2015z": "1.3"}}')
    rep = AUTH.authorization_preflight("", root=str(tmp_path))
    row = [r for r in rep["rows"] if r["check"] == "residual_ceiling"][0]
    assert row["effective_cap_contracts"] == "2.6"
    assert row["which_binds"] == "RELATIVE"


def test_an_unreadable_store_never_reddens_the_authorization_preflight(
        tmp_path):
    """This row is telemetry. A fault in it must not look like a blocker."""
    rep = AUTH.authorization_preflight(
        "", root=str(tmp_path / "does-not-exist"))
    row = [r for r in rep["rows"] if r["check"] == "residual_ceiling"][0]
    assert row["verdict"] in ("PASS", "NOT_EVALUATED")
    assert "residual_ceiling" not in rep["fail"]
    assert "residual_ceiling" not in rep["unknown"]


def test_an_unreadable_clip_is_not_reported_as_zero(tmp_path):
    """Zero would make the relative rule look tighter than reality."""
    assert R.largest_recorded_clip(root=str(tmp_path / "nope")) is None
    (tmp_path / "inventory.json").write_text('{"max_clip": {}}')
    assert R.largest_recorded_clip(root=str(tmp_path)) == D(0)
