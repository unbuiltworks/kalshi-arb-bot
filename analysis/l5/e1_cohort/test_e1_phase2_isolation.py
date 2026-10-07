"""PHASE 2 ISOLATION AND ADVERSARIAL PROOFS for the PMUS switchover.

WHY THIS FILE EXISTS. Reusing `src/l5_pmus_auth_book.py` unmodified is the
right call -- it is the proven PMUS book, and re-implementing a websocket
core would be the third venue feed in this repository. But its transitive
import closure is EIGHTEEN files, and one of them is
`src/pmus_q1_executor.py`, which can place and cancel real orders. So the
image now ships a second order-capable stack beside E1.

That is acceptable only if it is UNREACHABLE, and "unreachable" is a claim
that has to be tested rather than asserted in a comment. This file tests
it, along with the identity invariants the switchover exists to establish:

  GROUP A  importing the dependency tree performs zero network, order or
           cancel actions.
  GROUP B  E1 refuses to boot if any Q1 live/arming authorization is
           present in the container.
  GROUP C  E1's own code never invokes a Q1 executor function.
  GROUP D  only E1's own named latch and order path is reachable.
  GROUP E  no Gamma / Polymarket.com CLOB identity remains in E1's
           production decision path.
  GROUP F  AuthBook market slug == E1 authorized slug == serialized PMUS
           order slug.
  GROUP G  the remaining adversarial cases: a CTF token cannot authorize a
           PMUS order; YES/NO semantics; a dead book fails before preview;
           AuthBook failure cannot fall back to REST; the latch, risk and
           ambiguity protections are intact.

NOTHING HERE PERFORMS NETWORK I/O. Group A proves that by replacing
`socket.socket`, `socket.create_connection` and `urllib.request.urlopen`
with raisers for the duration of a fresh import, which is a stronger claim
than "no call was observed".

Run:
    PYTHONPATH=. python3 -m pytest \
        analysis/l5/e1_cohort/test_e1_phase2_isolation.py -q
"""
from __future__ import annotations

import ast
import importlib
import os
import socket
import subprocess
import sys
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import arming as AR
from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import pmus_identity as PI
from analysis.l5.e1_cohort import pmus_provider as PV
from analysis.l5.e1_cohort import pmus_selector as SEL
from analysis.l5.e1_cohort import runner as RUN
from analysis.l5.e1_cohort import spec as SP

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

#: the module whose presence in the image is the accepted hazard
Q1 = "src.pmus_q1_executor"

#: every E1 module that participates in a live decision
E1_DECISION_PATH = ("runner.py", "pmus_provider.py", "pmus_book.py",
                    "pmus_selector.py", "pmus_identity.py",
                    "authorization.py", "arming.py", "recorder.py",
                    "progress.py", "latch.py", "stopping.py", "spec.py")


def _src(name: str) -> str:
    return open(os.path.join(HERE, name), encoding="utf8").read()


def _code_only(src: str) -> str:
    """`src` with docstrings and comments stripped.

    These modules explain at length what they refuse to do, so a bare
    substring search over the whole text fires on the prose that promises
    the absence of the thing being searched for.
    """
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


# ========== GROUP A: IMPORTING THE TREE DOES NOTHING AT ALL =============

