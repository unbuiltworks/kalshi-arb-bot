"""Parity: the refactored translation must behave exactly as the frozen one.

WHY THIS FILE IS THE APPROVAL EVIDENCE. `l5_faithful_translation` was frozen
by sha256 at commit 030d8bf, and route (c) changes it deliberately: the body
of `pmus_translate` moves into `_translate_core` so a row-free
`controller_translate` can use the SAME gates instead of a second copy. The
freeze exists to stop SILENT drift, so the hash update is only defensible
with evidence that nothing drifted.

HOW IT IS CHECKED. The pre-refactor module is loaded from git as a separate
module object and its `pmus_translate` is called alongside the current one on
identical inputs. Equality is asserted field for field across missing books,
stale snapshots, every maker-class outcome, and valid maker and taker
translations on both sides.

Nothing here submits, arms, or reaches a venue.
"""
from __future__ import annotations

import subprocess
import types
from decimal import Decimal as D

import pytest

from src import l5_faithful_translation as FT


# --------------------------------------------------------------- the original

#: The freeze commit. Pinned to the COMMIT, never to `HEAD`: HEAD carries
#: the refactor the moment it is committed, and a parity test that compares
#: the new implementation against itself asserts nothing. The bytes at this
#: commit hash to `e2f786e00c800dbf`, which is the pin this change replaced,
#: so they are exactly the implementation being claimed equivalent.
FREEZE_COMMIT = "030d8bf"
PRE_REFACTOR_SHA16 = "e2f786e00c800dbf"


def _original_module() -> types.ModuleType:
    """The frozen implementation, loaded from git, not from disk."""
    src = subprocess.run(
        ["git", "show", "%s:src/l5_faithful_translation.py" % FREEZE_COMMIT],
        capture_output=True, text=True, check=True).stdout
    import hashlib
    got = hashlib.sha256(src.encode()).hexdigest()[:16]
    assert got == PRE_REFACTOR_SHA16, (
        "%s no longer carries the bytes this parity claim is against "
        "(got %s)" % (FREEZE_COMMIT, got))
    assert "def pmus_translate" in src
    # the frozen file resolved `_translate_core` nowhere; prove we loaded the
    # version that still had the body inline
    assert "_translate_core" not in src, (
        "HEAD already contains the refactor; parity must compare against the "
        "pre-refactor bytes")
    mod = types.ModuleType("frozen_ft")
    mod.__dict__["__name__"] = "frozen_ft"
    exec(compile(src, "frozen_ft", "exec"), mod.__dict__)
    return mod


FROZEN = _original_module()


# ------------------------------------------------------------------ the cases

def _row(bid, ask, age, key="k1", up_size="100", dn_size="100"):
    """A row shaped the way the live feed shapes one.

    The depth keys are `ask_shares`/`bid_shares` because that is what
    `X.displayed_size` reads; spelling them any other way silently produces
    a NO_DISPLAYED_DEPTH refusal and the taker branch never gets compared.
    """
    bbo = {}
    if bid is not None:
        bbo["bid"] = bid
    if ask is not None:
        bbo["ask"] = ask
    if up_size is not None:
        bbo["ask_shares"] = up_size
    if dn_size is not None:
        bbo["bid_shares"] = dn_size
    return {"action_key": key, "pmus": {"bbo": bbo},
            "pmus_snapshot_age_s": age}


def _dec(side, price):
    return {"side": side, "price": D(price), "l5_size": D("1"),
            "relation": "open"}


