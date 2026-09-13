"""The prospective <=0.97 cross shadow is OBSERVATIONAL, and provably so.

This instrumentation exists to make an UNKNOWN answerable, not to make it
favourable. The pre-registered retrospective run (66548a5e) produced ZERO
supported crosses over 260 real COMPLETE decisions because no row ever
reached the price test -- 56.2% stopped on a missing quote age, 18.5% on
missing depth, 25.0% on staleness. The evidence that IS available leans
AGAINST crossing (at +30s the mean complement move was about -10 cents).
Nothing here may quietly convert "we could not tell" into "it would have
worked", so the tests below are mostly about what the shadow REFUSES to
claim and what the live path REFUSES to notice.

The depth mapping itself is pinned separately and first, in
tests/test_pmus_cross_shadow_depth_direction.py.
"""
from __future__ import annotations

import ast
import builtins
import copy
import itertools
import json
import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import pmus_cross_shadow_live as xs
from src import pmus_spring_l5_session as ses

ROOT = Path(__file__).resolve().parents[1]
MODULE_SRC = Path(xs.__file__).read_text(encoding="utf-8")
SESSION_SRC = Path(ses.__file__).read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """The source of a nested session function, for structural assertions."""
    i = SESSION_SRC.index(f"def {name}(")
    j = SESSION_SRC.index("\n        def ", i + 10)
    return SESSION_SRC[i:j]


BOOK = {"slug": "aec-mlb-lad-mia-2026-09-13",
        "bid": "0.4950", "ask": "0.5900",
        "bid_qty": "5.0000", "ask_qty": "61.0000",
        "both_sides_served": True}
T0 = "2026-09-13T20:37:27+00:00"
T0_PLUS_HALF = "2026-09-13T20:37:27.500000+00:00"


def _capture(row=BOOK, *, status_utc=T0, receipt=T0_PLUS_HALF, age=0.4,
             buys_up=False, source="spring_watch"):
    return xs.observational_capture(
        row=row, book_source=source, status_utc=status_utc,
        probe_receipt_utc=receipt, authenticated_age_s=age, buys_up=buys_up)


# =====================================================================
# 1-4, 30: what the capture keeps, and what it refuses to keep
# =====================================================================
def test_1_bid_ask_and_both_quantities_survive_verbatim():
    """Unmutated: the recorder's own fixed-point strings, not floats, not
    re-quantised, not rounded. Every cent matters downstream and a silent
    coercion here would be invisible."""
    cap = _capture()
    for key in ("bid", "ask", "bid_qty", "ask_qty"):
        assert cap[key] == BOOK[key]
        assert type(cap[key]) is type(BOOK[key])
    assert cap["slug"] == BOOK["slug"]
    assert cap["both_sides_served"] is True


def test_1b_the_recorder_row_itself_is_never_mutated():
    before = copy.deepcopy(BOOK)
    _capture()
    assert BOOK == before


def test_2_both_timestamps_are_recorded_in_the_repository_convention():
    cap = _capture()
    assert cap["status_utc"] == T0
    assert cap["probe_receipt_utc"] == T0_PLUS_HALF
    # the convention is datetime.now(timezone.utc).isoformat(); the trailing
    # Z form the recorder has also emitted must parse to the same instant
    assert xs.parse_utc("2026-09-13T20:37:27Z") == xs.parse_utc(T0)
    assert xs.parse_utc("not a timestamp") is None
    assert xs.parse_utc(None) is None


def test_3_age_at_probe_receipt_adds_the_transport_delay():
    cap = _capture()
    assert cap["probe_transport_delta_s"] == pytest.approx(0.5)
    assert cap["age_at_probe_receipt_s"] == pytest.approx(0.9)   # 0.4 + 0.5


def test_3b_a_backwards_clock_can_never_make_a_quote_younger():
    """Two machines, one skewed clock. Crediting a negative delay would
    turn a stale quote fresh, which is the exact failure this field exists
    to prevent, so the delay floors at zero and the raw delta is still
    reported rather than hidden."""
    cap = _capture(status_utc=T0_PLUS_HALF, receipt=T0, age=4.9)
    assert cap["probe_transport_delta_s"] == pytest.approx(-0.5)
    assert cap["age_at_probe_receipt_s"] == pytest.approx(4.9)
    assert cap["age_at_probe_receipt_s"] >= 4.9


def test_3c_an_uncomputable_age_is_None_and_never_a_small_number():
    assert _capture(age=None)["age_at_probe_receipt_s"] is None
    assert _capture(status_utc=None)["age_at_probe_receipt_s"] is None
    assert _capture(receipt="garbage")["age_at_probe_receipt_s"] is None
    assert _capture(age="n/a")["age_at_probe_receipt_s"] is None


def test_3d_a_true_age_can_be_stale_where_the_recorder_age_was_not():
    """The point of the whole field: 4.9s at the recorder plus a 3s hop is
    7.9s at the probe, and 7.9s is not contemporaneous evidence."""
    cap = _capture(status_utc="2026-09-13T20:37:27+00:00",
                   receipt="2026-09-13T20:37:30+00:00", age=4.9)
    assert cap["age_at_probe_receipt_s"] == pytest.approx(7.9)
    assert cap["executable_depth_status"] == xs.DEPTH_STALE
    assert cap["executable_depth"] is None


def test_4_authenticated_age_s_is_copied_through_untouched():
    """The EXISTING field keeps its exact value and meaning: _authenticated_trim
    gates a live submission on it."""
    for value in (0.4, 0, 12.75, None, "4.0"):
        assert _capture(age=value)["authenticated_age_s"] == value \
            or (value is None and _capture(age=value)["authenticated_age_s"] is None)
    cap = _capture(age=0.4)
    assert cap["authenticated_age_s"] == 0.4
    assert cap["age_at_probe_receipt_s"] != cap["authenticated_age_s"]