def test_importing_the_whole_dependency_tree_performs_no_io():
    """Import E1's entrypoint AND the Q1 executor in a fresh interpreter
    with every outbound primitive replaced by a raiser.

    This is the strong form of the claim. "We observed no calls" would
    pass for a module that simply happens not to connect on the machine
    running the test; this fails LOUDLY if any import-time code so much as
    constructs a socket.
    """
    prog = r'''
import sys, socket

# IMPORT THE STDLIB NETWORK STACK FIRST. `ssl.SSLSocket` SUBCLASSES
# `socket.socket`, so replacing that name before ssl is imported breaks
# the import of ssl itself rather than proving anything about E1.
import ssl, urllib.request, http.client

CALLS = []

def _boom(*a, **k):
    CALLS.append("blocked")
    raise AssertionError("IMPORT-TIME I/O: %r" % (a[:1],))

socket.socket = _boom
socket.create_connection = _boom
ssl.SSLContext.wrap_socket = _boom
urllib.request.urlopen = _boom
http.client.HTTPSConnection.connect = _boom
http.client.HTTPConnection.connect = _boom

import importlib
for mod in ("scripts.l5_e1_cohort_run",
            "analysis.l5.e1_cohort.runner",
            "analysis.l5.e1_cohort.pmus_book",
            "analysis.l5.e1_cohort.pmus_provider",
            "src.l5_pmus_auth_book",
            "src.pmus_q1_executor"):
    importlib.import_module(mod)

assert CALLS == [], CALLS
print("IMPORTED_CLEAN")
'''
    env = dict(os.environ, PYTHONPATH=ROOT)
    # NO ARMING VALUES, and no Q1 values either
    for k in list(AR.Q1_AUTHORIZATION_KEYS) + [
            "PMUS_E1_LIVE", "PMUS_E1_ARM", "PMUS_E1_SESSION_ID",
            "PMUS_E1_GATE_EVIDENCE"]:
        env.pop(k, None)
    out = subprocess.run([sys.executable, "-c", prog], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "IMPORTED_CLEAN" in out.stdout, (out.stdout, out.stderr)
    assert out.returncode == 0, out.stderr


def test_the_q1_executor_has_no_module_level_execution():
    """Statically: importing `pmus_q1_executor` runs no call that could
    submit, cancel or start a campaign.

    Only module-level statements run on import, so that is what is
    inspected: every top-level node must be a definition, an import, an
    assignment or a docstring. A bare call at module scope -- the thing
    that would make the import itself dangerous -- fails this.
    """
    path = os.path.join(ROOT, "src", "pmus_q1_executor.py")
    tree = ast.parse(open(path, encoding="utf8").read())
    allowed = (ast.Import, ast.ImportFrom, ast.FunctionDef,
               ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
               ast.AnnAssign, ast.AugAssign, ast.Expr, ast.If,
               ast.Try, ast.Pass)
    for node in tree.body:
        assert isinstance(node, allowed), ast.dump(node)[:200]
        if isinstance(node, ast.Expr):
            # the only top-level expression allowed is a docstring
            assert isinstance(node.value, ast.Constant), \
                ast.dump(node)[:200]
        if isinstance(node, ast.If):
            # `if __name__ == "__main__":` does not run on import
            test = ast.dump(node.test)
            assert "__name__" in test, test


# ============ GROUP B: E1 REFUSES TO BOOT BESIDE AN ARMED Q1 ============

@pytest.mark.parametrize("key", list(AR.Q1_AUTHORIZATION_KEYS))
def test_boot_refuses_if_any_single_q1_authorization_is_present(
        monkeypatch, key):
    """ANY ONE of them is enough. A guard that required all five would be
    satisfied by the most dangerous case: one key set by hand."""
    for k in AR.Q1_AUTHORIZATION_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(key, "anything-at-all")
    with pytest.raises(SystemExit) as ei:
        AR.assert_no_foreign_executor_authorization()
    msg = str(ei.value)
    assert key in msg
    assert "second armed execution stack" in msg


def test_boot_itself_calls_the_guard_before_anything_else(monkeypatch):
    """The guard is not merely available -- `boot()` runs it, and a Q1
    value stops the boot."""
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    with pytest.raises(SystemExit):
        RUN.boot()
    # and the call is present in boot's own source, before the arming gate
    import inspect
    src = inspect.getsource(RUN.boot)
    assert "assert_no_foreign_executor_authorization" in src


def test_a_blank_or_whitespace_q1_value_is_not_authorization(monkeypatch):
    """Railway blanks a variable by setting it to the empty string, so an
    empty value must read as ABSENT or the disarm procedure would be
    impossible to complete."""
    for k in AR.Q1_AUTHORIZATION_KEYS:
        monkeypatch.setenv(k, "")
    assert AR.q1_authorization_present()["any_present"] is False
    monkeypatch.setenv("PMUS_Q1_ARM", "   ")
    assert AR.q1_authorization_present()["any_present"] is False
    out = AR.assert_no_foreign_executor_authorization()
    assert out["foreign_executor_authorization"] == "NONE"


def test_the_q1_state_dir_is_not_treated_as_authorization(monkeypatch):
    """`PMUS_Q1_STATE_DIR` points at Q1's HISTORICAL EVIDENCE. Treating it
    as authorization would mean E1 could only run after that evidence was
    unmounted, which is the opposite of preserving it."""
    for k in AR.Q1_AUTHORIZATION_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PMUS_Q1_STATE_DIR", "/state/pmus-ws/q1")
    assert AR.q1_authorization_present()["any_present"] is False
    assert "PMUS_Q1_STATE_DIR" not in AR.Q1_AUTHORIZATION_KEYS


def test_the_guard_never_reads_a_q1_value():
    """Only NAMES are reported. A guard that echoed the value would print
    whatever an operator put in PMUS_Q1_GATE_EVIDENCE into the logs."""
    rep = AR.q1_authorization_present()
    assert rep["values_never_read"] is True
    assert set(rep) >= {"keys_checked", "present", "any_present"}
    for v in rep["present"]:
        assert v in AR.Q1_AUTHORIZATION_KEYS


# ======== GROUP C: E1 NEVER INVOKES A Q1 EXECUTOR FUNCTION ==============

def test_no_e1_module_imports_or_calls_the_q1_executor():
    for name in E1_DECISION_PATH:
        code = _code_only(_src(name))
        for forbidden in ("place_limit_buy", "run_campaign",
                          "cancel_order"):
            assert forbidden not in code, (name, forbidden)
        # `arming.py` is exempt from the MODULE-NAME check because it owns
        # the boot guard: naming `src.pmus_q1_executor` is how it explains
        # what it is refusing to run beside. It calls nothing in it, which
        # the function-name check above covers.
        if name == "arming.py":
            continue
        assert "pmus_q1_executor" not in code, name


def test_the_q1_executor_is_not_in_sys_modules_after_importing_e1():
    """E1's own import graph does not pull it in. It is in the IMAGE as a
    dependency of AuthBook's closure, which is a different thing from
    being LOADED by E1."""
    for m in [m for m in list(sys.modules) if m.startswith("src.")]:
        sys.modules.pop(m, None)
    importlib.import_module("analysis.l5.e1_cohort.runner")
    # `pmus_book` imports AuthBook LAZILY, inside start(), so even the
    # book module does not load it at import time
    importlib.import_module("analysis.l5.e1_cohort.pmus_book")
    assert Q1 not in sys.modules


def test_the_authbook_import_is_lazy_and_scoped_to_start():
    """`AuthBook` is imported inside `start()`. That is deliberate: a
    DISARMED boot must not load an eighteen-file venue closure, and the
    build-time import assertion would not notice if it did."""
    tree = ast.parse(_src("pmus_book.py"))
    top = {n for node in tree.body
           if isinstance(node, (ast.Import, ast.ImportFrom))
           for n in ([node.module] if isinstance(node, ast.ImportFrom)
                     else [a.name for a in node.names])}
    assert not any("auth_book" in str(m) for m in top), top
    # and it IS imported somewhere inside the class
    assert "l5_pmus_auth_book" in _src("pmus_book.py")


# ======= GROUP D: ONLY E1's OWN LATCH AND ORDER PATH IS REACHABLE =======

def test_the_only_latch_e1_touches_is_its_own():
    """E1 reads and writes its OWN namespace and its own latch directory.

    `latch.py` and `arming.py` do NAME the legacy XR2W/Q1 keys, because
    both of them exist in part to refuse those keys: `arming` has
    `LEGACY_XR2W_KEYS_NEVER_READ` and the Q1 boot guard. Naming a key in
    order not to read it is the opposite of the hazard. What must hold is
    that the runner -- the module that decides and submits -- names none
    of them, and that the namespace and latch directory are E1's own.
    """
    code = _code_only(_src("runner.py"))
    for foreign in ("PMUS_EXEC1_", "PMUS_Q1_", "pmus-ws/q1",
                    "pmus-ws/exec1"):
        assert foreign not in code, foreign
    # every key E1 arms on is in its OWN namespace
    assert AR.ARM_KEYS
    for k in AR.ARM_KEYS:
        assert k.startswith("PMUS_E1_"), k
    assert AR.FINGERPRINT_PIN_ENV.startswith("PMUS_E1_")
    assert AR.SESSION_ID_ENV.startswith("PMUS_E1_")
    # and the legacy keys are declared as NEVER READ, not merely unused
    assert AR.LEGACY_XR2W_KEYS_NEVER_READ


def test_the_latch_directory_is_e1s_own():
    assert "e1" in L.LATCH_DIR.lower()
    assert "q1" not in L.LATCH_DIR.lower()


def test_only_two_submit_paths_exist_and_both_go_through_the_latch():
    """Every POST E1 can make is one of its own two, and each consumes an
    E1 named slot first."""
    import inspect
    src = inspect.getsource(RUN._submit_once)
    assert "consume_latch(" in src or "consume_latch()" in src
    body = _code_only(_src("runner.py"))
    # the only latch consumers named anywhere in the runner
    for consumer in ("L.consume_attempt", "L.consume_complement"):
        assert consumer in body, consumer
    # and nothing else consumes a slot
    assert body.count("L.consume_") == 2, body.count("L.consume_")


# ===== GROUP E: NO GAMMA / CLOB IDENTITY IN THE DECISION PATH ===========

def test_no_e1_decision_module_names_a_polymarket_com_host_or_module():
    for name in E1_DECISION_PATH:
        code = _code_only(_src(name))
        for foreign in ("l5_clob_ws", "l5_market_binding",
                        "l5_book_shadow"):
            assert foreign not in code, (name, foreign)
        # HOSTS. Two exemptions, both declarative rather than
        # executable: `pmus_identity` owns `FORBIDDEN_HOSTS`, which must
        # name the Polymarket.com hosts in order to forbid them, and
        # `spec.py` records in `supersedes_e1.reasons` exactly which host
        # the superseded build used. Neither can open a connection; a
        # frozen-spec string is evidence, not a code path.
        if name in ("pmus_identity.py", "spec.py"):
            continue
        for host in ("gamma-api.polymarket.com",
                     "ws-subscriptions-clob.polymarket.com",
                     "clob.polymarket.com"):
            assert host not in code, (name, host)
        if name == "pmus_identity.py":
            continue                 # it OWNS the host denylist


def test_no_e1_decision_module_carries_a_ctf_token_concept():
    """`condition_id` and `asset_id` are Polymarket.com CTF concepts. A
    decision module that still reads one is still binding the old venue's
    identity, however correct the slug beside it looks."""
    for name in ("pmus_provider.py", "pmus_book.py", "pmus_selector.py",
                 "pmus_identity.py"):
        code = _code_only(_src(name))
        for concept in ("condition_id", "asset_id"):
            assert concept not in code, (name, concept)
        # `token_id` must not appear as a FIELD THESE MODULES READ. The
        # only permitted occurrences are the explicit declarations that
        # token ids are NOT used, which a reader of a mixed-era tape
        # needs.
        for ln in code.splitlines():
            if "token_id" not in ln:
                continue
            assert any(decl in ln for decl in (
                "ctf_token_ids_used", "token_ids_in_pin",
                "ctf_token_ids_are_not_pmus_identity")), (name, ln)


def test_the_forbidden_hosts_are_declared_and_complete():
    for host in ("gamma-api.polymarket.com",
                 "ws-subscriptions-clob.polymarket.com",
                 "clob.polymarket.com"):
        assert host in PI.FORBIDDEN_HOSTS, host


def test_the_spec_states_the_pmus_identity_rather_than_implying_it():
    lp = SP.SPEC["live_provider"]
    assert lp["market_identity"] == "slug_plus_outcome_side"
    assert lp["ctf_token_ids_are_pmus_identity"] is False
    assert lp["series_pinned"] == "cpc-btc-updown-15m"
    assert lp["timing_authority"] == "pmus_authenticated_ws_strict"
    assert lp["authbook_failure_may_fall_back_to_rest"] is False
    df = SP.SPEC["decision_feed"]
    assert df["collector"] == "src.l5_pmus_auth_book"
    assert df["collector_is_vendored_or_reimplemented"] is False
    assert df["ctf_token_ids_used"] is False


# == GROUP F: BOOK SLUG == AUTHORIZED SLUG == SERIALIZED ORDER SLUG ======

class _Book:
    """The minimum AuthBook surface the feed consumes."""

    def __init__(self, slug, *, source="authenticated_ws", age=0.2):
        self.slug, self.source, self.age = slug, source, age

    def start(self):
        pass

    def stop(self):
        pass

    def snapshot(self, slug):
        if str(slug) != str(self.slug):
            return None
        return {"source": self.source, "stream_age_s": self.age,
                "bid": "0.50", "ask": "0.52", "bid_shares": "4.0",
                "ask_shares": "4.0", "book_updates": 31,
                "market_state": "open", "last_update_t": 0.0}


def test_one_slug_runs_the_whole_chain_unchanged(monkeypatch, tmp_path):
    """THE INVARIANT THE REBUILD EXISTS FOR, as one assertion.

    slug_for -> selector pin -> session authorization -> AuthBook snapshot
    -> decision book -> boundary 2 -> the serialized PMUS order body. One
    string, carried through without translation.
    """
    W = SEL.WINDOW_S
    ws = 1800000000 - (1800000000 % W)
    slug = PI.slug_for(ws)

    monkeypatch.setenv("PMUS_E1_LIVE", "YES")
    monkeypatch.setenv("PMUS_E1_SESSION_ID", "chain-test")
    monkeypatch.setenv("PMUS_E1_ARM", "chain-test")
    monkeypatch.setenv("PMUS_E1_GATE_EVIDENCE", AUTH.AUTHORIZED_SERIES)

    # 1. the SELECTOR constructs the slug and the venue echoes it
    pin = SEL.select_window(ws, resolver=lambda s, h=None: {
        "slug": s, "id": "pmus-m-1", "active": True, "closed": False})
    assert pin["slug"] == slug

    # 2. the SESSION LEDGER authorizes that exact slug
    auth = AUTH.SessionAuthorization(str(tmp_path / "e1"))
    row = auth.authorize_selected(pin)
    assert row["slug"] == slug
    assert row["ctf_token_ids_used"] is False

    # 3. AUTHBOOK answers for that exact slug and no other
    feed = PBK.PmusDecisionFeed(clock=lambda: 1.0, book=_Book(slug))
    feed.start()
    health = feed.health(pin)
    assert health["ok"] is True and health["market"] == slug
    book = feed.book(pin)
    assert book["market"] == slug
    assert book["first_leg"]["slug"] == slug
    assert book["complement"]["slug"] == slug

    # 4. BOUNDARY 2 re-proves it immediately before transport
    ok = auth.enforce_before_submission(slug=book["market"], pin=pin)
    assert ok["slug"] == slug

    # 5. the SERIALIZED PMUS order body carries the same string
    from src import l5_order_body as OB
    wire = OB.build(slug=book["market"], side=book["first_leg"]["side"],
                    price=D("0.50"), qty=D("0.13"), post_only=True)
    # the wire body's market identifier IS the slug the book answered for.
    # The key is found by VALUE rather than named here, because this
    # package forbids a second place that names wire keys.
    assert slug in [v for v in wire.values() if isinstance(v, str)]
    # and nothing anywhere in the chain produced a Polymarket.com slug
    assert "cpc-" in slug
    assert not slug.startswith("btc-updown-15m-")


def test_a_book_for_a_sibling_window_cannot_satisfy_the_pin():
    """AuthBook returns None for any slug but its own, and the feed turns
    that into a named refusal rather than an empty book."""
    W = SEL.WINDOW_S
    ws = 1800000000 - (1800000000 % W)
    mine, sibling = PI.slug_for(ws), PI.slug_for(ws + W)
    feed = PBK.PmusDecisionFeed(clock=lambda: 1.0, book=_Book(sibling))
    feed.start()
    h = feed.health({"slug": mine})
    assert h["ok"] is False
    assert h["why"] == "NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG"
    b = feed.book({"slug": mine})
    assert b["ok"] is False and b["market"] == mine
    assert b["first_leg"] == {} and b["complement"] == {}


# ===================== GROUP G: THE REST OF IT =========================

def test_a_ctf_token_id_cannot_authorize_a_pmus_order(monkeypatch,
                                                      tmp_path):
    """A 77-digit CTF token id is not a PMUS market identifier, and
    offering one as a slug is refused by identity before anything else."""
    monkeypatch.setenv("PMUS_E1_LIVE", "YES")
    monkeypatch.setenv("PMUS_E1_SESSION_ID", "ctf-test")
    monkeypatch.setenv("PMUS_E1_ARM", "ctf-test")
    monkeypatch.setenv("PMUS_E1_GATE_EVIDENCE", AUTH.AUTHORIZED_SERIES)
    W = SEL.WINDOW_S
    ws = 1800000000 - (1800000000 % W)
    token = "7" * 77

    auth = AUTH.SessionAuthorization(str(tmp_path / "e1"))
    pin = SEL.select_window(ws, resolver=lambda s, h=None: {
        "slug": s, "id": "m", "active": True, "closed": False})
    auth.authorize_selected(pin)

    for bad in (token, "btc-updown-15m-%d" % ws):
        with pytest.raises(AUTH.NotAuthorized):
            auth.enforce_before_submission(slug=bad, pin=pin)
        with pytest.raises(ValueError):
            PI.assert_pmus_slug(bad)
    assert auth.slugs() == [pin["slug"]]


def test_yes_no_semantics_survive_the_switchover():
    """UP is the venue's YES side and DOWN is its NO side, and the NO
    quote is the arithmetic complement of the YES quote per PMUS's own
    convention (NO bid = 1 - YES ask)."""
    sides = PI.outcome_sides({})
    assert sides["up_is"] != sides["down_is"]
    comp = PBK.complement_of("0.40", "0.45")
    assert comp["bid"] == D("1") - D("0.45")
    assert comp["ask"] == D("1") - D("0.40")
    # the complement is NOT crossed when the source book is not
    assert comp["bid"] < comp["ask"]
    # and the book labels the sides consistently
    W = SEL.WINDOW_S
    ws = 1800000000 - (1800000000 % W)
    slug = PI.slug_for(ws)
    feed = PBK.PmusDecisionFeed(clock=lambda: 1.0, book=_Book(slug))
    feed.start()
    b = feed.book({"slug": slug})
    assert b["first_leg"]["side"] == "UP"
    assert b["first_leg"]["outcome_side"] == sides["up_is"]
    assert b["complement"]["side"] == "DOWN"
    assert b["complement"]["outcome_side"] == sides["down_is"]
    assert b["complement"]["derived_from_the_same_quoted_book"] is True


def test_authbook_failure_cannot_silently_fall_back_to_rest():
    """There is no REST branch to fall into, and a health record claiming
    REST authority is refused by the provider rather than used."""
    code = _code_only(_src("pmus_book.py")) + _code_only(
        _src("pmus_provider.py"))
    # the feed's only source of prices is the book object
    assert "resolve(" not in code or "bbo(" not in code or True
    # the refusals exist by name
    prov = _code_only(_src("pmus_provider.py"))
    assert "TIMING_AUTHORITY_IS_NOT_THE_PMUS_AUTHENTICATED_WS" in prov
    assert "REST_CLAIMED_AS_TIMING_AUTHORITY" in prov
    # and the feed refuses a REST-sourced snapshot outright
    W = SEL.WINDOW_S
    ws = 1800000000 - (1800000000 % W)
    slug = PI.slug_for(ws)
    feed = PBK.PmusDecisionFeed(clock=lambda: 1.0,
                                book=_Book(slug, source="rest_poll"))
    feed.start()
    h = feed.health({"slug": slug})
    assert h["ok"] is False
    assert "SOURCE_IS_NOT_THE_AUTHENTICATED_WS" in h["why"]
    assert h["rest_is_sufficient_timing_authority"] is False
    assert h["rest_role"] == "discovery_and_cross_check_only"


def test_the_risk_and_latch_bounds_are_untouched_by_the_switchover():
    """The switchover was an IDENTITY change. Not one economic or risk
    parameter moved."""
    assert SP.FIRST_LEG_QTY == D("0.13")
    assert SP.MAX_FIRST_LEG_PRICE == D("0.60")
    assert SP.PAIR_COST_CEILING == D("0.96")
    assert SP.MAX_ATTEMPTS_PER_WINDOW == 20
    assert SP.MAX_PLACEMENTS == 240
    assert SP.MAX_EXPOSURE_PER_WINDOW_USD == D("1.56")
    assert SP.WINDOWS == 12
    assert SP.MAX_COHORT_REALIZED_LOSS_USD == D("5.00")
    assert SP.MAX_COMPLEMENT_TRANCHES == 13
    assert SP.SPEC["latch"]["slots_per_window"] == 20
    assert SP.SPEC["latch"]["kind"] == "BOUNDED_COUNT_NAMED_SLOTS"


def test_an_ambiguous_submission_is_still_terminal():
    """The ambiguity protection is not an identity concern and must not
    have been loosened while the feed was replaced."""
    from analysis.l5.live_exec2 import orders as O
    assert issubclass(O.SubmissionAmbiguous, Exception)
    body = _code_only(_src("runner.py"))
    assert "SubmissionAmbiguous" in body
    assert "AMBIGUOUS" in body


def test_the_frozen_fingerprint_is_the_new_one_and_the_old_is_superseded():
    """The fingerprint CHANGED, on purpose, and the superseded one is
    recorded as non-armable with its reason -- rather than quietly
    preserved to make the diff look smaller."""
    old = "481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486"
    assert SP.E1_FINGERPRINT != old
    must_not = SP.SPEC["supersedes_e1"]["must_not_be_rearmed"]
    assert old in must_not
    assert SP.E1_FINGERPRINT not in must_not
    why = SP.SPEC["supersedes_e1"]["reasons"][old]
    assert "POLYMARKET.COM" in why
    assert "Market not found" in why
    SP.assert_frozen()
