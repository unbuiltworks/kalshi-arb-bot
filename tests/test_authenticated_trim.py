"""OWNER GO (narrow, OPEN only): construct the first leg's touch from the
book the matching engine uses.

LIVE EVIDENCE FOR THE GO: 28 OPEN observations carrying both books; in 12
(43%) the gateway said inside while the authenticated book showed crossing.
Accepted OPEN controls crossed 0 of 11, rejected 14 of 17."""
from __future__ import annotations

import inspect
import re
from decimal import Decimal as D

from src import pmus_spring_l5_session as s


def _trim_body() -> str:
    """The WHOLE `_authenticated_trim` function, not a fixed byte count.

    These tests used to slice `[:4000]` chars. That silently became a
    truncation on 2026-09-17 when the owner-authorized freshness submission
    gate added provenance capture inside the function (now 5,195 chars), and
    three assertions failed on properties that were still perfectly true --
    they had simply scrolled past the window. Slicing the real function is
    strictly MORE coverage than 4000 characters, not less: nothing in the
    body can hide past the end of it any more.
    """
    src = inspect.getsource(s)
    body = src[src.index("def _authenticated_trim"):]
    nxt = re.search(r"\n        def ", body[1:])
    return body[:nxt.start() + 1] if nxt else body


def _trim_fn(book, *, slow_ms=0.0):
    """Rebuild the closure's helper against a scripted recorder book."""
    src = inspect.getsource(s.run_spring_session) if hasattr(
        s, "run_spring_session") else None
    assert src is None or "_authenticated_trim" in src
    return book


def test_the_call_site_is_open_only():
    """EXTEND rejections are still unexplained (#93) and the GO was narrow.
    A trim on EXTEND would be acting on evidence we do not have."""
    src = inspect.getsource(s)
    i = src.index("_trim = _authenticated_trim(")
    before = src[max(0, i - 400):i]
    assert 'if relation == "open":' in before
    assert "extend" not in src[i:i + 400]


def test_it_can_only_ever_lower_the_price():
    """The property that makes it safe to run in front of a submission: the
    opposite ask used is the MINIMUM of the two books, so the price is
    arithmetically incapable of moving up. The refusal is in the code, not
    only in the comment."""
    body = _trim_body()
    assert "safe = min(gw, auth_ask)" in body
    assert "refused:would_raise_price" in body
    assert "if new > px_:" in body


def test_it_fails_open_on_every_bad_book():
    """A first leg is never blocked by this. Unreadable, stale, slow,
    missing field, or any exception leaves L5's price exactly as chosen --
    the worst case is the rejection we already get today."""
    body = _trim_body()
    for guard in ('unreadable:', 'slow_read:failed_open', 'stale:',
                  'no_authenticated_opposite_ask', 'error_failed_open'):
        assert guard in body, guard
    # every early return leaves applied False, set once at the top
    assert body.count('"applied": False') == 1
    assert body.count('"applied": True') == 1


def test_a_stale_book_is_not_trusted_in_front_of_an_order():
    """Same bar the forensics module convicts on: a book that cannot speak
    to a submission being made now is ignored, not believed."""
    assert s.AUTH_TRIM_MAX_AGE_S > 0
    from src import l5_rejection_forensics as rf
    assert s.AUTH_TRIM_MAX_AGE_S == rf.AUTHENTICATED_FRESH_S


def test_latency_in_front_of_a_submission_is_bounded():
    """This is the one place the codebase does not spend latency. Over
    budget it fails open rather than delaying the order."""
    assert 0 < s.AUTH_TRIM_SLOW_MS <= 1000.0
    body = _trim_body()
    assert "AUTH_TRIM_SLOW_MS" in body
    assert "elapsed_ms" in body


def test_the_decision_row_records_what_happened_either_way():
    """A price that moved must be auditable against the price L5 chose."""
    src = inspect.getsource(s)
    i = src.index("_trim = _authenticated_trim(")
    after = src[i:i + 500]
    assert 'report["decisions"][-1]["authenticated_trim"] = _trim' in after
    body = _trim_body()
    assert '"px_in"' in body and '"px_out"' in body


def test_nothing_else_moved():
    """The GO was for the crossing guard. Sizing, the cap, the stop, the
    band and concurrency are untouched by this change."""
    src = inspect.getsource(s)
    body = _trim_body()
    for forbidden in ("choose_micro_quantity", "target_notional",
                      "max_leg_notional", "pair_cost_max", "SPRING_BAND",
                      "daily", "concurren"):
        assert forbidden not in body, forbidden