def test_4b_the_session_never_recomputes_authenticated_age_s():
    """Structural: `authenticated_age_s` is still assigned from the recorder
    row's own field and from nothing else, and the gate still reads it."""
    body = _fn("_recorder_book")
    assert body.count('"authenticated_age_s"') == 2      # set, then passed on
    assert 'row.get(\n                       "last_authenticated_update_age_s")' \
        in body
    assert 'authenticated_age_s=out["authenticated_age_s"]' in body
    trim = _fn("_authenticated_trim")
    assert 'age = book.get("authenticated_age_s")' in trim
    assert "age_at_probe_receipt_s" not in trim, (
        "the live trim gate must keep reading the ORIGINAL age field")


def test_30_only_allowlisted_keys_are_ever_copied_off_a_recorder_row():
    """ALLOWLIST, never a denylist. A recorder that starts serving a new
    field -- or a compromised one that serves a credential -- cannot get it
    into a logged or persisted shadow row."""
    hostile = dict(BOOK)
    hostile.update({
        "api_key": "AKIA-not-a-real-key", "secret": "sh-hunter2",
        "authorization": "Bearer eyJhbGciOi", "private_key": "-----BEGIN-----",
        "password": "hunter2", "token": "t-0xdeadbeef",
        "cookie": "session=abc", "PMUS_SPRING_ARM": "s1",
        "arbitrary_future_recorder_field": {"deeply": ["nested", "junk"]}})
    blob = json.dumps(_capture(row=hostile), default=str)
    for leaked in ("AKIA", "hunter2", "Bearer", "BEGIN", "0xdeadbeef",
                   "session=abc", "arbitrary_future_recorder_field",
                   "api_key", "secret", "authorization", "private_key",
                   "password", "token", "cookie", "PMUS_SPRING_ARM"):
        assert leaked not in blob, leaked
    # and the allowlist itself carries nothing credential-shaped
    for name in xs.CAPTURE_ALLOWLIST:
        assert not re.search(r"key|secret|token|auth|pass|cred|cookie|arm",
                             name, re.I), name


def test_30b_the_evaluation_row_is_also_allowlist_bound():
    hostile = dict(BOOK, secret="sh-hunter2")
    out = xs.evaluate(book=_capture(row=hostile), buys_up=False,
                      intended_qty=D("0.02"), held_avg_cost=D("0.43"),
                      held_residual=D("0.02"), model_relation="complete",
                      maker_price=D("0.54"))
    assert "hunter2" not in json.dumps(out, default=str)


def test_the_shadow_rows_are_json_serialisable():
    """A row that cannot be serialised is a row that breaks the log line it
    rides on, which is a production effect. Pinned for both rows."""
    json.dumps(_capture())
    json.dumps(xs.evaluate(book=_capture(), buys_up=False,
                           intended_qty=D("0.02"), held_avg_cost=D("0.43"),
                           held_residual=D("0.02"), model_relation="complete",
                           maker_price=D("0.54")))


# =====================================================================
# 5-7, 23, 24: evidence fails closed and never becomes a fill
# =====================================================================
def test_5_a_stale_quote_stays_STALE():
    depth, status = xs.executable_depth(BOOK, buys_up=False, quote_age_s=5.1)
    assert (depth, status) == (None, xs.DEPTH_STALE)
    assert xs.executable_depth(BOOK, buys_up=False,
                               quote_age_s=5.0)[1] == xs.DEPTH_OBSERVED


def test_6_missing_depth_stays_UNKNOWN():
    for book in ({k: v for k, v in BOOK.items() if k != "bid_qty"},
                 dict(BOOK, bid_qty=None)):
        depth, status = xs.executable_depth(book, buys_up=False,
                                            quote_age_s=1.0)
        assert (depth, status) == (None, xs.DEPTH_UNKNOWN_NOT_RECORDED)
    assert xs.executable_depth(None, buys_up=False, quote_age_s=1.0) == \
        (None, xs.DEPTH_UNKNOWN_NO_BOOK)


@pytest.mark.parametrize("bad", ["", "  ", "abc", "NaN", "Infinity", "-1",
                                 "1e", [], {}, object()])
def test_7_malformed_depth_is_UNKNOWN_and_refused(bad):
    depth, status = xs.executable_depth(dict(BOOK, bid_qty=bad),
                                        buys_up=False, quote_age_s=1.0)
    assert depth is None
    assert status in (xs.DEPTH_UNKNOWN_MALFORMED, xs.DEPTH_UNKNOWN_NOT_RECORDED)
    out = xs.evaluate(book=dict(BOOK, bid_qty=bad), buys_up=False,
                      intended_qty=D("0.02"), held_avg_cost=D("0.43"),
                      held_residual=D("0.02"), model_relation="complete",
                      quote_age_s=1.0, maker_price=D("0.54"))
    assert out["verdict"] == xs.UNKNOWN_EXEC
    assert out["would_submit"] is False


def test_a_quote_with_no_age_at_all_cannot_produce_executable_depth():
    """56.2% of the pre-registered corpus stopped exactly here."""
    assert xs.executable_depth(BOOK, buys_up=False, quote_age_s=None) == \
        (None, xs.DEPTH_UNKNOWN_QUOTE_AGE)


def test_an_observed_zero_depth_is_a_real_observation_not_missing_evidence():
    """"The far side was empty" is a finding. It must fail the SIZE test
    honestly rather than hide inside UNKNOWN -- and it is still never a fill."""
    depth, status = xs.executable_depth(dict(BOOK, bid_qty="0"),
                                        buys_up=False, quote_age_s=1.0)
    assert (depth, status) == (D("0"), xs.DEPTH_OBSERVED)
    out = xs.evaluate(book=dict(BOOK, bid_qty="0"), buys_up=False,
                      intended_qty=D("0.02"), held_avg_cost=D("0.40"),
                      held_residual=D("0.02"), model_relation="complete",
                      quote_age_s=1.0, maker_price=D("0.50"))
    assert out["verdict"] == xs.REFUSED
    assert out["first_stop"].startswith("depth_0_below_intended")