#: (label, decision, row, authenticated) -- the matrix requirement 4 names
CASES = [
    # missing book, both directions
    ("no_book_both", _dec("UP", "0.40"), _row(None, None, 0.1), {}),
    ("no_book_bid", _dec("DOWN", "0.40"), _row(None, "0.55", 0.1), {}),
    ("no_book_ask", _dec("UP", "0.40"), _row("0.54", None, 0.1), {}),
    # stale snapshot: strictly greater than STALE_MAX_S refuses
    ("stale_just_over", _dec("UP", "0.40"), _row("0.54", "0.55", 2.01), {}),
    ("stale_far_over", _dec("UP", "0.40"), _row("0.54", "0.55", 30.0), {}),
    ("stale_at_bound", _dec("UP", "0.40"), _row("0.54", "0.55", 2.0), {}),
    ("stale_absent", _dec("UP", "0.40"), _row("0.54", "0.55", None), {}),
    # STALENESS IS CHECKED BEFORE THE BOOK: a stale AND bookless row must
    # report staleness, and the order of the two gates is part of parity
    ("stale_and_no_book", _dec("UP", "0.40"), _row(None, None, 9.0), {}),
    # restable -> maker-class outcomes
    ("maker_class_absent", _dec("UP", "0.40"), _row("0.54", "0.55", 0.1), {}),
    ("maker_class_none_value", _dec("UP", "0.40"),
     _row("0.54", "0.55", 0.1), {"k1": None}),
    ("maker_class_no_interaction", _dec("UP", "0.40"),
     _row("0.54", "0.55", 0.1), {"k1": {"klass": "NO_INTERACTION"}}),
    ("maker_class_ambiguous", _dec("UP", "0.40"),
     _row("0.54", "0.55", 0.1), {"k1": {"klass": "SOMETHING_ELSE"}}),
    ("maker_class_bare_string", _dec("UP", "0.40"),
     _row("0.54", "0.55", 0.1), {"k1": "NO_INTERACTION"}),
    # not restable -> taker
    ("taker_up_at_touch", _dec("UP", "0.55"), _row("0.54", "0.55", 0.1), {}),
    ("taker_up_through", _dec("UP", "0.90"), _row("0.54", "0.55", 0.1), {}),
    ("taker_down_through", _dec("DOWN", "0.90"),
     _row("0.54", "0.55", 0.1), {}),
    ("taker_no_depth", _dec("UP", "0.90"),
     _row("0.54", "0.55", 0.1, up_size=None, dn_size=None), {}),
    ("taker_zero_depth", _dec("UP", "0.90"),
     _row("0.54", "0.55", 0.1, up_size="0"), {}),
    ("taker_depth_capped", _dec("UP", "0.90"),
     _row("0.54", "0.55", 0.1, up_size="0.4"), {}),
    # DOWN-side restable uses 1 - bid as the touch
    ("maker_down", _dec("DOWN", "0.40"), _row("0.54", "0.55", 0.1),
     {"k1": {"klass": "NO_INTERACTION"}}),
]


def _strict_class_case():
    """A valid maker translation needs a class the module itself accepts,
    so it is read from the module rather than guessed."""
    from src import l5_execution_translations as X
    klass = sorted(X.STRICT_CLASSES)[0]
    return ("maker_valid_strict", _dec("UP", "0.40"),
            _row("0.54", "0.55", 0.1), {"k1": {"klass": klass}})


CASES.append(_strict_class_case())


# ------------------------------------------------------------------- the tests

class TestTheRefactorChangedNothing:

    @pytest.mark.parametrize("label,dec,row,auth",
                             [(c[0], c[1], c[2], c[3]) for c in CASES],
                             ids=[c[0] for c in CASES])
    def test_identical_to_the_frozen_implementation(self, label, dec, row,
                                                    auth):
        want = D("1")
        before = FROZEN.pmus_translate(dec, row, auth, want)
        after = FT.pmus_translate(dec, row, auth, want)
        assert after == before, label

    def test_the_matrix_actually_covers_every_outcome(self):
        """A parity suite that only ever hits one branch proves nothing."""
        reasons, modes, oks = set(), set(), set()
        for _l, dec, row, auth in CASES:
            got = FT.pmus_translate(dec, row, auth, D("1"))
            oks.add(bool(got.get("ok")))
            if got.get("reason") is not None:
                reasons.add(got["reason"])
            if got.get("mode") is not None:
                modes.add(got["mode"])
        assert oks == {True, False}
        assert FT.MAKER in modes and FT.TAKER in modes
        assert len(reasons) >= 5, sorted(reasons)

    def test_a_valid_maker_and_a_valid_taker_both_occur(self):
        got = {c[0]: FT.pmus_translate(c[1], c[2], c[3], D("1"))
               for c in CASES}
        assert got["maker_valid_strict"]["ok"] is True
        assert got["maker_valid_strict"]["mode"] == FT.MAKER
        assert got["taker_depth_capped"]["ok"] is True
        assert got["taker_depth_capped"]["mode"] == FT.TAKER
        # the book had less than we wanted, so the fill is clamped to it
        assert got["taker_depth_capped"]["depth_capped"] is True
        assert got["taker_depth_capped"]["qty"] == D("0.4")
        assert got["taker_no_depth"]["ok"] is False


