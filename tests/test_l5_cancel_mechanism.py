"""The PMUS cancellation-mechanism test: its preconditions, and its silence.

This mode exists to answer ONE venue question -- does place, rest,
cancel-by-exact-id and an authoritative re-read work for this account -- and
it is explicitly NOT an L5 action. So the properties pinned here are:

  1. it cannot be reached by forgetting a variable;
  2. every precondition is reported, not short-circuited, so one disarmed
     run says exactly which blockers remain;
  3. nothing is sent while any precondition fails;
  4. the 0.01-contract cap is enforced at the wire as well as in the
     executor, because this mode does not go through the executor;
  5. the independent halt and the durable ledger are preconditions, not
     afterthoughts.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as P
import src.l5_halt_gate as HG
import src.l5_live_authorization as AUTH
import src.l5_pmus_transport as TP

MARKET = "cpc-btc-updown-15m-2026-10-18-1145z"
START = P.window_start(MARKET)
#: mid-window, with plenty of room left
NOW = (START or 0) + 120.0

CAP = "L5_PROBE_MAX_ORDER_CONTRACTS"
REQ = "L5_PROBE_ORDER_CAP_REQUIRED"


class Feed:
    """The authenticated socket book, stubbed in the shape the real one
    returns -- `l5_window_actions.book_row`, not the REST ladder."""

    def __init__(self, bid="0.50", ask="0.52", market=MARKET):
        self.bid, self.ask, self.market = bid, ask, market
        self.started = self.stopped = 0

    def start(self):                                 # pragma: no cover
        self.started += 1

    def stop(self):                                  # pragma: no cover
        self.stopped += 1

    def wait_ready(self, timeout_s=60.0, sleep=None):  # pragma: no cover
        return True

    def latest(self):
        if self.bid is None and self.ask is None:
            return None
        return {"t": NOW, "bid": self.bid, "ask": self.ask,
                "bid_qty": "10", "ask_qty": "10",
                "status": ("OK" if self.bid is not None
                           and self.ask is not None else "ONE_SIDED")}

    def age_s(self):
        return 0.5

    def health(self):
        return {"market": self.market, "frames": 3, "source":
                "AUTHENTICATED_WS", "errors": []}


def top_of(feed):
    return P.feed_book_top(feed.latest(), feed.market, age_s=feed.age_s())


class Latch:
    """A halt store standing in for the watchdog, with a settable answer."""

    def __init__(self, halt=False, age_s=1.0, raises=False):
        self.halt, self.age_s, self.raises = halt, age_s, raises

    def get(self, key):
        if self.raises:
            raise HG.HaltReadUnavailable("endpoint unreachable")
        t = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(seconds=self.age_s)).isoformat()
        return json.dumps({"halt": self.halt, "t": t}).encode()

    def put(self, key, body):                        # pragma: no cover
        raise AssertionError("this side may only read the latch")


def configure(monkeypatch, *, cap="0.01", required="YES", live=None,
              price="0.40", side="UP", halt_url="http://watchdog.internal",
              latch=None, market=MARKET):
    for name, val in ((CAP, cap), (REQ, required),
                      ("L5_MAX_LIVE_ORDERS", live),
                      (P.MECH_PRICE_ENV, price), (P.MECH_SIDE_ENV, side),
                      (P.MECH_MARKET_ENV, market),
                      ("L5_HALT_URL", halt_url)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    monkeypatch.setattr(HG, "HttpHaltStore",
                        lambda url, **kw: (latch or Latch()))


def named(checks):
    return {c["check"]: c for c in checks}


# 1 ------------------------------------------------- it cannot be stumbled on

def test_the_mode_needs_its_own_flag(monkeypatch):
    monkeypatch.delenv("L5_RECOVER_ONLY", raising=False)
    monkeypatch.delenv(P.OBSERVE_ENV, raising=False)
    monkeypatch.delenv(P.MECHANISM_ENV, raising=False)
    assert P.selected_mode() == "LIVE_PROBE"
    monkeypatch.setenv(P.MECHANISM_ENV, "YES")
    assert P.selected_mode() == "CANCEL_MECHANISM"


@pytest.mark.parametrize("other,mode", [
    (P.OBSERVE_ENV, "OBSERVE_ONLY"),
    ("L5_RECOVER_ONLY", "RECOVER_ONLY"),
])
def test_observation_and_recovery_still_win(monkeypatch, other, mode):
    """A mode that cannot send must outrank one that can."""
    monkeypatch.delenv("L5_RECOVER_ONLY", raising=False)
    monkeypatch.delenv(P.OBSERVE_ENV, raising=False)
    monkeypatch.setenv(P.MECHANISM_ENV, "YES")
    monkeypatch.setenv(other, "YES")
    assert P.selected_mode() == mode


def test_the_probe_image_carries_the_halt_gate():
    """Without this module the arming boundary refuses HALT_GATE_ABSENT."""
    df = pathlib.Path("deploy/l5_live_probe/Dockerfile").read_text()
    assert "COPY src/l5_halt_gate.py src/l5_halt_gate.py" in df


# 2 ------------------------------------------------------------- the plan

def test_the_plan_states_every_field_before_anything_is_contacted(monkeypatch):
    configure(monkeypatch, price="0.40")
    plan = P.mechanism_plan(clock=lambda: NOW)
    assert plan["market"] == MARKET
    assert plan["side"] == "UP"
    assert plan["price"] == "0.40"
    assert plan["qty"] == "0.01"
    assert plan["post_only"] is True
    assert plan["mode"] == "MAKER"
    # a BUY settles worthless at worst, so the premium IS the loss
    assert plan["max_loss_usd"] == "0.0040"
    assert plan["cash_committed_usd"] == "0.0040"
    assert plan["window_remaining_s"] == pytest.approx(780.0, abs=1.0)
    assert "not an l5 strategy trade" in plan["labels"].lower()
    assert "post_only" in plan["fill_handling"]
    assert "never strategy" in plan["fill_handling"]


def test_the_max_loss_never_exceeds_a_cent(monkeypatch):
    """Whatever price is named, 0.01 contracts of a $1 instrument is a cent."""
    for px in ("0.01", "0.25", "0.50", "0.99"):
        configure(monkeypatch, price=px)
        plan = P.mechanism_plan(clock=lambda: NOW)
        assert D(plan["max_loss_usd"]) <= D("0.01")


# 3 ------------------------------------- every precondition, every time

ALL_CHECKS = (
    "probe_cap_declared", "probe_cap_usable", "probe_cap_is_one_hundredth",
    "order_qty_is_the_cap", "one_live_order", "halt_gate_module_present",
    "halt_url_configured", "halt_gate_permits",
    "authorization_ledger_durable", "side_is_up", "price_stated",
    "price_on_tick", "window_has_room", "book_readable",
    "price_non_marketable", "owner_authorization",
)


def test_nothing_is_short_circuited(monkeypatch, tmp_path):
    """With NOTHING configured, every check is still evaluated and named."""
    for name in (CAP, REQ, "L5_HALT_URL", P.MECH_PRICE_ENV,
                 P.MECH_SIDE_ENV, P.MECH_MARKET_ENV, "PMUS_Q1_ARM",
                 "PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    plan = P.mechanism_plan(clock=lambda: NOW)
    checks = P.mechanism_checks(plan, book=None,
                               ledger_path=str(tmp_path / "led.jsonl"),
                               clock=lambda: NOW)
    got = named(checks)
    for name in ALL_CHECKS:
        assert name in got, name
    assert got["owner_authorization"]["ok"] is False
    assert got["probe_cap_declared"]["ok"] is False


def test_the_happy_path_passes_everything_but_the_owner(monkeypatch,
                                                        tmp_path):
    """The shape of the authorized run: one check left, and it is a person."""
    configure(monkeypatch)
    monkeypatch.setattr(AUTH, "durability_refusal", lambda p: None)
    for name in ("PMUS_Q1_ARM", "PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    plan = P.mechanism_plan(clock=lambda: NOW)
    checks = P.mechanism_checks(plan, book=top_of(Feed()),
                               ledger_path=str(tmp_path / "led.jsonl"),
                               clock=lambda: NOW)
    failed = [c["check"] for c in checks if not c["ok"]]
    assert failed == ["owner_authorization"], failed


# 4 ------------------------------------------------- the individual refusals

def test_a_cap_other_than_one_hundredth_is_refused(monkeypatch, tmp_path):
    configure(monkeypatch, cap="0.02")
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["probe_cap_is_one_hundredth"]["ok"] is False


def test_more_than_one_live_order_is_refused(monkeypatch, tmp_path):
    configure(monkeypatch, live="8")
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["one_live_order"]["ok"] is False
    assert got["one_live_order"]["detail"]["max_live_orders"] == 8


@pytest.mark.parametrize("price,ok", [
    ("0.45", True),     # best_bid 0.50 minus the 0.05 margin: rests
    ("0.46", False),    # inside the margin
    ("0.50", False),    # AT the bid
    ("0.52", False),    # at the offer: would cross
    ("0.60", False),    # through the offer
])
def test_only_a_genuinely_non_marketable_price_passes(monkeypatch, tmp_path,
                                                      price, ok):
    configure(monkeypatch, price=price)
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["price_non_marketable"]["ok"] is ok


def test_an_off_tick_price_is_refused(monkeypatch, tmp_path):
    configure(monkeypatch, price="0.405")
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["price_on_tick"]["ok"] is False


def test_a_down_side_is_refused(monkeypatch, tmp_path):
    """BUY_SHORT's worst case is the venue's dollar reserve, not the premium."""
    configure(monkeypatch, side="DOWN")
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["side_is_up"]["ok"] is False


def test_an_unreachable_watchdog_refuses(monkeypatch, tmp_path):
    """Every failure reads as unavailable, and unavailable is not permitted."""
    configure(monkeypatch, latch=Latch(raises=True))
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["halt_gate_permits"]["ok"] is False
    assert got["halt_gate_permits"]["detail"]["reachable"] is False


def test_an_asserted_halt_refuses(monkeypatch, tmp_path):
    configure(monkeypatch, latch=Latch(halt=True))
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["halt_gate_permits"]["ok"] is False
    assert got["halt_gate_permits"]["detail"]["why"] == "halt_asserted"


def test_a_stale_latch_refuses(monkeypatch, tmp_path):
    configure(monkeypatch, latch=Latch(age_s=10_000.0))
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["halt_gate_permits"]["ok"] is False
    assert got["halt_gate_permits"]["detail"]["why"] == "halt_record_stale"


def test_an_unconfigured_halt_url_refuses(monkeypatch, tmp_path):
    configure(monkeypatch, halt_url=None)
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["halt_url_configured"]["ok"] is False
    assert got["halt_gate_permits"]["ok"] is False


def test_a_container_local_ledger_refuses(monkeypatch, tmp_path):
    """A claim lock a restart hands back is not a claim lock."""
    configure(monkeypatch)
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["authorization_ledger_durable"]["ok"] is False
    assert (got["authorization_ledger_durable"]["detail"]["refusal"]
            == "AUTHORIZATION_LEDGER_NOT_DURABLE")


def test_a_window_about_to_close_refuses(monkeypatch, tmp_path):
    configure(monkeypatch)
    late = (START or 0) + P.WINDOW_S - 30.0
    plan = P.mechanism_plan(clock=lambda: late)
    got = named(P.mechanism_checks(plan, book=top_of(Feed()),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: late))
    assert got["window_has_room"]["ok"] is False


def test_a_book_that_never_arrived_refuses(monkeypatch, tmp_path):
    """No socket book, no claim about where our price sits in it."""
    configure(monkeypatch)
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed(bid=None, ask=None)),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["book_readable"]["ok"] is False
    assert got["price_non_marketable"]["ok"] is False


def test_a_one_sided_book_refuses(monkeypatch, tmp_path):
    """Below the bid proves it rests; below the offer proves it will not
    cross. One side cannot establish both."""
    configure(monkeypatch)
    plan = P.mechanism_plan(clock=lambda: NOW)
    got = named(P.mechanism_checks(plan, book=top_of(Feed(ask=None)),
                                   ledger_path=str(tmp_path / "l.jsonl"),
                                   clock=lambda: NOW))
    assert got["book_readable"]["ok"] is False
    assert got["price_non_marketable"]["ok"] is False


def test_non_marketability_is_established_on_the_authenticated_book():
    """Not the REST ladder: the socket row, and the file proves it.

    tests/test_l5_live_book_feed.py walks this module for any `.book(` call
    precisely so the probe cannot drift back to the unsigned gateway
    snapshot. This asserts the positive side of that rule.
    """
    import inspect
    src = inspect.getsource(P.mechanism)
    assert "AuthenticatedBookFeed" in src
    assert ".book(" not in src
    assert "AUTHENTICATED_WS" in inspect.getsource(P.mechanism_checks)


# 5 ------------------------------------------------- it sends nothing, ever

def test_a_refused_run_contacts_no_write_path(monkeypatch, tmp_path):
    configure(monkeypatch)
    feed = Feed()
    rep = P.mechanism(clock=lambda: NOW, sleep=lambda s: None,
                      book_feed=feed,
                      journal_path=str(tmp_path / "j.jsonl"),
                      state_path=str(tmp_path / "i.json"),
                      ledger_path=str(tmp_path / "l.jsonl"))
    assert rep["refused"] == "MECHANISM_PRECONDITIONS_NOT_MET"
    assert rep["submitted"] == 0 and rep["cancelled"] == 0
    assert rep["armed"] is False
    assert rep["LABEL"] == "PMUS_CANCEL_MECHANISM"
    assert "owner_authorization" in rep["failed_checks"]
    # no transport of any kind was constructed, so nothing capable of a POST
    # existed in the process at all
    assert "intent" not in rep and "authorization" not in rep


def test_the_report_says_it_is_not_l5_evidence(monkeypatch, tmp_path):
    configure(monkeypatch)
    rep = P.mechanism(clock=lambda: NOW, sleep=lambda s: None,
                      book_feed=Feed(),
                      journal_path=str(tmp_path / "j.jsonl"),
                      state_path=str(tmp_path / "i.json"),
                      ledger_path=str(tmp_path / "l.jsonl"))
    low = rep["labels"].lower()
    assert "not an l5 action" in low
    assert "profitability" in low


# 6 ------------------------- the cap at the wire, for a non-executor caller

def cap_check(intent):
    """The transport's own refusal, called as the submit path calls it."""
    return TP.PmusTransport._require_within_order_cap(object(), intent)