@pytest.mark.parametrize("status", list(xs.DEPTH_NOT_EXECUTABLE))
def test_23_no_non_observed_status_can_ever_become_a_hypothetical_fill(status):
    """Every non-OBSERVED status carries depth None, and None can never
    satisfy the size test -- 0 and infinity are not substitutes for unknown."""
    assert status != xs.DEPTH_OBSERVED
    books = {
        xs.DEPTH_UNKNOWN_NO_BOOK: None,
        xs.DEPTH_UNKNOWN_NOT_RECORDED: {k: v for k, v in BOOK.items()
                                        if k != "bid_qty"},
        xs.DEPTH_UNKNOWN_MALFORMED: dict(BOOK, bid_qty="abc"),
        xs.DEPTH_UNKNOWN_QUOTE_AGE: BOOK,
        xs.DEPTH_STALE: BOOK,
    }
    ages = {xs.DEPTH_UNKNOWN_QUOTE_AGE: None, xs.DEPTH_STALE: 99.0}
    depth, got = xs.executable_depth(books[status], buys_up=False,
                                     quote_age_s=ages.get(status, 1.0))
    assert got == status and depth is None
    out = xs.evaluate(book=books[status], buys_up=False,
                      intended_qty=D("0.02"), held_avg_cost=D("0.40"),
                      held_residual=D("0.02"), model_relation="complete",
                      quote_age_s=ages.get(status, 1.0), maker_price=D("0.50"))
    assert out["verdict"] != xs.SUPPORTED
    assert out["would_submit"] is False


def test_24_the_module_is_stateless_so_an_old_depth_cannot_be_reused():
    """No caches, no globals written at call time: a missing observation can
    never be served by a previous market's number."""
    tree = ast.parse(MODULE_SRC)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Global)]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Nonlocal)]
    mutable = {k: v for k, v in vars(xs).items()
               if not k.startswith("__") and isinstance(v, (list, set))}
    assert not mutable, mutable
    # functional: a rich observation followed by nothing yields nothing
    good = xs.executable_depth(BOOK, buys_up=False, quote_age_s=1.0)
    assert good == (D("5.0000"), xs.DEPTH_OBSERVED)
    assert xs.executable_depth(None, buys_up=False, quote_age_s=1.0) == \
        (None, xs.DEPTH_UNKNOWN_NO_BOOK)
    assert xs.executable_depth({"slug": "x"}, buys_up=False,
                               quote_age_s=1.0) == \
        (None, xs.DEPTH_UNKNOWN_NOT_RECORDED)


def test_24b_the_session_shadow_reads_only_this_orders_own_book():
    body = _fn("_cross_shadow")
    assert 'book = row.get("recorder_book")' in body
    for forbidden in ("_recorder_book", "load_status_document", "t.request",
                      "outcome_bbo", "_complete_book_state", "_control_state"):
        assert forbidden not in body, forbidden


# =====================================================================
# 16, 28, 29: shadow only, by construction
# =====================================================================
def test_16_every_result_reports_would_submit_false():
    """On every path, including the SUPPORTED one."""
    assert xs.WOULD_SUBMIT is False
    seen = set()
    cases = [
        dict(book=None, quote_age_s=1.0),
        dict(book=BOOK, model_relation="open", quote_age_s=1.0),
        dict(book=BOOK, held_residual=D("0"), quote_age_s=1.0),
        dict(book=BOOK, quote_age_s=None),
        dict(book=BOOK, quote_age_s=99.0),
        dict(book=dict(BOOK, bid_qty=None), quote_age_s=1.0),
        dict(book=dict(BOOK, bid_qty="0.0001"), quote_age_s=1.0),
        dict(book=BOOK, quote_age_s=1.0),                  # pair 0.93 -> ok
        dict(book=BOOK, held_avg_cost=D("0.60"), quote_age_s=1.0),
    ]
    for case in cases:
        kwargs = dict(buys_up=False, intended_qty=D("0.02"),
                      held_avg_cost=D("0.42"), held_residual=D("0.02"),
                      model_relation="complete", maker_price=D("0.50"))
        kwargs.update(case)
        out = xs.evaluate(**kwargs)
        assert out["would_submit"] is False
        assert out["shadow_only"] is True
        assert out["provenance"] == xs.PROVENANCE
        assert out["provenance"].startswith("SHADOW_ONLY")
        seen.add(out["verdict"])
    assert seen == {xs.SUPPORTED, xs.REFUSED, xs.UNKNOWN_EXEC}, seen


def test_28_the_module_imports_nothing_that_could_submit_anything():
    """Not "it does not submit" -- it CANNOT. No src import, no transport,
    no client, no socket, and nothing importable at call time."""
    tree = ast.parse(MODULE_SRC)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots <= {"__future__", "datetime", "decimal", "typing"}, roots
    assert "src" not in roots


def test_28b_no_import_is_deferred_into_a_function_body():
    """A lazy import inside a function would be a submission path hidden
    from the check above."""
    tree = ast.parse(MODULE_SRC)
    top = {id(n) for n in tree.body}
    hidden = [n for n in ast.walk(tree)
              if isinstance(n, (ast.Import, ast.ImportFrom))
              and id(n) not in top]
    assert not hidden, [ast.dump(n) for n in hidden]


def test_28c_no_call_or_attribute_in_the_module_can_reach_a_venue():
    names = set()
    for node in ast.walk(ast.parse(MODULE_SRC)):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    forbidden = {"request", "urlopen", "socket", "place_limit_buy", "Transport",
                 "outcome_bbo", "cancel_all_deadman", "ledger_append",
                 "httpx", "requests", "urllib", "connect", "sendall",
                 "post", "put", "delete", "patch", "open", "exec", "eval",
                 "__import__", "getattr", "setattr", "globals", "locals"}
    assert not (names & forbidden), names & forbidden


