"""The bounded live probe: one market, one authorization, then gone.

Ten properties, each of which is the reason this runs in its own one-shot
service instead of being switched on inside the 24/7 observer.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as P
import src.l5_live_authorization as AUTH
import src.l5_live_execution as EX
import src.pmus_auth as Q1
import tests.test_l5_live_execution as L

MARKET = "cpc-btc-updown-15m-2026-09-23-1615z"
START = P.window_start(MARKET)


def authorize(monkeypatch, sid="sess-001"):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)


def deauthorize(monkeypatch):
    for k in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(k, raising=False)


class Venue:
    """A transport with the real shape: read-only until arm() is called."""

    name = "VENUE_DOUBLE"

    def __init__(self, *, preflight="PASSED", fill="0", reject=None,
                 positions=(), open_orders=()):
        self.armed = False
        self.posts, self.cancels = [], []
        self._preflight = preflight
        self._fill = fill
        self._reject = reject
        self._positions = list(positions)
        self._open = list(open_orders)
        self.preflight_report = None

    def preflight(self):
        self.preflight_report = {"preflight": self._preflight, "checks": []}
        if self._preflight != "PASSED":
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

    def book(self, slug):
        return {"bestBid": "0.40", "bestAsk": "0.60",
                "bestBidQuantity": "500", "bestAskQuantity": "500"}


def drive(tmp_path, monkeypatch, *, venue=None, actions=None, ledger=None,
          market=MARKET):
    """Run one probe over a simulated window, with no real clock or network."""
    venue = venue or Venue()
    rows = list(actions or [])
    monkeypatch.setenv(AUTH.MARKET_ENV, market)

    t = {"now": START + P.PREFLIGHT_AT_S}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += s

    def once(slug, seen, act=None):
        """Deliver the window's actions the first time, then nothing."""
        if "done" in seen:
            return [], True
        seen.add("done")
        stamp = dt.datetime.fromtimestamp(t["now"], dt.timezone.utc).isoformat()
        return ([dict(r, action_key="k%d" % i, receipt_utc=stamp)
                 for i, r in enumerate(rows)], True)

    monkeypatch.setattr(P, "new_l5_actions", once)
    rep = P.run(clock=clock, sleep=sleep,
                transport_factory=lambda slug: venue,
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


# 5 ------------------------- one live order / $5 reservation through partials

def test_one_live_order_holds_through_a_partial_fill(tmp_path, monkeypatch):
    authorize(monkeypatch)
    # lambda-normalised size 2.0 translates to well under one share, so the
    # partial has to be smaller still: this fills a fraction and leaves the
    # rest resting, which is the case the envelope has to keep counting.
    venue = Venue(fill="0.001")
    rep, venue = drive(tmp_path, monkeypatch, venue=venue,
                       actions=[l5row(key="a", size=2.0),
                                l5row(key="b", size=2.0),
                                l5row(key="c", size=2.0)])
    assert len(venue.posts) == 1, "a partially filled order is still live"
    assert rep["refused_reason"].get("MAX_LIVE_ORDERS") == 2


def test_the_five_dollar_envelope_is_the_probe_envelope(tmp_path, monkeypatch):
    authorize(monkeypatch)
    rep, _ = drive(tmp_path, monkeypatch, actions=[l5row(size=2.0)])
    lim = rep["live_limits"]
    assert lim["max_usd_per_market"] == "5"
    assert lim["max_total_exposure_usd"] == "5"
    assert lim["max_abs_residual"] == "10"
    assert lim["max_concurrent_markets"] == 1
    assert lim["max_live_orders"] == 1
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
    assert events == ["CLAIMED", "CONSUMED"]


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


def test_only_the_oneshot_probe_arms_anything():
    """AST, not grep: docstrings that explain arm() are not calls to it."""
    import ast
    import pathlib as _p

    callers = []
    for f in list(_p.Path("src").glob("*.py")) + list(_p.Path("scripts").glob("*.py")):
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:                                    # pragma: no cover
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "arm" and not n.args):
                callers.append("%s:%d" % (f, n.lineno))
    assert callers, "something must be able to arm, or the probe cannot run"
    assert all("l5_pmus_live_oneshot" in c for c in callers), callers
