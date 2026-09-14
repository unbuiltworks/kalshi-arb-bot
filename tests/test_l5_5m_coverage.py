"""The recorder must capture the CONTINUE states and never invent a number."""
from src.l5_5m_coverage import recorder as R


def test_windows_are_enumerated_so_late_subscribe_cannot_recur():
    """The Gemini collector lost 82 s/window to discovery. 5m windows sit on
    fixed 300 s epochs, so there is nothing to discover and capture opens a
    full PRE_ROLL_S before the window, by construction."""
    s = R.window_start(1789420000)
    assert s % R.WINDOW_S == 0 and s >= 1789420000
    assert R.due_at(s) == s - R.PRE_ROLL_S
    assert R.window_start(s) == s                      # already on a boundary
    assert R.window_start(s + 1) == s + R.WINDOW_S     # strictly after


def test_a_window_where_l5_does_nothing_is_still_a_full_observation():
    rec = R.sample(asset="btc", start=1789420200, now=1789420100.0,
                   book={"up_bid": 0.48, "up_ask": 0.52}, inv={},
                   flow={"l5_fill": False, "buy_qty": 0, "sell_qty": 0},
                   history=[])
    # a READ feed showing nothing is a real CONTINUE observation; an UNREAD
    # feed is not, and the two are deliberately different values here.
    assert rec["pre_roll"] is True and rec["l5_acted"] is False
    assert rec["two_sided"] is True and abs(rec["spread"] - 0.04) < 1e-9
    assert abs(rec["attainable_pair_cost"] - 0.96) < 1e-9
    assert rec["tte_s"] == 400.0


def test_a_one_sided_book_is_null_with_a_reason_never_a_zero_spread():
    rec = R.sample(asset="eth", start=1789420200, now=1789420300.0,
                   book={"up_bid": 0.48}, inv={"dn": 10, "cost_dn": 4.5},
                   flow=None, history=[])
    assert rec["spread"] is None and rec["spread_reason"] == R.R_NO_BOOK
    assert rec["attainable_pair_cost"] is None
    assert rec["mid"] is None
    # a zero spread would read as a locked market; a zero depth as an empty one
    assert rec["spread"] != 0 and rec["depth_bid"] is None


def test_returns_are_as_of_and_never_look_ahead():
    hist = [{"t": 1789420200.0 + i, "mid": 0.50 + 0.001 * i} for i in range(61)]
    rec = R.sample(asset="btc", start=1789420200, now=1789420260.0,
                   book={"up_bid": 0.55, "up_ask": 0.57}, inv={},
                   flow=None, history=hist)
    # mid now is 0.56; 60 s ago it was 0.50
    assert abs(rec["ret_60s"] - 0.06) < 1e-9
    assert abs(rec["ret_5s"] - 0.005) < 1e-9
    # a lag with no history yet is null WITH a reason, not 0
    rec2 = R.sample(asset="btc", start=1789420200, now=1789420201.0,
                    book={"up_bid": 0.55, "up_ask": 0.57}, inv={},
                    flow=None, history=hist[:1])
    assert rec2["ret_60s"] is None and rec2["ret_60s_reason"] == R.R_NO_HISTORY


def test_inventory_gives_matched_absolute_and_relative_residual():
    st = R.inventory_state(up=3.0, dn=10.0, cost_up=1.5, cost_dn=4.5,
                           complement_price=0.55)
    assert st["matched_qty"] == 3.0
    assert st["residual_abs"] == 7.0 and st["residual_signed"] == -7.0
    assert abs(st["residual_relative"] + 7 / 13) < 1e-9
    assert abs(st["held_avg_cost"] - 0.45) < 1e-9      # holds DOWN
    assert abs(st["held_pair_cost"] - 1.00) < 1e-9


def test_pair_undefined_before_a_first_leg_is_null_with_a_reason():
    st = R.inventory_state(0, 0, 0, 0, complement_price=0.55)
    assert st["held_avg_cost"] is None
    assert st["held_pair_cost"] is None
    assert st["held_pair_cost_reason"] == R.R_NO_HELD
    assert st["residual_relative"] is None


def test_digest_surfaces_a_collection_defect_rather_than_hiding_it():
    start = 1789420200
    samples = [R.sample(asset="btc", start=start, now=start - 120.0 + i,
                        book={"up_bid": 0.48, "up_ask": 0.52}, inv={},
                        flow=None, history=[]) for i in range(0, 420)]
    d = R.digest("btc", start, samples)
    assert d["first_sample_lead_s"] == 120.0          # early, as intended
    assert d["max_gap_s"] == 1.0
    assert d["pre_roll_samples"] == 120
    assert d["two_sided_frac"] == 1.0
    empty = R.digest("btc", start, [])
    assert empty["coverage"] is None and "never opened" in empty["coverage_reason"]