class TestTheRowFreeSiblingSharesTheGates:
    """Not a second set of rules: the same inputs must give the same answer
    through either entry point."""

    @pytest.mark.parametrize("label,dec,row,auth",
                             [(c[0], c[1], c[2], c[3]) for c in CASES],
                             ids=[c[0] for c in CASES])
    def test_the_controller_path_matches_the_row_path(self, label, dec, row,
                                                      auth):
        want = D("1")
        via_row = FT.pmus_translate(dec, row, auth, want)
        got = auth.get(row.get("action_key"))
        klass = got.get("klass") if isinstance(got, dict) else got
        via_controller = FT.controller_translate(
            dec, bbo=((row.get("pmus") or {}).get("bbo")) or {},
            snapshot_age_s=row.get("pmus_snapshot_age_s"),
            maker_class=klass, want=want)
        assert via_controller == via_row, label

    def test_it_needs_no_row_no_action_key_and_no_pmus_block(self):
        """Checked against the CODE, not the prose. The docstring names
        `action_key` in order to say there is none, so the body is isolated
        first -- otherwise the explanation would trip the assertion."""
        import ast
        import inspect
        src = inspect.getsource(FT.controller_translate)
        fn = ast.parse(src.lstrip()).body[0]
        body = fn.body[1:] if (isinstance(fn.body[0], ast.Expr) and isinstance(
            getattr(fn.body[0], "value", None), ast.Constant)) else fn.body
        code = "\n".join(ast.unparse(n) for n in body)
        for forbidden in ("action_key", "pmus", "row", "authenticated"):
            assert forbidden not in code, (forbidden, code)
        # and it takes the book directly rather than a record to dig through
        args = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
        assert args == ["decision", "bbo", "snapshot_age_s",
                        "maker_class", "want"], args

    def test_the_staleness_bound_is_the_same_object_not_a_copy(self):
        """One implementation, so there is no second 2.0 to drift."""
        import inspect
        core = inspect.getsource(FT._translate_core)
        assert "STALE_MAX_S" in core
        assert FT.STALE_MAX_S == 2.0
        # and neither entry point re-implements it
        for fn in (FT.pmus_translate, FT.controller_translate):
            s = inspect.getsource(fn)
            assert "STALE_MAX_S" not in s
            assert "T_NO_BOOK" not in s
            assert "STRICT_CLASSES" not in s

    def test_a_missing_maker_class_is_unevaluable_not_permitted(self):
        got = FT.controller_translate(
            _dec("UP", "0.40"), bbo={"bid": "0.54", "ask": "0.55"},
            snapshot_age_s=0.1, maker_class=None, want=D("1"))
        assert got["ok"] is False
        assert got["reason"] == FT.T_UNEVALUABLE

    def test_a_stale_controller_book_is_refused(self):
        got = FT.controller_translate(
            _dec("UP", "0.40"), bbo={"bid": "0.54", "ask": "0.55"},
            snapshot_age_s=9.0, maker_class="X", want=D("1"))
        assert got["ok"] is False and got["reason"] == FT.T_STALE

    def test_a_bookless_controller_call_is_refused(self):
        got = FT.controller_translate(
            _dec("UP", "0.40"), bbo={}, snapshot_age_s=0.1,
            maker_class="X", want=D("1"))
        assert got["ok"] is False and got["reason"] == FT.T_NO_BOOK


class TestTheFreezeWasUpdatedDeliberately:

    def test_the_frozen_hash_matches_the_file_on_disk(self):
        """The freeze still binds -- it was re-pinned, not removed."""
        import hashlib
        import pathlib
        import tests.test_faithful_rule_frozen as FZ
        root = pathlib.Path(FZ.__file__).resolve().parent.parent
        for rel, want in FZ.FROZEN.items():
            got = hashlib.sha256((root / rel).read_bytes()).hexdigest()[:16]
            assert got == want, rel

    def test_the_rule_still_has_no_tunable_threshold(self):
        assert FT.STALE_MAX_S == 2.0
        assert FT.MAKER == "MAKER" and FT.TAKER == "TAKER"
        assert FT.LAYERS == ("L5_NATIVE", "PMUS_TRANSLATION",
                             "SAFETY_OVERLAY")