def test_28d_evaluating_imports_nothing_at_call_time():
    """Runtime proof to go with the static one: with every import made fatal,
    a full evaluation still completes."""
    real = builtins.__import__

    def boom(name, *a, **k):
        raise AssertionError(f"the shadow tried to import {name}")
    builtins.__import__ = boom
    try:
        out = xs.evaluate(book=BOOK, buys_up=False, intended_qty=D("0.02"),
                          held_avg_cost=D("0.42"), held_residual=D("0.02"),
                          model_relation="complete", quote_age_s=1.0,
                          maker_price=D("0.50"))
    finally:
        builtins.__import__ = real
    assert out["verdict"] == xs.SUPPORTED and out["would_submit"] is False


def test_28e_no_function_here_is_named_for_a_mutation():
    funcs = {n.name for n in ast.walk(ast.parse(MODULE_SRC))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in funcs:
        assert not re.search(
            r"submit|place|cancel|replace|reprice|order|post|send|write|"
            r"save|persist|arm|execute", name, re.I), name


def test_28f_the_live_probe_image_carries_it_and_watches_it():
    """A boot-scope import absent from the image does not degrade: the probe
    fails to import and never starts."""
    dockerfile = (ROOT / "deploy/pmus_spring_l5/Dockerfile").read_text(
        encoding="utf-8")
    assert "COPY src/pmus_cross_shadow_live.py " in dockerfile
    watched = json.loads((ROOT / "deploy/pmus_spring_l5/railway.json")
                         .read_text(encoding="utf-8"))["build"]["watchPatterns"]
    assert "src/pmus_cross_shadow_live.py" in watched


def test_29_a_serialisation_hostile_shadow_row_cannot_reach_production_code():
    """The session writes the row and moves on; nothing downstream of the
    write is allowed to depend on it being serialisable, and the write itself
    sits inside an exception boundary (pinned in test_12/13/14 below)."""
    body = _fn("_cross_shadow")
    assert body.count("row[") == 1
    written = set(re.findall(r'row\["([a-z_]+)"\]\s*=', body))
    assert written == {"cross_shadow_live"}, written
    assert "-> None:" in body


# =====================================================================
# 17: the prospective arms
# =====================================================================
def test_17_point98_point99_and_1_00_are_absent_as_prospective_arms():
    """Only 0.97 is carried. Break-even after the confirmed taker fee AND the
    forgone maker rebate is ~0.977-0.988, so .99/1.00 are economically dead
    and .98 is too marginal to promote."""
    assert xs.PROSPECTIVE_THRESHOLDS == (D("0.97"),)
    assert xs.PROSPECTIVE_THRESHOLD == D("0.97")
    for dead in (D("0.98"), D("0.99"), D("1.00")):
        assert dead not in xs.PROSPECTIVE_THRESHOLDS
    out = xs.evaluate(book=BOOK, buys_up=False, intended_qty=D("0.02"),
                      held_avg_cost=D("0.42"), held_residual=D("0.02"),
                      model_relation="complete", quote_age_s=1.0,
                      maker_price=D("0.50"))
    assert out["prospective_arms"] == ["0.97"]
    assert set(out["excluded_prospective_arms"]) == {"0.98", "0.99", "1.00"}
    assert out["prospective_pair_cost_max"] == "0.97"


def test_17b_a_pair_between_0_97_and_1_00_is_refused_not_promoted():
    """The window the dead arms would have opened, closed explicitly."""
    for avg in ("0.3850", "0.3950", "0.4050"):       # pairs .975/.985/.995
        out = xs.evaluate(book=BOOK, buys_up=True, intended_qty=D("1"),
                          held_avg_cost=D(avg), held_residual=D("-1"),
                          model_relation="complete", quote_age_s=1.0,
                          maker_price=D("0.58"))
        pair = D(out["economics"]["pair_cost"])
        assert D("0.97") < pair < D("1.00")
        assert out["verdict"] == xs.REFUSED
        assert out["first_stop"].startswith(f"pair_{pair}_over_prospective")


def test_17c_the_live_1_00_pair_ceiling_is_recorded_as_unchanged():
    assert xs.LIVE_PAIR_CEILING == D("1.00")
    assert ses.completion_pair_ceiling() <= D("1.00")
    out = xs.evaluate(book=BOOK, buys_up=False, intended_qty=D("0.02"),
                      held_avg_cost=D("0.42"), held_residual=D("0.02"),
                      model_relation="complete", quote_age_s=1.0)
    assert out["live_pair_ceiling_unchanged"] == "1.00"


def test_17d_the_frozen_retrospective_arms_are_untouched_and_separate():
    """The pre-registered module keeps its four arms because its spec was
    frozen before any outcome was seen. This one is a different experiment."""
    from src import l5_cross_shadow as frozen
    assert frozen.THRESHOLDS == (D("0.97"), D("0.98"), D("0.99"), D("1.00"))
    assert xs.PROSPECTIVE_THRESHOLDS != frozen.THRESHOLDS


# =====================================================================
# 18-22: the shadow's own gates
# =====================================================================
def _ok(**over):
    kwargs = dict(book=BOOK, buys_up=False, intended_qty=D("0.02"),
                  held_avg_cost=D("0.42"), held_residual=D("0.02"),
                  model_relation="complete", quote_age_s=1.0,
                  maker_price=D("0.50"))
    kwargs.update(over)
    return xs.evaluate(**kwargs)


def test_18_the_quote_must_be_no_older_than_five_seconds_at_the_probe():
    assert xs.MAX_QUOTE_AGE_S == 5.0
    assert _ok(quote_age_s=5.0)["verdict"] == xs.SUPPORTED
    late = _ok(quote_age_s=5.01)
    assert late["verdict"] == xs.REFUSED
    assert "over_5.0s" in late["first_stop"]
    assert _ok(quote_age_s=None)["first_stop"] == "no_authenticated_quote_age"
    assert _ok(quote_age_s=None)["verdict"] == xs.UNKNOWN_EXEC


def test_18b_the_age_the_gate_uses_is_the_age_AT_THE_PROBE():
    """Derived from the capture when not passed explicitly, so a quote that
    was fresh at the recorder and stale on arrival is refused."""
    out = xs.evaluate(book=BOOK, buys_up=False, intended_qty=D("0.02"),
                      held_avg_cost=D("0.42"), held_residual=D("0.02"),
                      model_relation="complete", maker_price=D("0.50"),
                      authenticated_age_s=4.9, status_utc=T0,
                      probe_receipt_utc="2026-09-13T20:37:30+00:00")
    assert out["quote_age_s"] == pytest.approx(7.9)
    assert out["verdict"] == xs.REFUSED and "over_5.0s" in out["first_stop"]


def test_19_observed_depth_must_cover_the_intended_quantity():
    assert _ok(intended_qty=D("5.0000"))["verdict"] == xs.SUPPORTED
    short = _ok(intended_qty=D("5.0001"))
    assert short["verdict"] == xs.REFUSED
    assert short["first_stop"] == "depth_5.0000_below_intended_5.0001"


def test_20_and_21_both_confirmed_costs_are_charged():
    out = _ok()
    econ = out["economics"]
    assert econ["fee_status"] == "CONFIRMED"
    assert "docs.polymarket.us/fees" in econ["fee_source"]
    assert D(econ["confirmed_taker_fee"]) == xs.taker_fee(D("0.02"), D("0.5050"))
    assert D(econ["maker_rebate_forgone"]) == xs.maker_rebate(D("0.02"),
                                                              D("0.50"))
    # net is gross MINUS both, never gross alone
    assert D(econ["net_locked_pair_pnl"]) == (
        D(econ["gross_locked_pair_pnl"]) - D(econ["confirmed_taker_fee"])
        - D(econ["maker_rebate_forgone"]))


def test_21b_the_forgone_rebate_is_a_real_cost_that_can_flip_the_answer():
    """Charging only the taker fee would promote a cross that loses money.
    Here the fee alone leaves it positive and the rebate we give up by
    crossing sinks it -- which is precisely why the .98/.99/1.00 arms are
    dead and are not carried."""
    econ = xs.cross_economics(contracts=D("40"), taker_price=D("0.5050"),
                              held_avg_cost=D("0.4785"), maker_price=D("0.50"))
    gross, fee = D(econ["gross_locked_pair_pnl"]), D(econ["confirmed_taker_fee"])
    forgone = D(econ["maker_rebate_forgone"])
    assert econ["pair_cost"] == "0.9835"           # inside a dead arm, not ours
    assert gross - fee > 0, "fee alone leaves it positive"
    assert gross - fee - forgone < 0, "the forgone rebate sinks it"
    assert econ["net_positive_after_confirmed_costs"] is False
    # and dropping the rebate from the model would have flipped the verdict
    without = xs.cross_economics(contracts=D("40"), taker_price=D("0.5050"),
                                 held_avg_cost=D("0.4785"), maker_price=None)
    assert without["maker_rebate_forgone"] == "0"
    assert without["net_positive_after_confirmed_costs"] is True


def test_22_the_net_after_both_costs_gate_refuses_a_real_0_97_row():
    """A pair AT the 0.97 arm whose confirmed costs eat the whole edge once
    the venue's banker's rounding is applied at the real clip. The pair test
    passes and the NET test is what stops it."""
    out = _ok(book=dict(BOOK, bid="0.5000"), intended_qty=D("0.33334"),
              held_avg_cost=D("0.47"), maker_price=D("0.50"))
    assert out["economics"]["pair_cost"] == "0.9700"
    assert D(out["economics"]["pair_cost"]) <= xs.PROSPECTIVE_THRESHOLD
    assert out["economics"]["net_locked_pair_pnl"] == "0.0000"
    assert out["economics"]["net_positive_after_confirmed_costs"] is False
    assert out["verdict"] == xs.REFUSED
    assert out["first_stop"] == "net_not_positive_after_confirmed_costs"
    assert out["would_submit"] is False


def test_22a_a_net_that_rounds_away_is_not_a_gain():
    """The reported net and the flag are read off the same number, so a row
    can be checked by hand. An unrounded hair above zero that displays as
    0.0000 must not be promoted."""
    econ = xs.cross_economics(contracts=D("0.33334"), taker_price=D("0.50"),
                              held_avg_cost=D("0.47"), maker_price=D("0.50"))
    assert econ["net_locked_pair_pnl"] == "0.0000"
    assert econ["net_positive_after_confirmed_costs"] is False


def test_22d_at_the_0_97_arm_the_ceiling_does_almost_all_the_work():
    """An honest statement of what the net gate is worth here: across a grid
    of clips and prices at a pair of exactly 0.97, the confirmed costs are
    below the gross edge except in a razor-thin rounding band. The 0.97
    ceiling nearly implies net-positive on its own -- so the net gate is a
    backstop, and no report may present it as the thing doing the filtering."""
    binding = []
    for q in ("0.01", "0.02", "0.1", "0.3333", "0.33334", "0.5", "1", "1.72",
              "2", "5", "40", "1000"):
        for p in ("0.05", "0.2", "0.4", "0.5", "0.505", "0.7", "0.9"):
            avg = D("0.97") - D(p)
            if avg <= 0:
                continue
            econ = xs.cross_economics(contracts=D(q), taker_price=D(p),
                                      held_avg_cost=avg, maker_price=D(p))
            if not econ["net_positive_after_confirmed_costs"]:
                binding.append((q, p, econ["net_locked_pair_pnl"]))
    assert binding == [("0.33334", "0.5", "0.0000")], binding


def test_22b_the_fee_math_agrees_with_the_frozen_pre_registered_module():
    """Duplicated constants, pinned against drift on a grid."""
    from src import l5_cross_shadow as frozen
    for q in ("0.02", "1", "1.72", "40", "1000"):
        for p in ("0.05", "0.25", "0.50", "0.5050", "0.75", "0.95"):
            assert xs.taker_fee(q, p) == frozen.taker_fee(D(q), D(p))
            assert xs.maker_rebate(q, p) == frozen.maker_rebate(D(q), D(p))
    assert (xs.TAKER_THETA, xs.MAKER_THETA) == (frozen.TAKER_THETA,
                                                frozen.MAKER_THETA)
    assert (xs.TAKER_MAX, xs.MAKER_REBATE_MAX) == (frozen.TAKER_MAX,
                                                   frozen.MAKER_REBATE_MAX)


def test_22c_the_representative_clip_economics_are_reproduced():
    """The numbers the 0.97-only decision rests on: ~1.72 contracts at
    p~=0.50, a 0.97 pair -- gross ~$0.0516, taker ~$0.03, forgone ~$0.01."""
    econ = xs.cross_economics(contracts=D("1.72"), taker_price=D("0.50"),
                              held_avg_cost=D("0.47"), maker_price=D("0.50"))
    assert econ["pair_cost"] == "0.9700"
    assert econ["gross_locked_pair_pnl"] == "0.0516"
    assert econ["confirmed_taker_fee"] == "0.03"
    assert econ["maker_rebate_forgone"] == "0.01"
    assert econ["net_locked_pair_pnl"] == "0.0116"
    assert econ["net_positive_after_confirmed_costs"] is True
    assert econ["theoretical"] is True


def test_the_gates_report_the_FIRST_thing_that_stopped_the_row():
    """So a corpus reads the way the pre-registered one did: "could not
    tell" and "would not have" stay different facts."""
    stale_and_shallow = _ok(quote_age_s=99.0, intended_qty=D("9999"))
    assert stale_and_shallow["first_stop"].startswith("quote_age_")
    assert _ok(model_relation="extend")["first_stop"] == \
        "model_did_not_select_complete"
    assert _ok(held_residual=D("0"))["first_stop"] == "no_held_residual"
    assert _ok(risk_gates_pass=False)["first_stop"] == \
        "existing_risk_spend_or_daily_loss_gate"


# =====================================================================
# 12-15, 25-27: the live path does not notice any of this
# =====================================================================
def _complete_session(monkeypatch, tmp_path, *, status, fake_kwargs=None,
                      counter=None, held_up=True):
    """One armed session that RESUMES a held residual and completes it.

    held_up=True  -> holding UP   -> COMPLETE buys DOWN -> depth is bid_qty
    held_up=False -> holding DOWN -> COMPLETE buys UP   -> depth is ask_qty
    """
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                       _ledger_with_fill)
    from tests.test_pmus_spring_l5_session import (Fake, _ticking,
                                                   _script_proposals)
    from src import pmus_spring_l5_probe_entry as entry
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held",          # +/-0.02 @ 0.43
                      side="OUTCOME_SIDE_YES" if held_up else "OUTCOME_SIDE_NO")
    _script_proposals(monkeypatch, ["complete"])
    monkeypatch.setenv(ses.MAX_ACTIONS_ENV, "1")

    def load(path=None):
        if counter is not None:
            counter.append(path)
        return (copy.deepcopy(status), "status_path:/test")
    monkeypatch.setattr(entry, "load_status_document", load)
    fake = Fake(**(fake_kwargs or {"bid": "0.4300", "ask": "0.4350"}))
    fake.positions["held"] = {"netPositionDecimal":
                              "0.02" if held_up else "-0.02"}
    return ses.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)


