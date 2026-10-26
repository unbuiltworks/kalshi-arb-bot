"""Regression tests for the L5 wallet-ingestion stage.

WHAT THEY PIN, AND WHY EACH ONE EXISTS. Every test here corresponds to a
defect that was live in production on 2026-10-26, when an L5_FOLLOWING
campaign produced zero L5_NATIVE decisions and the logs could not say why:

  * the instrumented collector (`scripts/l5_window_collector_run.py`) had
    raised `OSError: [Errno 28] No space left on device` out of
    `WindowRecord.__init__` -- the FIRST statement of `collect_one` -- every
    five seconds since 2026-10-08 19:30Z, so `new_l5_actions` was never
    reached and the wallet feed was never polled, while the Railway
    deployment reported SUCCESS throughout. There was no retention of any
    kind on the volume: one directory per 15-minute window, forever.
    -> test_prune_* , test_degraded_*

  * the canonical executor (`src/l5_pmus_live_runner.py`) kept its dedupe
    set and its "have I seeded" flag in memory, so every restart re-marked
    the newest 200 feed rows as history and discarded them.
    -> test_restart_*

  * that same poll emitted no counter between the feed and `handle`, so
    "L5 traded nothing", "L5 traded other markets", "the slug stopped
    matching" and "the feed is serving a stale page" produced identical
    logs. -> test_counters_* , test_slug_histogram_*

DATA PROVENANCE. The window slugs and epochs are REAL, taken from the
production logs of deployment 482cb9e3 (`cpc-btc-updown-15m-2026-10-26-0330z`
= epoch 1792985400, cross-checked against the recorder frame stamped
`t: 1792984500.298` in the 0315z window). The activity ROWS are synthesised
to the field contract `l5_pmus_realtime_observer.action_key` reads
(transactionHash/asset/side/price/size/timestamp) -- they are not a captured
wallet payload, and no test here asserts anything about what L5 actually
traded.
"""
from __future__ import annotations

import collections
import datetime as dt
import errno
import json
import os
import pathlib
import tempfile

import pytest

from src import l5_window_collector as C

# real window boundaries from deployment 482cb9e3
W0315 = 1792984500.0
W0330 = 1792985400.0
SLUG_0330 = "cpc-btc-updown-15m-2026-10-26-0330z"


# ------------------------------------------------------------------ slugs

def test_slug_window_start_matches_production_epoch():
    """The retention ordering key must agree with the venue's own clock."""
    assert C.slug_window_start(SLUG_0330) == W0330
    assert C.slug_window_start("cpc-btc-updown-15m-2026-10-26-0315z") == W0315
    # L5's own series prefix parses identically
    assert C.slug_window_start("btc-updown-15m-2026-01-01-0000z") is not None


@pytest.mark.parametrize("bad", [
    "garbage", "", "cpc-btc-updown-15m-bad-9999z",
    "cpc-x-2026-13-45-0000z",          # month 13
    "cpc-btc-updown-15m-2026-10-26-0330",   # no trailing z
])
def test_slug_window_start_refuses_unparsable(bad):
    assert C.slug_window_start(bad) is None


# --------------------------------------------------------------- retention

def _tree(n: int, start: dt.datetime | None = None):
    root = tempfile.mkdtemp()
    wd = pathlib.Path(root) / "windows"
    base = start or dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    slugs = []
    for i in range(n):
        t = base + dt.timedelta(minutes=15 * i)
        sl = "cpc-btc-updown-15m-%s-%02d%02dz" % (
            t.strftime("%Y-%m-%d"), t.hour, t.minute)
        slugs.append(sl)
        (wd / sl).mkdir(parents=True)
        (wd / sl / "frames.jsonl").write_text("x\n")
    return root, slugs