def test_the_wire_permits_exactly_one_hundredth(monkeypatch):
    monkeypatch.setenv(CAP, "0.01")
    monkeypatch.setenv(REQ, "YES")
    assert cap_check({"qty": D("0.01")}) is None


@pytest.mark.parametrize("qty", ["0.02", "0.1", "1", "5", "20"])
def test_the_wire_refuses_anything_larger(monkeypatch, qty):
    monkeypatch.setenv(CAP, "0.01")
    monkeypatch.setenv(REQ, "YES")
    with pytest.raises(TP.OrderCapExceeded) as e:
        cap_check({"qty": D(qty)})
    assert "PROBE_ORDER_CAP_EXCEEDED" in str(e.value)


def test_the_wire_refuses_an_unreadable_quantity(monkeypatch):
    monkeypatch.setenv(CAP, "0.01")
    monkeypatch.setenv(REQ, "YES")
    with pytest.raises(TP.OrderCapExceeded):
        cap_check({"qty": "abc"})


def test_the_wire_fails_closed_when_the_cap_is_declared_but_absent(
        monkeypatch):
    monkeypatch.delenv(CAP, raising=False)
    monkeypatch.setenv(REQ, "YES")
    with pytest.raises(TP.OrderCapExceeded) as e:
        cap_check({"qty": D("0.01")})
    assert "PROBE_ORDER_CAP_MISSING" in str(e.value)


def test_an_unconfigured_environment_is_untouched(monkeypatch):
    """The 24/7 executor declares no cap; its wire behaviour must not change."""
    monkeypatch.delenv(CAP, raising=False)
    monkeypatch.delenv(REQ, raising=False)
    assert cap_check({"qty": D("20")}) is None