def _status(now_iso=None, **row):
    from datetime import datetime, timezone
    base = {"slug": "held", "bid": "0.4500", "ask": "0.4550",
            "bid_qty": "5.0000", "ask_qty": "61.0000",
            "both_sides_served": True,
            "last_authenticated_update_age_s": 0.4}
    base.update(row)
    return {"t": now_iso or datetime.now(timezone.utc).isoformat(),
            "spring_watch": [base]}


def test_the_shadow_row_reaches_a_real_complete_order(monkeypatch, tmp_path):
    rep = _complete_session(monkeypatch, tmp_path, status=_status())
    order = [o for o in rep["orders"] if o["relation"] == "complete"][0]
    shadow = order["cross_shadow_live"]
    assert shadow["would_submit"] is False
    assert shadow["shadow_only"] is True
    # holding UP -> buys DOWN -> bid_qty, and the price is 1 - the YES bid
    assert shadow["order_buys"] == "DOWN"
    assert shadow["executable_depth_field"] == "bid_qty"
    assert shadow["executable_depth"] == "5.0000"
    assert shadow["executable_complement_price"] == "0.5500"
    assert shadow["book_source"] == "spring_watch"
    obs = order["recorder_book"]["cross_shadow_observation"]
    assert obs["bid_qty"] == "5.0000" and obs["ask_qty"] == "61.0000"
    assert obs["authenticated_age_s"] == 0.4
    assert obs["age_at_probe_receipt_s"] >= 0.4
    assert obs["would_submit"] is False
    json.dumps(rep, default=str)


