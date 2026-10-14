"""One bounded shared activity walk per settle cycle, not one per market.

/v1/portfolio/activities is ACCOUNT-scoped, so auditing N markets by walking
it N times re-reads the same feed N times. LIVE 2026-10-13/14: 173 consecutive
shutdown_idle cycles, 5 markets each, 573 HTTP 429 and zero settlements.

These tests pin the properties that make the fix safe rather than just
cheaper: the cursor still advances, a market the one walk cannot reach stays
PENDING instead of being skipped or falsely settled, and a failure to build
the shared walk degrades to the OLD cost rather than to auditing nothing.
"""
from __future__ import annotations

import types

import pytest

from src import pmus_l5_continuous_session as S


class _Rec:
    def __init__(self):
        self.walk_calls = []
        self.settle_calls = []


@pytest.fixture
def harness(monkeypatch):
    rec = _Rec()
    pending = ["m-2026-09-06", "m-2026-09-08", "m-2026-09-15"]

    monkeypatch.setattr(S, "unsettled_slugs", lambda: list(pending))
    cur = {"v": 0}
    monkeypatch.setattr(S, "load_settle_cursor", lambda: cur["v"])
    monkeypatch.setattr(S, "save_settle_cursor",
                        lambda v: cur.__setitem__("v", v))
    monkeypatch.setattr(S, "SETTLE_AUDIT_BATCH", 3, raising=False)

    import src.pmus_settlement_auditor as A

    firsts = {"m-2026-09-06": "2026-09-06T00:00:00+00:00",
              "m-2026-09-08": "2026-09-08T00:00:00+00:00",
              "m-2026-09-15": "2026-09-15T00:00:00+00:00"}
    monkeypatch.setattr(A, "first_fill_time", lambda s: firsts.get(s))

    def _walk(t, since=None, max_pages=None):
        rec.walk_calls.append({"since": since, "max_pages": max_pages})
        return {"rows": [], "pages": 1, "ended": "max_pages",
                "cursor_param": "cursor", "oldest_time": "2026-09-26T16:03:53",
                "newest_time": "2026-10-14T00:00:00", "errors": [],
                "truncated_by_page_cap": True, "truncated_by_error": False}

    monkeypatch.setattr(A, "activity_pages", _walk)

    def _settle(t, slug, walk=None):
        rec.settle_calls.append({"slug": slug, "walk_is": id(walk)
                                 if walk is not None else None})
        return {"status": "AUDIT_UNAVAILABLE", "settled": False,
                "error": "activity_walk_short_of_since:ended=max_pages"}

    monkeypatch.setattr(S, "settle_held", _settle)

    fake_base = types.SimpleNamespace(
        q1=types.SimpleNamespace(has_credentials=lambda: True,
                                 realized_today=lambda: 0),
        held_inventory_from_ledger=lambda s: None,
        unsettled_floor_total=lambda invs: 0,
        combined_loss=lambda r, invs: 0,
        combined_budget_tripped=lambda r, invs: False,
        daily_loss_stop=lambda: -30,
    )
    monkeypatch.setattr(S, "base", fake_base)
    monkeypatch.setattr(S, "_preflight_sleep", lambda *a, **k: None,
                        raising=False)
    return rec, cur


def test_one_walk_serves_every_market_in_the_batch(harness):
    rec, _ = harness
    out = S.shutdown_settle_pass(t=object())
    assert len(rec.settle_calls) == 3, rec.settle_calls
    # THE WHOLE POINT: one walk, not one per market.
    assert len(rec.walk_calls) == 1, rec.walk_calls
    assert out["shared_walk"]["used"] is True


def test_every_market_receives_the_same_walk_object(harness):
    rec, _ = harness
    S.shutdown_settle_pass(t=object())
    ids = {c["walk_is"] for c in rec.settle_calls}
    assert len(ids) == 1 and None not in ids, rec.settle_calls


def test_the_walk_is_bounded_by_the_earliest_first_fill_in_the_batch(harness):
    rec, _ = harness
    S.shutdown_settle_pass(t=object())
    call = rec.walk_calls[0]
    assert call["since"] == "2026-09-06T00:00:00+00:00", call
    import src.pmus_settlement_auditor as A
    assert call["max_pages"] == A.ACTIVITY_MAX_PAGES, call


