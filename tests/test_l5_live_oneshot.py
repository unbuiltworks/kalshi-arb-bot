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
import src.l5_window_actions as WA
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
    rep = P.run(clock=clock, sleep=sleep,
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
    rep = P.run(clock=lambda: t["now"],
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

RAW_ACTIVITY = {"timestamp": None, "outcome": "Up", "side": "BUY",
                "price": 0.47, "size": 12.0, "usdcSize": 5.64,
                "transactionHash": "0xabc"}


def raw_at(t, **kw):
    return dict(RAW_ACTIVITY, timestamp=t, **kw)


def test_a_built_action_is_readable_by_the_frozen_rule():
    """The property Probe 02 lacked, asserted end to end."""
    import src.l5_faithful_translation as FT

    ws = P.window_start(MARKET)
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
    rep = P.run(clock=lambda: t["now"],
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
    P.run(clock=lambda: t["now"],
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
    P.run(clock=lambda: t["now"],
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
