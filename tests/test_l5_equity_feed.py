"""The transfer watch: proof that no money moved, or a refusal."""
from __future__ import annotations

import ast
import datetime as dt
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_equity_baseline as EB
from src import l5_equity_feed as EF

from tests.test_l5_venue_cost_attestation import Feed, fill

ROOT = Path(__file__).resolve().parents[1]
SINCE = dt.datetime(2026, 11, 20, 0, 30, tzinfo=dt.timezone.utc)
M = "cpc-btc-updown-5m-2026-11-20-0100z"


def trade(t):
    return fill(M, "UP", "1", "0.5", "T" + t, "V" + t, t=t)


def dep(t, kind="activity_type_account_deposit"):
    return {"type": kind, "accountBalanceChange": {"time": t, "amount": "1"}}


def run(feed, *, steps=100, since=SINCE, w=None):
    w = w or EF.TransferWatch()
    now = 1000.0
    for _ in range(steps):
        now += 1
        w.step(feed, since, now)
    return w, now


def verdict(w, now, since=SINCE):
    return EB.net_transfers(w.view(since, now), since)


OLD = "2026-11-18T%02d:00:00Z"


def old_page(h):
    return [trade(OLD % h), trade(OLD % (h + 1))]


def test_before_any_read_it_refuses():
    w = EF.TransferWatch()
    v = w.view(SINCE, 1.0)
    assert v["by"] == "NOT_READ_YET"
    assert EB.net_transfers(v, SINCE)["net_usd"] is None


def test_a_quiet_feed_read_to_its_end_proves_nothing_moved():
    w, now = run(Feed([[trade("2026-11-20T00:40:00Z")],
                       [dep("2026-10-01T00:00:00Z")]]))
    v = w.view(SINCE, now)
    assert v["by"] == "END_OF_FEED"
    assert verdict(w, now)["net_usd"] == D(0)


def test_old_pages_are_never_proof_only_the_end_of_the_feed_is():
    """The feed is not ordered. A deposit served after any number of pages
    of old rows must still be seen, so nothing but the venue ending the
    feed stops the read."""
    feed = Feed([[trade("2026-11-20T00:40:00Z")], old_page(1), old_page(3),
                 old_page(5), old_page(7),
                 [dep("2026-11-20T00:45:00Z")]])
    w, now = run(feed)
    assert len(feed.reads) == 6
    assert w.view(SINCE, now)["by"] == "END_OF_FEED"
    assert verdict(w, now)["why"] == "MONEY_MOVED_SINCE_BASELINE"


def test_a_feed_that_never_ends_is_never_trusted():
    feed = Feed([old_page(1), old_page(3), old_page(5), old_page(7)],
                eof=False)
    w, now = run(feed)
    assert w.view(SINCE, now)["by"] == "INCOMPLETE"
    assert verdict(w, now)["why"] == "FEED_NOT_READ_BACK_TO_BASELINE"


def test_there_is_no_date_based_stop_in_the_code():
    src = (ROOT / "src" / "l5_equity_feed.py").read_text()
    code = src[src.index("class TransferWatch"):]
    for word in ("floor", "MARGIN", "max(times)", "row_time(r)"):
        assert word not in code, word


@pytest.mark.parametrize("kind", ["activity_type_account_deposit",
                                  "activity_type_transfer",
                                  "activity_type_referral_bonus",
                                  "activity_type_something_new"])
def test_any_non_trading_row_after_the_baseline_refuses(kind):
    w, now = run(Feed([[dep("2026-11-20T00:31:00Z", kind)]]))
    assert verdict(w, now)["why"] == "MONEY_MOVED_SINCE_BASELINE"


def test_an_undated_non_trading_row_refuses():
    w, now = run(Feed([[{"type": "activity_type_transfer",
                         "transfer": {"amount": "5"}}]]))
    assert verdict(w, now)["why"] == "UNDATED_TRANSFER_ROW"


def test_a_feed_too_long_to_finish_is_incomplete_and_refuses(monkeypatch):
    monkeypatch.setattr(EF, "MAX_PAGES", 3)
    feed = Feed([[trade("2026-11-20T00:4%d:00Z" % i)] for i in range(6)],
                eof=False)
    w, now = run(feed)
    assert w.view(SINCE, now)["by"] == "INCOMPLETE"
    assert verdict(w, now)["why"] == "FEED_NOT_READ_BACK_TO_BASELINE"


def test_a_transport_error_keeps_refusing_and_backs_off():
    feed = Feed([[trade("2026-11-20T00:40:00Z")]], fail=RuntimeError("x"))
    w, now = run(feed, steps=30)
    assert w.last_error and w.view(SINCE, now)["by"] == "NOT_READ_YET"
    assert verdict(w, now)["net_usd"] is None
    assert len(feed.reads) == 1                      # backed off, no storm


def test_an_error_after_a_good_read_does_not_outlive_the_staleness_limit():
    feed = Feed([[trade("2026-11-20T00:40:00Z")]])
    w, now = run(feed, steps=5)
    assert verdict(w, now)["net_usd"] == D(0)
    feed.fail = RuntimeError("venue down")
    later = now + EF.STALE_S + 1
    w.step(feed, SINCE, later)
    assert w.view(SINCE, later)["by"] == "STALE"
    assert verdict(w, later)["net_usd"] is None


def test_it_rereads_on_the_refresh_interval_and_not_before():
    feed = Feed([[trade("2026-11-20T00:40:00Z")]])
    w, now = run(feed, steps=50)
    assert len(feed.reads) == 1
    w.step(feed, SINCE, now + EF.REFRESH_S + 1)
    assert len(feed.reads) == 2


def test_a_deposit_that_arrives_later_is_caught_on_the_next_read():
    feed = Feed([[trade("2026-11-20T00:40:00Z")]])
    w, now = run(feed, steps=5)
    assert verdict(w, now)["net_usd"] == D(0)
    feed.pages = [[dep("2026-11-20T00:55:00Z"),
                   trade("2026-11-20T00:40:00Z")]]
    now += EF.REFRESH_S + 1
    w.step(feed, SINCE, now)
    assert verdict(w, now)["why"] == "MONEY_MOVED_SINCE_BASELINE"


def test_a_new_baseline_discards_what_was_read_for_the_old_one():
    feed = Feed([[trade("2026-11-20T00:40:00Z")]])
    w, now = run(feed, steps=5)
    newer = SINCE + dt.timedelta(hours=1)
    assert w.view(newer, now)["by"] == "NOT_READ_YET"
    w.step(feed, newer, now + 1)
    assert w.view(newer, now + 1)["by"] == "END_OF_FEED"


def test_it_only_ever_gets_the_activity_feed():
    feed = Feed([[trade("2026-11-20T00:40:00Z")], old_page(1)])
    run(feed)
    assert feed.reads and all(p.startswith("/v1/portfolio/activities")
                              for p in feed.reads)


def test_the_module_writes_nothing_and_knows_no_ledger():
    src = (ROOT / "src" / "l5_equity_feed.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)) \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(getattr(node.body[0], "value", None),
                               ast.Constant):
            node.body = node.body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    for word in ("store", "state[", "save(", "open(", "_post", "_put",
                 "_delete", "submit", "cancel", "realized", "os."):
        assert word not in code, word