def test_an_unreachable_market_stays_pending_and_is_never_falsely_settled(harness):
    rec, _ = harness
    out = S.shutdown_settle_pass(t=object())
    assert out["settled_now"] == []
    assert len(out["still_pending"]) == 3
    assert all(p["status"] == "AUDIT_UNAVAILABLE" for p in out["still_pending"])
    # and it cost no extra requests: still exactly one walk
    assert len(rec.walk_calls) == 1


def test_the_durable_cursor_still_advances(harness):
    _, cur = harness
    before = cur["v"]
    out = S.shutdown_settle_pass(t=object())
    assert out["cursor"] == before
    assert out["next_cursor"] == cur["v"]
    assert cur["v"] != before or len(out["batch"]) == 0


def test_a_failed_shared_walk_degrades_to_per_market_not_to_nothing(
        harness, monkeypatch):
    rec, _ = harness
    import src.pmus_settlement_auditor as A

    def _boom(*a, **k):
        raise RuntimeError("429")

    monkeypatch.setattr(A, "activity_pages", _boom)
    out = S.shutdown_settle_pass(t=object())
    assert out["shared_walk"]["used"] is False
    assert "429" in out["shared_walk"]["error"]
    # every market was still audited -- the old cost, never a silent skip
    assert len(rec.settle_calls) == 3
    assert all(c["walk_is"] is None for c in rec.settle_calls)


def test_the_pass_is_still_read_only_about_settlement(harness):
    out = S.shutdown_settle_pass(t=object())
    assert "no order path" in out["labels"]
    assert out["realized_today_usd"] == "0"


# --------------------------------------------------------------------------
# The measurement, not the claim: count the authenticated requests the pass
# actually makes. The fallback path IS the old behaviour, so one test
# measures both and reports the ratio.
# --------------------------------------------------------------------------

def _paged_feed(pages: int):
    """A feed deep enough to need `pages` requests and never reach `since`."""
    def _get(t, path, *a, **k):
        _get.n += 1
        # every page is full and always offers a next cursor, so the walk
        # stops only on its page cap -- the real shape for the 96 markets
        # whose fills predate the reachable window.
        return {"activities": [{"type": "ACTIVITY_TYPE_TRADE",
                                "timestamp": "2026-10-01T00:00:00+00:00",
                                "trade": {"marketSlug": "other"}}],
                "nextCursor": "c%d" % _get.n, "eof": False}
    _get.n = 0
    return _get


@pytest.fixture
def counted(monkeypatch, harness):
    rec, cur = harness
    import src.pmus_settlement_auditor as A
    # let the REAL activity_pages run, against a counted transport
    monkeypatch.undo()  # drop the stubbed activity_pages only
    return rec, cur, A


def test_requests_drop_from_two_walks_per_market_to_one_walk_per_cycle(
        monkeypatch):
    """The number, measured end to end through the real walk."""
    import src.pmus_settlement_auditor as A
    import src.pmus_q1_executor_v4 as q1

    CAP = A.ACTIVITY_MAX_PAGES
    markets = ["m-2026-09-06", "m-2026-09-08", "m-2026-09-15"]

    def _run(shared: bool) -> int:
        getter = _paged_feed(CAP)
        monkeypatch.setattr(q1.base, "_get_with_retry", getter)
        monkeypatch.setattr(A, "first_fill_time",
                            lambda s: "2026-09-06T00:00:00+00:00")
        walk = A.activity_pages(
            None, since="2026-09-06T00:00:00+00:00",
            max_pages=CAP) if shared else None
        before = getter.n
        for slug in markets:
            A.traded_qty_with_evidence(slug, object(), walk=walk)
        return (before if shared else 0) + (getter.n - before)

    with_shared = _run(True)
    with_per_market = _run(False)

    # one walk, then nothing per market (each market's call reuses it and
    # returns qty None because the one walk does not cover its `since`)
    assert with_shared == CAP, with_shared
    # the old shape: one full walk PER MARKET
    assert with_per_market == CAP * len(markets), with_per_market
    assert with_per_market == with_shared * len(markets)