def test_prune_drops_oldest_first_and_keeps_the_live_window():
    root, slugs = _tree(40)
    rep = C.prune_windows(root, retain=30, floor_mb=0.0,
                          keep_slugs=[slugs[-1]])
    # 39 directories are prunable (the live window is protected), so the
    # count bound drops 39 - 30 = 9, oldest first.
    assert rep["pruned_for_count"] == 9
    assert rep["pruned_for_floor"] == 0
    assert rep["pruned_slugs"] == slugs[:9]
    assert slugs[-1] not in rep["pruned_slugs"]
    assert (pathlib.Path(root) / "windows" / slugs[-1]).is_dir()


def test_prune_drops_unidentifiable_directories_first():
    """A directory whose slug carries no window start is the first thing we
    are willing to lose: it cannot be placed in the study."""
    root, slugs = _tree(30)
    (pathlib.Path(root) / "windows" / "not-a-window").mkdir()
    rep = C.prune_windows(root, retain=C.MIN_RETAIN_WINDOWS, floor_mb=0.0)
    assert rep["pruned_slugs"][0] == "not-a-window"


def test_prune_never_empties_the_volume_for_an_unreachable_floor():
    """The bug was a volume that filled; the cure must not be a volume that
    deletes everything trying to satisfy a floor it can never reach."""
    root, _ = _tree(40)
    rep = C.prune_windows(root, retain=1, floor_mb=10.0 ** 9)
    assert rep["floor_unreachable"] is True
    assert rep["windows_after"] == C.MIN_RETAIN_WINDOWS


def test_default_prune_deletes_no_history_when_space_is_adequate():
    """PHASE 2 REQUIREMENT: preserve historical data.

    The default must not be a count bound. A volume with plenty of free
    space holds months of window records, and switching on a count cap
    would discard them to make room for one more window. With the floor
    already satisfied, the default prune is a no-op.
    """
    root, slugs = _tree(40)
    rep = C.prune_windows(root)                 # no arguments: the default
    assert rep["count_bound_active"] is False
    assert rep["pruned_count"] == 0
    assert rep["windows_after"] == 40
    for sl in slugs:
        assert (pathlib.Path(root) / "windows" / sl).is_dir()


def test_floor_prune_removes_only_the_oldest_and_only_as_needed(monkeypatch):
    """Under space pressure it frees the minimum, oldest first -- not a
    wholesale cull."""
    root, slugs = _tree(40)
    calls = {"n": 0}

    # free space climbs past the floor after three deletions
    def fake_free(_path):
        return 100.0 + (50.0 * calls["n"])

    def fake_rmtree(p):
        calls["n"] += 1
        return True

    monkeypatch.setattr(C, "free_mb", fake_free)
    monkeypatch.setattr(C, "_rmtree", fake_rmtree)
    rep = C.prune_windows(root, floor_mb=200.0)
    assert rep["count_bound_active"] is False
    assert rep["pruned_for_count"] == 0
    # free goes 100 -> 150 -> 200; it stops the instant the floor clears
    assert rep["pruned_for_floor"] == 2         # the minimum that clears it
    assert rep["pruned_slugs"] == slugs[:2]     # oldest first
    assert rep["floor_unreachable"] is False


def test_prune_is_idempotent():
    root, slugs = _tree(40)
    first = C.prune_windows(root, retain=30, floor_mb=0.0)
    second = C.prune_windows(root, retain=30, floor_mb=0.0)
    assert first["pruned_count"] == 10
    assert second["pruned_count"] == 0
    assert second["windows_after"] == 30


def test_prune_reports_rather_than_raises_on_a_missing_root():
    rep = C.prune_windows("/nonexistent/path/for/test")
    assert rep["pruned_count"] == 0
    assert "error" not in rep or rep["pruned_count"] == 0


# ---------------------------------------------------- degraded WindowRecord

class _NoSpace:
    """Simulate a full volume. The production failure was ENOSPC out of
    `Path.mkdir`, which cannot be reproduced with file permissions in a
    container running as root."""

    def __init__(self, monkeypatch):
        self.mp = monkeypatch

    def __enter__(self):
        def boom(*_a, **_k):
            raise OSError(errno.ENOSPC, "No space left on device")
        self.mp.setattr(pathlib.Path, "mkdir", boom)
        return self

    def __exit__(self, *_a):
        return False


