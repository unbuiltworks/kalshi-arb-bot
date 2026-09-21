"""The narrow collector-hygiene patch.

Three separate defects, measured in production on deployment 6553d6dd over 80
minutes with ~40 watched tokens:

  * 2,888 `tokens_retired` and 8,724 `book_errors` -- a retire/re-add churn
    loop, because `refresh_habitat_for_settlement` re-adds every habitat token
    it does not already hold, including one retired seconds earlier;
  * `book_errors` counted rolled-over 404s together with genuine fetch
    failures, so the counter could not be read as health at all;
  * `intl_flow` had no equivalent of `pmus_flow_selected`, leaving a flat row
    counter ambiguous between "nothing matched" and "loop stuck" -- the exact
    ambiguity that cost two hours on pmus_flow.

None of these touches discovery semantics, ranking, L5 logic, execution logic
or the reconciliation hypothesis, and the last group of tests pins that.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import l5_prospective_recorder as R
from src import l5_live_habitat_service as S


class Gone(Exception):
    code = 404


class Sick(Exception):
    code = 503


# ------------------------------------------------ (b) error classification
def test_404_and_410_are_the_venue_working_not_a_health_signal():
    assert R.book_gone(Gone()) is True
    g = Exception(); g.code = 410
    assert R.book_gone(g) is True


def test_timeouts_5xx_and_resets_stay_countable_as_health():
    assert R.book_gone(Sick()) is False
    assert R.book_gone(TimeoutError()) is False
    assert R.book_gone(ConnectionResetError()) is False
    assert R.book_gone(Exception("no code at all")) is False


def test_the_two_classes_are_separate_counters(tmp_path, monkeypatch):
    rec = R.Recorder(str(tmp_path))
    rec.tokens = {"gone": {"token": "gone"}, "sick": {"token": "sick"}}
    def fake(url, timeout=20.0):
        raise Gone() if "gone" in url else Sick()
    monkeypatch.setattr(R, "_get", fake)
    rec.poll_books(workers=2)
    assert rec.counts["book_gone"] == 1
    assert rec.counts["book_errors"] == 1


def test_both_counters_exist_from_the_start_so_zero_is_not_missing(tmp_path):
    c = R.Recorder(str(tmp_path)).counts
    for k in ("book_errors", "book_gone", "tokens_retired",
              "habitat_readd_suppressed"):
        assert c[k] == 0


# -------------------------------------------------------- (a) the tombstone
def test_three_failures_retire_the_token_and_lay_a_tombstone(tmp_path, monkeypatch):
    rec = R.Recorder(str(tmp_path))
    rec.tokens = {"t": {"token": "t", "habitat": True}}
    monkeypatch.setattr(R, "_get", lambda *a, **k: (_ for _ in ()).throw(Gone()))
    for _ in range(3):
        rec.poll_books(workers=1)
    assert "t" not in rec.tokens
    assert rec.counts["tokens_retired"] == 1
    assert rec.tombstones["t"] > time.time()


def test_habitat_rediscovery_does_not_resurrect_a_tombstoned_token(tmp_path, monkeypatch):
    """THE CHURN LOOP. Without this the retirement is undone within 15s."""
    rec = R.Recorder(str(tmp_path))
    rec.tombstones["t"] = time.time() + R.TOMBSTONE_S
    monkeypatch.setattr(R, "discover_habitat",
                        lambda *a, **k: [{"token": "t", "slug": "s",
                                          "conditionId": "0xc"}])
    rec.refresh_habitat_for_settlement()
    assert "t" not in rec.tokens
    assert rec.counts["habitat_readd_suppressed"] == 1


def test_an_expired_tombstone_lets_the_token_back(tmp_path, monkeypatch):
    rec = R.Recorder(str(tmp_path))
    rec.tombstones["t"] = time.time() - 1.0
    monkeypatch.setattr(R, "discover_habitat",
                        lambda *a, **k: [{"token": "t", "slug": "s",
                                          "conditionId": "0xc"}])
    rec.refresh_habitat_for_settlement()
    assert "t" in rec.tokens
    assert rec.tombstones == {}


def test_a_token_that_answers_again_drops_its_tombstone(tmp_path, monkeypatch):
    """A market that recovers must not be held out for the rest of its window."""
    rec = R.Recorder(str(tmp_path))
    rec.tokens = {"t": {"token": "t"}}
    rec.tombstones["t"] = time.time() + R.TOMBSTONE_S
    monkeypatch.setattr(R, "_get", lambda *a, **k: {"bids": [], "asks": [],
                                                    "hash": "h"})
    rec.poll_books(workers=1)
    assert "t" not in rec.tombstones


def test_the_churn_loop_is_actually_closed_end_to_end(tmp_path, monkeypatch):
    """Retire, then rediscover repeatedly, and count the wasted fetches.

    Before the tombstone this sequence re-added the token every refresh and
    spent three more fetches per cycle on a book that is gone.
    """
    rec = R.Recorder(str(tmp_path))
    rec.tokens = {"t": {"token": "t", "habitat": True}}
    calls = {"n": 0}
    def fake(url, timeout=20.0):
        calls["n"] += 1
        raise Gone()
    monkeypatch.setattr(R, "_get", fake)
    monkeypatch.setattr(R, "discover_habitat",
                        lambda *a, **k: [{"token": "t", "slug": "s",
                                          "conditionId": "0xc"}])
    for _ in range(3):
        rec.poll_books(workers=1)
    assert calls["n"] == 3 and "t" not in rec.tokens
    for _ in range(20):                       # 20 rollover refreshes
        rec.refresh_habitat_for_settlement()
        rec.poll_books(workers=1)
    assert calls["n"] == 3, "the token came back and burned more fetches"
    assert rec.counts["tokens_retired"] == 1
    assert rec.counts["habitat_readd_suppressed"] == 20


def test_the_tombstone_outlives_any_updown_window():
    assert R.TOMBSTONE_S >= 900.0


# --------------------------------------------- (c) intl_flow disambiguation
def test_intl_flow_publishes_the_pmus_flow_selected_equivalent():
    import inspect
    src = inspect.getsource(S.intl_flow_loop)
    for k in ("intl_flow_sweeps", "intl_flow_selected", "intl_flow_conditions",
              "intl_flow_tape", "intl_flow_fetch_errors"):
        assert 'state["%s"]' % k in src, k


def test_a_swallowed_tape_failure_is_now_counted():
    import inspect
    src = inspect.getsource(S.intl_flow_loop)
    assert "errors += 1" in src


def test_the_heartbeat_emits_them():
    import inspect
    src = inspect.getsource(S.heartbeat_loop)
    for k in ("intl_flow_sweeps", "intl_flow_selected", "intl_flow_tape",
              "intl_flow_fetch_errors"):
        assert k in src, k


def test_sweeps_rise_while_selected_stays_zero_on_a_quiet_slate(monkeypatch):
    """The discrimination the patch exists for."""
    state = {"intl_cond": {}}
    calls = {"n": 0}
    def fake_get(url, timeout=25.0):
        calls["n"] += 1
        if calls["n"] > 3:
            raise KeyboardInterrupt
        return [{"conditionId": "0xUNWATCHED", "timestamp": 1, "size": "1",
                 "price": "0.5", "asset": "a", "transactionHash": "0x1"}]
    monkeypatch.setattr(S, "get", fake_get)
    monkeypatch.setattr(S, "INTL_FLOW_CAD", 0.0)
    with pytest.raises(KeyboardInterrupt):
        S.intl_flow_loop(state)
    assert state["intl_flow_sweeps"] == 3          # the loop ran
    assert state["intl_flow_selected"] == 0        # and matched nothing
    assert state["intl_flow_tape"] == 1            # against a served tape
    assert state["intl_flow_fetch_errors"] == 0


def test_a_watched_condition_is_written_and_counted(monkeypatch):
    state = {"intl_cond": {"0xC": {"slug": "s", "league": "l", "event": "e",
                                   "sports_type": None}}}
    calls = {"n": 0}
    def fake_get(url, timeout=25.0):
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt
        return [{"conditionId": "0xC", "timestamp": 1, "size": "3",
                 "price": "0.5", "asset": "a", "transactionHash": "0x1"}]
    monkeypatch.setattr(S, "get", fake_get)
    monkeypatch.setattr(S, "write", lambda *a, **k: None)
    monkeypatch.setattr(S, "INTL_FLOW_CAD", 0.0)
    with pytest.raises(KeyboardInterrupt):
        S.intl_flow_loop(state)
    assert state["intl_flow_selected"] == 1
    assert state["intl_flow_conditions"] == 1


# ------------------------------------- what this patch must NOT have changed
def test_discovery_semantics_are_untouched():
    import inspect
    src = inspect.getsource(S.pmus_discover)
    assert "livemap[e.get(\"slug\")] = (is_live" in src
    assert "return out, livemap" in src
    assert 'raise ValueError("no leagues discovered from /v2/sports")' in src


def test_market_ranking_is_untouched():
    # index into rank()'s own pattern tuple: moneyline 0, full_game_winner 1,
    # full_time_winner 2, match_winner 3, team_full_game_spread 4.
    assert S.rank("moneyline")[0] == 0
    assert S.rank("match_winner")[0] == 3
    assert S.rank("team_full_game_spread")[0] == 4
    assert S.rank("something_else")[0] == 99


def test_the_reconciliation_hypothesis_is_untouched():
    from src import l5_queue_reconciliation as Q
    assert Q.MIN_N_PER_MARKET == 200
    assert Q.VERDICTS == ("NO_COVERAGE", "NO_QUEUE", "ACCOUNTED_BY_TRADES",
                          "ACCOUNTED_WITH_CANCELS", "INCONCLUSIVE_GAP",
                          "UNEXPLAINED")


def test_habitat_tokens_are_still_never_pruned(tmp_path):
    rec = R.Recorder(str(tmp_path))
    rec.tokens = {"h%d" % i: {"token": "h%d" % i, "habitat": True}
                  for i in range(R.MAX_TOKENS + 5)}
    R.prune_tokens(rec)
    assert len(rec.tokens) == R.MAX_TOKENS + 5


def test_the_image_is_still_stdlib_only():
    import ast
    t = ast.parse(open("src/l5_prospective_recorder.py").read())
    mods = {a.name.split(".")[0] for n in ast.walk(t)
            if isinstance(n, ast.Import) for a in n.names}
    assert not (mods - set(sys.stdlib_module_names))