def test_identical_l5_fills_survive_dedup_but_overlap_does_not():
    """L5 genuinely repeats identical fills, so dedup by identity destroys real
    trades; summing overlapping pages double-counts them. Multiplicity is the
    MAX in any single response."""
    a, b = ("t1", 0.44, 10.0), ("t2", 0.45, 5.0)
    # 'a' twice in one response is two real fills; the overlap must not make four
    out = R.dedupe_fills([[a, a, b], [a, b, b]])
    assert out.count(a) == 2 and out.count(b) == 2
    assert R.dedupe_fills([[a], [a], [a]]).count(a) == 1      # pure overlap
    assert R.dedupe_fills([[a, a, a]]).count(a) == 3          # genuinely three


def test_source_and_collection_clocks_are_kept_separate():
    rec = R.sample(asset="btc", start=1789420200, now=1789420100.0,
                   book={"up_bid": 0.48, "up_ask": 0.52}, inv={}, flow=None,
                   history=[], t_source=1789420097.5)
    assert rec["t_collected"] == 1789420100.0
    assert rec["t_source"] == 1789420097.5
    assert abs(rec["source_age_s"] - 2.5) < 1e-9
    # no venue clock is null WITH a reason, never silently the collection clock
    rec2 = R.sample(asset="btc", start=1789420200, now=1789420100.0,
                    book={"up_bid": 0.48, "up_ask": 0.52}, inv={}, flow=None,
                    history=[])
    assert rec2["t_source"] is None
    assert rec2["t_source_reason"] == R.R_NO_SOURCE_TS
    assert rec2["source_age_s"] is None


def test_run_digest_reports_windows_and_first_last_timestamps():
    start = 1789420200
    mk = lambda st, n: [R.sample(asset="btc", start=st, now=st - 120.0 + i,
                                 book={"up_bid": 0.48, "up_ask": 0.52}, inv={},
                                 flow=None, history=[]) for i in range(n)]
    ds = [R.digest("btc", start, mk(start, 420)),
          R.digest("btc", start + 300, mk(start + 300, 420)),
          R.digest("btc", start + 600, [])]          # a missed window
    rd = R.run_digest(ds)
    assert rd["windows_captured"] == 2 and rd["windows_missed"] == 1
    assert rd["first_sample_utc"] == start - 120.0
    assert rd["last_sample_utc"] == start + 300 - 120.0 + 419
    assert rd["first_sample_lead_s_min"] == 120.0       # never negative
    # no L5 action is the CONTINUE class, not a failure
    assert rd["windows_with_no_l5_action"] == 2


def test_the_recorder_has_no_order_path_and_no_credential_read():
    """Structural, not a grep for the word 'order' in prose: the module may
    only import stdlib and its own recorder, may never read an env var that
    could be a credential, and may never call an HTTP verb other than GET."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("src/l5_5m_coverage/recorder.py", "src/l5_5m_coverage/run.py"):
        tree = ast.parse((root / name).read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {al.name.split(".")[0] for al in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        allowed = {"__future__", "argparse", "ast", "json", "os", "time",
                   "typing", "urllib", "pathlib", "src"}
        assert mods <= allowed, (name, mods - allowed)
        # CALLED names, not prose: the docstrings legitimately contain the
        # words "order" and "submits" while promising the opposite.
        called = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                called.add(f.attr if isinstance(f, ast.Attribute)
                           else getattr(f, "id", ""))
        for verb in ("place_limit_buy", "place_order", "submit", "cancel",
                     "sign", "post", "getenv"):
            assert verb not in called, (name, verb)
        src = (root / name).read_text()
        assert "pmus_spring" not in src and "os.environ" not in src
        # only GET reaches the network
        for verb in ('method="POST"', 'method="DELETE"', 'method="PUT"'):
            assert verb not in src
        for cred in ("PRIVATE_KEY", "API_KEY", "SECRET", "PASSPHRASE"):
            assert cred not in src, (name, cred)


def test_the_recorder_polls_l5s_wallet_not_his_handle():
    """The handle returns HTTP 400 and the fail-soft read turns that into a null
    sample, so every window logs l5_fills 0 and a BROKEN READ is indistinguishable
    from a quiet venue. Pin the address against the canonical config."""
    from src.l5_5m_coverage import run as RUN
    from src.polymarket_l5 import config
    assert RUN.L5 == config.TARGET_ADDRESS
    assert RUN.L5.startswith("0x") and len(RUN.L5) == 42
    assert RUN.L5_HANDLE not in RUN.ACTIVITY.format(user=RUN.L5)


def test_an_unread_l5_feed_is_not_reported_as_no_action():
    quiet = R.sample(asset="btc", start=1789420200, now=1789420300.0,
                     book={"up_bid": 0.48, "up_ask": 0.52}, inv={},
                     flow={"l5_fill": False, "buy_qty": 0, "sell_qty": 0},
                     history=[])
    assert quiet["l5_acted"] is False and quiet["l5_acted_reason"] is None
    broken = R.sample(asset="btc", start=1789420200, now=1789420300.0,
                      book={"up_bid": 0.48, "up_ask": 0.52}, inv={},
                      flow=None, history=[])
    assert broken["l5_acted"] is None          # NOT False
    assert "unread" in broken["l5_acted_reason"]
    d = R.digest("btc", 1789420200, [quiet, broken])
    assert d["l5_fills"] == 0 and d["samples_with_unread_l5_feed"] == 1