def test_degraded_record_does_not_raise_and_says_so(monkeypatch):
    """THE CORE REGRESSION. A full volume used to abort the window from its
    first statement, so the L5 feed below it was never polled."""
    root = tempfile.mkdtemp()
    with _NoSpace(monkeypatch):
        rec = C.WindowRecord(root, SLUG_0330, W0330)   # must not raise
    assert rec.degraded is True
    assert "ENOSPC" in rec.degraded_reason or "28" in rec.degraded_reason
    st = rec.storage()
    assert st["degraded"] is True
    assert st["raw_frames"] == 0


def test_degraded_record_keeps_the_interface_collect_one_uses(monkeypatch):
    """`collect_one` calls open_raw/sink/close_raw/write/raw_path. All of
    them must work on a degraded record, because the window must still be
    watched and still be summarised to the log."""
    root = tempfile.mkdtemp()
    with _NoSpace(monkeypatch):
        rec = C.WindowRecord(root, SLUG_0330, W0330)
    sink = rec.open_raw()
    sink({"frame": 1})
    sink({"frame": 2})
    rec.close_raw()
    assert rec.raw_frames == 0            # kept nothing, and admits it
    assert rec.write({"schema": "x"}) == ""   # no path, no exception
    assert rec.write_error is not None
    assert str(rec.raw_path).endswith("frames.jsonl")


def test_healthy_record_still_writes_and_reports_undegraded():
    """The degrade path must not change the healthy path."""
    root = tempfile.mkdtemp()
    rec = C.WindowRecord(root, SLUG_0330, W0330)
    assert rec.degraded is False
    sink = rec.open_raw()
    sink({"frame": 1})
    rec.close_raw()
    assert rec.raw_frames == 1
    path = rec.write({"schema": "l5_window_collector/v1"})
    assert path and json.load(open(path))["schema"] == "l5_window_collector/v1"
    assert rec.storage()["degraded"] is False


def test_record_degrades_mid_window_without_losing_the_window(monkeypatch):
    """The volume can fill between frames. Taping stops; watching does not."""
    root = tempfile.mkdtemp()
    rec = C.WindowRecord(root, SLUG_0330, W0330)
    sink = rec.open_raw()
    sink({"frame": 1})
    assert rec.raw_frames == 1

    class _FullFile:
        def write(self, _s):
            raise OSError(errno.ENOSPC, "No space left on device")

        def close(self):
            pass

    rec._raw = _FullFile()
    sink({"frame": 2})                  # must not raise
    assert rec.degraded is True
    assert rec.raw_frames == 1          # the honest count, not 2


# ============================================================ the executor
#
# `Runner.__init__` builds an authenticated book, a ledger, a watchdog
# client and a dozen self-checks, so these tests drive `Runner.poll` as an
# unbound method against a stand-in holding only the attributes it touches.
# That is deliberate: it pins the INGESTION contract without importing the
# money path, and it cannot arm, submit or cancel anything.

from src import l5_pmus_live_runner as R           # noqa: E402
from src import l5_pmus_realtime_observer as OBS   # noqa: E402


def _row(ts, slug="btc-updown-15m-%d" % int(W0330), *, tx="0xaa",
         side="BUY", price="0.47", size="12", asset="tok-up"):
    """An activity row in the shape `OBS.action_key` reads."""
    return {"transactionHash": tx, "asset": asset, "side": side,
            "price": price, "size": size, "timestamp": ts, "slug": slug}


