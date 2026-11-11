"""A rate-limited activity walk must not become an authoritative verdict.

LIVE 2026-09-11T23:44:08Z. With 1.5s pacing BETWEEN markets the preflight
still took HTTP 429 on 80 of 93 markets -- because the walk itself fires up
to ACTIVITY_MAX_PAGES requests PER market, so pacing between markets never
touched the burst. Worse, seven markets came back PENDING_SETTLEMENT: the
first page succeeded, a later cursor page was rate-limited, the `except:
continue` swallowed it, the walk ended silently with a partial row set, and a
missing POSITION_RESOLUTION row was reported as "not settled".

That is the collapse this codebase keeps warning about -- "we could not ask"
presented as "the answer was no" -- and here it was doing it on the check
that gates a live trading campaign.
"""
import pytest

from src import pmus_settlement_auditor as auditor
from src import pmus_q1_executor_v4 as q1


class _T:
    """Transport that serves page 1 then fails every cursor page."""

    def __init__(self, fail_with=None, pages=2):
        self.calls: list = []
        self.fail_with = fail_with
        self.pages = pages

    def request(self, method, path, base=None):
        self.calls.append(path)
        if "cursor" not in path and "Cursor" not in path and "page" not in path:
            return {"activities": [{"type": "X", "createTime": "2026-09-11T10:00:00Z"}],
                    "eof": False, "nextCursor": "c1"}
        if self.fail_with is not None:
            raise self.fail_with
        return {"activities": [{"type": "Y", "createTime": "2026-09-11T09:00:00Z"}],
                "eof": True}


def _rate_limited():
    exc = Exception("HTTP Error 429: Too Many Requests")
    exc.code = 429
    return exc


def test_the_walk_uses_the_retrying_getter(monkeypatch):
    """q1._get_with_retry already backs off on 429; the auditor was calling
    t.request directly and had no backoff at all."""
    import inspect
    src = inspect.getsource(auditor.activity_pages)
    assert "q1.base._get_with_retry" in src
    assert 't.request("GET", "/v1/portfolio/activities")' not in src


def test_a_failed_cursor_page_marks_the_walk_truncated(monkeypatch):
    monkeypatch.setattr(q1.base, "_get_with_retry",
                        lambda t, path, **kw: t.request("GET", path))
    t = _T(fail_with=_rate_limited())
    walk = auditor.activity_pages(t)
    assert walk["truncated_by_error"] is True
    assert walk["ended"] == "error"
    assert walk["errors"] and "429" in walk["errors"][0]


def test_a_clean_walk_is_not_marked_truncated(monkeypatch):
    monkeypatch.setattr(q1.base, "_get_with_retry",
                        lambda t, path, **kw: t.request("GET", path))
    walk = auditor.activity_pages(_T(fail_with=None))
    assert walk["truncated_by_error"] is False
    assert walk["errors"] == []
    assert walk["ended"] == "eof"


def test_a_failed_request_is_not_retried_as_a_wrong_cursor_name(monkeypatch):
    """The old code `continue`d, so one rate-limited page was retried under
    every candidate parameter name -- five failing requests instead of one,
    which made the rate limiting worse, and then ended the walk silently."""
    monkeypatch.setattr(q1.base, "_get_with_retry",
                        lambda t, path, **kw: t.request("GET", path))
    t = _T(fail_with=_rate_limited())
    auditor.activity_pages(t)
    cursor_calls = [c for c in t.calls if "c1" in c]
    assert len(cursor_calls) == 1, cursor_calls


def test_max_pages_is_not_an_error(monkeypatch):
    """A walk bounded by ACTIVITY_MAX_PAGES is complete-as-designed; only a
    FAILED REQUEST makes absence of a row unprovable."""
    monkeypatch.setattr(q1.base, "_get_with_retry",
                        lambda t, path, **kw: t.request("GET", path))

    class _Endless(_T):
        def request(self, method, path, base=None):
            self.calls.append(path)
            return {"activities": [{"type": "X", "createTime": "2026-09-11T10:00:00Z"}],
                    "eof": False, "nextCursor": "c1"}

    walk = auditor.activity_pages(_Endless(), max_pages=3)
    assert walk["ended"] == "max_pages"
    assert walk["truncated_by_error"] is False


def test_the_pending_verdict_refuses_to_come_from_a_truncated_walk():
    """The load-bearing one. audit_market must raise rather than return
    PENDING_SETTLEMENT when the walk it is reasoning over was cut short --
    settle_held turns that into AUDIT_UNAVAILABLE with the reason."""
    import inspect
    src = inspect.getsource(auditor.audit_market)
    assert "truncated_by_error" in src
    assert "activity_walk_truncated" in src
    # and the raise must come BEFORE the PENDING_SETTLEMENT assignment
    assert src.index("truncated_by_error") < src.index('"PENDING_SETTLEMENT"')