def test_a_complete_with_no_captured_book_is_UNKNOWN_not_assumed(
        monkeypatch, tmp_path):
    """The existing throttles are unchanged, so most completes carry no book.
    That must read as UNKNOWN -- never as a fill, and never filled in from an
    earlier market's observation."""
    rep = _complete_session(monkeypatch, tmp_path,
                            status={"t": "2026-09-13T20:37:27+00:00",
                                    "spring_watch": []})
    order = [o for o in rep["orders"] if o["relation"] == "complete"][0]
    assert order["recorder_book"]["unreadable"] == "slug_not_in_status_document"
    shadow = order["cross_shadow_live"]
    assert shadow["verdict"] == xs.UNKNOWN_EXEC
    assert shadow["first_stop"] == "no_authenticated_book"
    assert shadow["executable_depth"] is None
    assert shadow["would_submit"] is False


@pytest.mark.parametrize("victim", ["evaluate", "observational_capture"])
def test_12_13_14_a_raising_shadow_changes_nothing_at_all(monkeypatch,
                                                          tmp_path, victim):
    """12: it cannot change the model-selected action.
    13: it cannot prevent a valid real order.
    14: it cannot cancel, replace or reprice one.
    15: the existing rows stay behaviourally identical.

    Proved by running the SAME session twice -- once normally, once with the
    instrumentation detonating -- and diffing everything but the new fields."""
    status = _status()          # ONE fixture, so the two runs differ only
                                # in whether the instrumentation detonates

    def _run(broken):
        def boom(*a, **k):
            raise RuntimeError("instrumentation exploded")
        mp = pytest.MonkeyPatch()
        try:
            if broken:
                mp.setattr(xs, victim, boom)
            with mp.context() as inner:
                return _complete_session(inner, tmp_path, status=status)
        finally:
            mp.undo()

    good, broken = _run(False), _run(True)

    # Two independent sessions differ in a few fields BY DESIGN (a fresh
    # session id, a uuid-derived decision id, a wall-clock stamp). Rather
    # than trusting a strip list, the diff is COMPUTED and required to be a
    # subset of exactly those plus the two new observational keys -- so any
    # other difference, anywhere, fails this test.
    # session id, uuid-derived decision id, and the wall-clock stamps
    # (`t_utc` on a decision, `t` on an Inventory action, `observed_at` on
    # the fill reconciliation)
    PER_SESSION = {"session_id", "decision_id", "t_utc", "t", "observed_at"}
    NEW_FIELDS = {"cross_shadow_live", "cross_shadow_observation"}

    def _diff_keys(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            out = set()
            for key in set(a) | set(b):
                if key in NEW_FIELDS:
                    # the whole subtree is new; name the key, do not descend
                    if a.get(key) != b.get(key):
                        out.add(key)
                elif key not in a or key not in b:
                    out.add(key)
                else:
                    out |= _diff_keys(a[key], b[key], f"{path}.{key}")
            return out
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return {f"{path}:length"}
            out = set()
            for i, (x, y) in enumerate(zip(a, b)):
                out |= _diff_keys(x, y, f"{path}[{i}]")
            return out
        return set() if a == b else {path.rsplit(".", 1)[-1] or path}

    assert good["status"] == broken["status"] == "SPRING_MATCHED"
    for section in ("orders", "decisions", "actions", "inventory",
                    "pair_economics", "fill_reconciliation", "flat_verified",
                    "pair_unit_cost", "combined_loss_usd",
                    "risk_reduction_only"):
        differ = _diff_keys(good.get(section), broken.get(section))
        assert differ <= PER_SESSION | NEW_FIELDS, (section, differ)
    # and the difference that DOES exist is only ever the new field
    assert _diff_keys(good["orders"], broken["orders"]) <= NEW_FIELDS
    # 13: the real order still happened, at the same price and size
    order = [o for o in broken["orders"] if o["relation"] == "complete"][0]
    assert order["filled_qty"] == "0.02" and order["outcome"] == "filled"
    # 14: nothing extra was cancelled or replaced -- one complete, one order
    assert len([o for o in broken["orders"] if o["relation"] == "complete"]) \
        == 1
    # 12: and the broken run still carries a fail-soft marker, not silence
    if victim == "observational_capture":
        assert "unreadable" in order["recorder_book"][
            "cross_shadow_observation"]
    else:
        assert "cross_shadow_live" not in order


def test_25_the_shadow_adds_no_recorder_read(monkeypatch, tmp_path):
    """Same session, shadow working vs shadow detonating: the number of
    status-document loads must be identical."""
    def _count(broken):
        seen: list = []
        mp = pytest.MonkeyPatch()
        try:
            if broken:
                mp.setattr(xs, "evaluate",
                           lambda *a, **k: (_ for _ in ()).throw(
                               RuntimeError("boom")))
            with mp.context() as inner:
                _complete_session(inner, tmp_path, status=_status(),
                                  counter=seen)
        finally:
            mp.undo()
        return len(seen)

    assert _count(False) == _count(True)
    assert _count(False) >= 1, "precondition: the existing path does read it"


def test_25b_the_shadow_call_site_is_post_hoc_and_guarded():
    call = SESSION_SRC[SESSION_SRC.index(
        "_control_book(side_up_, px_, report"):][:1400]
    assert 'if relation_ == "complete":' in call
    assert '_cross_shadow(side_up_, px_, qty_,' in call
    # its own boundary, and it runs AFTER the existing capture
    assert call.index("_complete_book(") < call.index("_cross_shadow(")
    assert call.count("except Exception:") >= 2
    # and the whole block still sits in the non-rejected harvest branch
    i = SESSION_SRC.index('if not end_reason.startswith("rejected"):')
    assert "_cross_shadow(" in SESSION_SRC[i:i + 1600]


def test_26_and_27_the_recorder_throttles_are_unchanged():
    assert ses.COMPLETE_BOOK_MAX_PER_SESSION == 8
    assert ses.COMPLETE_BOOK_MIN_GAP_S == 2.0
    assert ses.CONTROL_BOOK_MAX_PER_SESSION == 24
    assert ses.CONTROL_BOOK_MIN_GAP_S == 5.0
    body = _fn("_complete_book")
    assert "COMPLETE_BOOK_MAX_PER_SESSION" in body
    assert "COMPLETE_BOOK_MIN_GAP_S" in body
    # the shadow does not participate in, relax, or reset any of them.
    # Asserted against the CODE: the docstring names them on purpose.
    code = _fn("_cross_shadow").split('"""')[2]
    assert "COMPLETE_BOOK" not in code
    assert "CONTROL_BOOK" not in code
    assert "_complete_book_state" not in code and "_control_state" not in code


def test_the_risk_envelope_did_not_move():
    """Every number this change is forbidden to touch, asserted in one place."""
    assert ses.MAX_RESIDUAL_UNITS == 3
    assert ses.SPRING_BAND_LO == D("0.25") and ses.SPRING_BAND_HI == D("0.75")
    assert ses.COMPLETE_REJECT_STREAK_MAX == 3
    assert ses.DEFAULT_MAX_COMPLETE_ACTIONS == 12
    assert ses.DEFAULT_COMPLETE_REQUOTE is False
    assert ses.completion_pair_ceiling() <= D("1.00")
    assert ses.AUTH_TRIM_MAX_AGE_S == 10.0
    assert ses.AUTH_TRIM_SLOW_MS == 750.0
    assert ses.SPORTS_MAKER_REST_MAX_S == 30.0


def test_no_taker_path_was_introduced_into_the_session():
    """Maker-only live execution. Every real submission in the session is
    still post_only=True."""
    for call in re.findall(r"micro\.place_limit_buy\((?:[^()]|\([^()]*\))*\)",
                           SESSION_SRC):
        assert "post_only=True" in call, call
    assert "post_only=False" not in SESSION_SRC


def test_the_session_only_ever_writes_observational_keys_for_this_change():
    """The exact new surface in the live module: one key on the order row,
    one key on the recorder book. Nothing else."""
    added = set(re.findall(r'(?:row|out)\["(cross_shadow[a-z_]*)"\]',
                           SESSION_SRC))
    assert added == {"cross_shadow_live", "cross_shadow_observation"}, added


# =====================================================================
# what the evaluator is allowed to see, and what a malformed age does
# =====================================================================
def test_the_evaluator_only_ever_sees_allowlisted_observation_fields():
    """The session hands `evaluate` the CAPTURE, not the raw recorder row,
    so a field that never passed the allowlist cannot reach the evaluation
    either."""
    body = _fn("_cross_shadow")
    assert 'book.get("cross_shadow_observation")' in body
    assert "book=(obs if isinstance(obs, Mapping) else None)" in body
    # everything the evaluator reads off a book is on the allowlist
    for field in ("bid", "ask", "bid_qty", "ask_qty"):
        assert field in xs.CAPTURE_ALLOWLIST


def test_a_fail_soft_capture_evaluates_to_UNKNOWN_not_to_a_fill():
    """If the capture itself broke, the recorder book carries the
    `unreadable` marker; the evaluator must treat that as no evidence."""
    out = xs.evaluate(book={"unreadable": "ValueError:boom",
                            "would_submit": False},
                      buys_up=False, intended_qty=D("0.02"),
                      held_avg_cost=D("0.43"), held_residual=D("0.02"),
                      model_relation="complete")
    assert out["verdict"] == xs.UNKNOWN_EXEC
    assert out["first_stop"] == "no_authenticated_quote_age"
    assert out["executable_depth"] is None
    assert out["would_submit"] is False


@pytest.mark.parametrize("bad", ["soon", float("nan"), -1.0, [], True])
def test_a_malformed_quote_age_is_unknown_and_never_raises(bad):
    """A shadow that raises is caught by the session, but a shadow that
    silently treats junk as freshness would be worse -- it would promote a
    cross on evidence that does not exist."""
    assert xs.executable_depth(BOOK, buys_up=False, quote_age_s=bad)[1] == \
        xs.DEPTH_UNKNOWN_QUOTE_AGE
    out = _ok(quote_age_s=bad)
    assert out["verdict"] == xs.UNKNOWN_EXEC
    assert out["first_stop"] == "no_authenticated_quote_age"
    assert out["quote_age_s"] is None


def test_evaluate_exposes_no_threshold_parameter_at_all():
    """Requirement 17 made structural: there is no argument through which a
    0.98/0.99/1.00 cross could be evaluated, now or later."""
    import inspect
    params = inspect.signature(xs.evaluate).parameters
    for name in params:
        assert not re.search(r"threshold|ceiling|pair_max|arm", name, re.I), name
    assert "threshold" not in params
    with pytest.raises(TypeError):
        xs.evaluate(book=BOOK, buys_up=False, intended_qty=D("1"),
                    held_avg_cost=D("0.01"), held_residual=D("1"),
                    model_relation="complete", threshold=D("1.00"))


# =====================================================================
# 10/11 again, END TO END: the mapping the LIVE session actually uses
# =====================================================================
@pytest.mark.parametrize("held_up,side,field,depth,price", [
    (True, "NO", "bid_qty", "5.0000", "0.5500"),     # holding UP -> buys DOWN
    (False, "YES", "ask_qty", "61.0000", "0.4550"),  # holding DOWN -> buys UP
])
def test_10_11_live_the_mapping_follows_the_ORDER_side_both_ways(
        monkeypatch, tmp_path, held_up, side, field, depth, price):
    """Pinned at module level in the depth-direction file and pinned AGAIN
    here through the real session, because a correct module wired backwards
    at the call site would look exactly like a correct system."""
    rep = _complete_session(monkeypatch, tmp_path, status=_status(),
                            held_up=held_up)
    order = [o for o in rep["orders"] if o["relation"] == "complete"][0]
    assert order["side"] == side, "precondition: COMPLETE buys the deficit side"
    shadow = order["cross_shadow_live"]
    assert shadow["order_buys"] == ("UP" if side == "YES" else "DOWN")
    assert shadow["executable_depth_field"] == field
    assert shadow["executable_depth"] == depth
    # the price comes off the SAME side of the book as the depth
    assert shadow["executable_price_field"] == field.split("_")[0]
    assert shadow["executable_complement_price"] == price
    assert shadow["would_submit"] is False


def test_the_shadows_own_side_derivation_agrees_with_the_real_order(
        monkeypatch, tmp_path):
    """The cross-check that catches a backwards wiring: the side the module
    derives from the residual must be the side the venue order actually
    bought. Asserted in both directions."""
    for held_up, expect_side in ((True, "NO"), (False, "YES")):
        rep = _complete_session(monkeypatch, tmp_path, status=_status(),
                                held_up=held_up)
        order = [o for o in rep["orders"] if o["relation"] == "complete"][0]
        residual_before = D("0.02") if held_up else D("-0.02")
        derived = xs.complete_buys_up(held_residual=residual_before)
        assert derived is (expect_side == "YES")
        assert order["side"] == expect_side
        assert order["cross_shadow_live"]["order_buys"] == (
            "UP" if derived else "DOWN")
        assert order["cross_shadow_live"]["executable_depth_field"] == (
            "ask_qty" if derived else "bid_qty")