class _Stub:
    """Only what `Runner.poll` reads. No transport, no executor."""

    def __init__(self, root, rows, *, max_age=900.0):
        self.cursor = R.IngestCursor(
            os.path.join(root, "l5_ingest_cursor.json")).load()
        self.max_action_age_s = max_age
        self.ingest_slugs = collections.Counter()
        self.stats = collections.Counter()
        self.rows = rows
        self.handled = []
        self.shadow_seen = []

    # the injected feed
    def feed(self):
        return self.rows

    def handle(self, r, now):
        self.handled.append(r)

    def shadow_compare(self, r, now):
        self.shadow_seen.append(r)

    # staticmethod() re-wraps it: accessing `Runner._action_age_s` off the
    # class yields the plain function, which would otherwise rebind as an
    # instance method here and receive `self`.
    _action_age_s = staticmethod(R.Runner._action_age_s)
    poll = R.Runner.poll
    ingest_report = R.Runner.ingest_report


# ------------------------------------------------------ restart durability

def test_restart_does_not_re_seed_and_does_not_discard_the_page():
    """THE CORE EXECUTOR REGRESSION.

    `seeded` was in-memory, so boot #2 marked the whole feed page as
    history again. Here boot #2 must resume: an action that arrived while
    the process was down is still on the page and must reach `handle`.
    """
    root = tempfile.mkdtemp()
    now = W0330 + 60.0
    history = [_row(now - 300, tx="0x%02d" % i) for i in range(5)]

    boot1 = _Stub(root, list(history))
    boot1.poll(now)
    assert boot1.handled == []                      # history, not a signal
    assert boot1.stats["seeded"] == 5
    assert boot1.cursor.seeded is True

    # L5 acts while we are restarting; the feed still serves the old rows
    fresh = _row(now + 5.0, tx="0xNEW")
    boot2 = _Stub(root, [fresh] + list(history))
    assert boot2.cursor.seeded is True              # resumed from the volume
    boot2.poll(now + 10.0)
    assert [r["transactionHash"] for r in boot2.handled] == ["0xNEW"]
    assert boot2.stats["seeded"] == 0               # never re-seeded
    assert boot2.stats["ingest:dup"] == 5           # history recognised


def test_one_l5_action_cannot_produce_two_executable_decisions():
    """Idempotency across a restart, which is the other half of resuming."""
    root = tempfile.mkdtemp()
    now = W0330 + 60.0
    seed = [_row(now - 300, tx="0xold")]
    _Stub(root, list(seed)).poll(now)               # boot 1: seed only

    act = _row(now + 1.0, tx="0xONCE")
    b2 = _Stub(root, [act] + seed)
    b2.poll(now + 2.0)
    assert len(b2.handled) == 1

    # same page, same process: a second poll must not re-handle it
    b2.poll(now + 3.0)
    assert len(b2.handled) == 1

    # and a restart must not re-handle it either
    b3 = _Stub(root, [act] + seed)
    b3.poll(now + 4.0)
    assert b3.handled == []
    assert b3.stats["ingest:dup"] == 2


def test_an_unreadable_cursor_reseeds_rather_than_replaying_history():
    """The safe direction: a missed action costs an opportunity, a replayed
    one costs money."""
    root = tempfile.mkdtemp()
    path = os.path.join(root, "l5_ingest_cursor.json")
    open(path, "w").write("{ this is not json")
    cur = R.IngestCursor(path).load()
    assert cur.seeded is False
    assert cur.load_error is not None
    assert cur.report()["durable"] is False


def test_cursor_survives_a_save_failure_without_raising(monkeypatch):
    root = tempfile.mkdtemp()
    cur = R.IngestCursor(os.path.join(root, "c.json"))
    cur.add("k", W0330)

    def boom(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(R.os, "replace", boom)
    cur.save()                                      # must not raise
    assert cur.save_error is not None
    assert cur.report()["durable"] is False
    assert cur.known("k") is True                   # in-process dedupe holds


def test_cursor_prunes_by_horizon_and_cap():
    root = tempfile.mkdtemp()
    cur = R.IngestCursor(os.path.join(root, "c.json"),
                         horizon_s=100.0, max_keys=1000)
    cur.add("old", W0330 - 10_000)
    cur.add("new", W0330)
    cur.save()
    reloaded = R.IngestCursor(os.path.join(root, "c.json")).load()
    assert reloaded.known("new") is True
    assert reloaded.known("old") is False           # beyond the horizon


# ------------------------------------------------------- ordering & filters

def test_actions_reach_handle_in_chronological_order():
    """The feed serves newest-first; the executor's inventory reasoning is
    sequential, so it must see L5's actions in the order L5 took them."""
    root = tempfile.mkdtemp()
    now = W0330 + 10.0
    _Stub(root, [_row(now - 500, tx="0xseed")]).poll(now)

    s = _Stub(root, [_row(now + 3, tx="0xC"),      # newest first, as served
                     _row(now + 1, tx="0xA"),
                     _row(now + 2, tx="0xB"),
                     _row(now - 500, tx="0xseed")])
    s.poll(now + 5.0)
    assert [r["transactionHash"] for r in s.handled] == ["0xA", "0xB", "0xC"]


def test_stale_actions_are_refused_with_a_machine_readable_reason():
    """A process that was down must not act on an hour-old action as though
    it had just happened -- that is backdating with real money."""
    root = tempfile.mkdtemp()
    now = W0330
    _Stub(root, [_row(now - 10_000, tx="0xseed")]).poll(now)

    s = _Stub(root, [_row(now - 5_000, tx="0xSTALE"),
                     _row(now - 10, tx="0xFRESH"),
                     _row(now - 10_000, tx="0xseed")], max_age=900.0)
    s.poll(now)
    assert [r["transactionHash"] for r in s.handled] == ["0xFRESH"]
    assert s.stats["ingest:refused:STALE_ACTION"] == 1
    assert s.stats["ingest:stale_max_age_s"] >= 5_000


def test_non_btc15m_rows_are_refused_and_counted_not_silently_dropped():
    """This is the counter whose absence made the 2026-10-26 campaign
    undiagnosable: 'L5 is trading other markets' must be establishable."""
    root = tempfile.mkdtemp()
    now = W0330 + 10.0
    # the seed row is foreign too, so rows_btc15m isolates the live page
    seed = _row(now - 500, tx="0xseed", slug="eth-updown-15m-000")
    _Stub(root, [seed]).poll(now)

    s = _Stub(root, [_row(now, tx="0xETH", slug="eth-updown-15m-123"),
                     _row(now, tx="0xNFL", slug="nfl-sf-sea-2026-10-26"),
                     seed])
    s.poll(now + 1.0)
    assert s.handled == []
    assert s.stats["ingest:refused:NOT_BTC15M_SLUG"] == 2

    rep = s.ingest_report()
    assert rep["rows_btc15m"] == 0
    assert rep["distinct_slugs_seen"] >= 2
    seen = {d["slug"] for d in rep["top_slugs_seen"]}
    assert "eth-updown-15m-123" in seen     # the evidence, not just a count


def test_rows_without_a_timestamp_are_refused_and_deduped():
    """An unparseable row must be refused AND remembered, or it is
    re-handled on every poll forever."""
    root = tempfile.mkdtemp()
    now = W0330
    _Stub(root, [_row(now - 500, tx="0xseed")]).poll(now)
    bad = _row(None, tx="0xBAD")
    s = _Stub(root, [bad, _row(now - 500, tx="0xseed")])
    s.poll(now)
    assert s.handled == []
    assert s.stats["ingest:refused:NO_TIMESTAMP"] == 1
    s.poll(now + 1.0)
    assert s.stats["ingest:refused:NO_TIMESTAMP"] == 1      # not re-refused
    assert s.stats["ingest:dup"] >= 1


def test_a_failed_feed_is_not_a_quiet_market():
    """`l5_activity` returns None on failure and [] when L5 simply has not
    traded. Collapsing them would make a broken feed look like silence."""
    root = tempfile.mkdtemp()
    s = _Stub(root, None)
    s.poll(W0330)
    assert s.stats["feed_errors"] == 1
    assert s.stats["ingest:FEED_FAILED"] == 1
    assert s.stats["ingest:polls_ok"] == 0
    assert s.cursor.seeded is False      # a failed read never seeds

    s2 = _Stub(root, [])
    s2.poll(W0330)
    assert s2.stats["ingest:polls_ok"] == 1
    assert s2.stats.get("feed_errors", 0) == 0


def test_ingest_report_is_compact_enough_to_survive_the_log_line_limit():
    """The heartbeat is truncated at 81,920 bytes in production and `stats`
    sits past the cut. This block is placed ahead of it, so it has to stay
    small even after thousands of distinct slugs."""
    root = tempfile.mkdtemp()
    s = _Stub(root, [])
    for i in range(5000):
        s.ingest_slugs["slug-%d" % i] += 1
    rep = s.ingest_report()
    assert len(s.ingest_slugs) == 5000
    assert len(rep["top_slugs_seen"]) == 6
    assert len(json.dumps(rep, default=str)) < 4096


def test_action_key_identity_is_the_economic_identity_not_the_tx_hash():
    """One transaction can carry two fills at different prices. Keying on
    the hash alone would silently drop the second."""
    a = _row(W0330, tx="0xsame", price="0.47")
    b = _row(W0330, tx="0xsame", price="0.48")
    assert OBS.action_key(a) != OBS.action_key(b)

    root = tempfile.mkdtemp()
    _Stub(root, [_row(W0330 - 500, tx="0xseed")]).poll(W0330)
    s = _Stub(root, [a, b, _row(W0330 - 500, tx="0xseed")])
    s.poll(W0330 + 1.0)
    assert len(s.handled) == 2


# ================================================== the end-to-end property
#
# THE ONE THAT MATTERS MOST. Everything above pins a piece; this pins the
# behaviour whose absence cost 17 days: a volume that refuses every write
# must not stop the wallet feed from being polled.

def test_a_full_volume_does_not_stop_the_wallet_poll(monkeypatch):
    root = tempfile.mkdtemp()
    monkeypatch.setenv("L5_COLLECTOR_ROOT", root)
    import scripts.l5_window_collector_run as S

    rows = [_row(W0330 + 10.0, slug="btc-updown-15m-%d" % int(W0330))]

    class _FakeFeed:
        """No socket, no network. The PMUS side is not what is under test."""

        def __init__(self, slug, sink):
            self.connected = False
            self.errors = []
            self.stop_reason = None
            self.book_states = []

        def start(self, cov):
            pass

        def stop(self):
            pass

        def prints(self):
            return []

        def market_state(self):
            return {"bid": "0.46", "ask": "0.48", "bid_qty": "5",
                    "ask_qty": "5", "state": "OPEN"}

    t = {"v": W0330 + 5.0}

    def clock():
        t["v"] += 400.0          # a couple of passes, then the window ends
        return t["v"]

    with _NoSpace(monkeypatch):
        doc = S.collect_one(SLUG_0330, W0330, clock=clock,
                            sleep=lambda _s: None,
                            activity=lambda limit=100: rows,
                            feed_factory=_FakeFeed)

    # the window was still summarised, and it says it kept nothing
    assert doc["storage"]["degraded"] is True
    assert "28" in doc["storage"]["degraded_reason"]
    assert doc["pmus"]["raw_frames"] == 0

    # AND THE FEED WAS POLLED. This is the regression.
    assert doc["l5"]["polls"] >= 1
    assert doc["l5"]["polls_ok"] >= 1
    health = doc["l5"]["health"]
    assert health["raw_rows"] >= 1
    assert health["rows_in_window"] >= 1

    # whatever refused the row did so with a machine-readable reason rather
    # than by vanishing: here the market binding is unfetchable offline, so
    # the identity gate fails closed and names itself.
    assert (health["identity_refused"] >= 1
            or health["new_actions"] >= 1)
    if health["identity_refused"]:
        assert health["identity_reasons"]          # named, not anonymous
