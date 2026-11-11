"""Pre-entry habitat: measurement, not an admission rule.

The owner's instruction is explicit -- rank and segment first, do not invent a
threshold. So most of these tests are about what this module declines to
decide, and about the one denominator mistake that would make every number
wrong.
"""
import pytest

from src.gemini_execution import preentry as PE


def _flow(prints, *, readable=True, newest=None, oldest=None):
    return {"readable": readable, "n_prints": len(prints),
            "newest_utc_ms": newest, "oldest_utc_ms": oldest,
            "prints": prints}


def _p(ms, price="0.30", qty="1", side="buy"):
    return {"tid": ms, "utc_ms": ms, "price": price, "qty": qty, "side": side}


NOW = 1789800000000.0


# ============================================== the denominator that matters

def test_a_TWENTY_SECOND_OLD_CONTRACT_IS_NEW_NOT_QUIET():
    """THE MISTAKE THIS MODULE EXISTS TO AVOID. At TTE 280 the contract is
    ~20 s old, so two prints on its tape is a rate of 6/min, not evidence of a
    dead market. Dividing that count by a fixed 120 s window would report 1/min
    and mark every fresh contract as quiet -- which is every contract we would
    ever admit."""
    snap = PE.snapshot(
        symbol="S", tte_s=280.0, book={"readable": True, "yes": {}},
        depth=None, own_flow=_flow([_p(NOW - 5000), _p(NOW - 15000)],
                                   newest=NOW - 5000),
        prior_flow=None, spot_flow=None, now_ms=NOW)
    own = snap["flow_own"]
    assert own["age_s"] == 20.0
    assert own["prints_per_min_over_age"] == "6.000"
    assert "NEW, not" in own["why_age"]


def test_a_rate_over_an_unknown_denominator_is_not_a_rate():
    assert PE._rate_per_min(5, None) is None
    assert PE._rate_per_min(5, 0) is None
    assert PE._rate_per_min(None, 60) is None


# ================================================= unreadable is not zero

def test_an_unreadable_tape_is_not_a_quiet_one():
    got = PE.tape_window({"readable": False, "why": "timeout"})
    assert got["readable"] is False
    assert got["n_recent"] is None and got["prints_per_min"] is None
    assert got["since_last_print_s"] is None
    assert got["why"] == "timeout"


def test_an_unreadable_spot_tape_is_refused_not_guessed():
    assert PE.spot_movement(None)["readable"] is False
    assert PE.spot_movement(_flow([]))["readable"] is False


# ============================================== two-sided, where observable

def test_one_side_printing_is_not_a_two_sided_market():
    """A passive quote on the side nobody trades has no counterparty, so
    'there was flow' and 'there was flow on our side' are different facts."""
    got = PE.tape_window(_flow([_p(NOW - 1000, side="buy"),
                                _p(NOW - 2000, side="buy")], newest=NOW - 1000),
                         now_ms=NOW)
    assert got["two_sided"] is False
    assert got["side_balance"] == 0.0

    both = PE.tape_window(_flow([_p(NOW - 1000, side="buy"),
                                 _p(NOW - 2000, side="sell")],
                                newest=NOW - 1000), now_ms=NOW)
    assert both["two_sided"] is True and both["side_balance"] == 1.0


def test_time_since_last_print_comes_from_the_tape_not_from_absence():
    got = PE.tape_window(_flow([_p(NOW - 30000)], newest=NOW - 30000),
                         now_ms=NOW)
    assert got["since_last_print_s"] == 30.0


def test_only_prints_inside_the_recent_window_are_counted():
    old = _p(NOW - 600000)
    new = _p(NOW - 10000)
    got = PE.tape_window(_flow([new, old], newest=NOW - 10000), now_ms=NOW,
                         recent_s=120.0)
    assert got["n_total_on_tape"] == 2 and got["n_recent"] == 1


# ==================================================== spot movement

def test_spot_movement_reports_range_and_refuses_to_forecast():
    got = PE.spot_movement(_flow(
        [_p(NOW - 1000, price="100000"), _p(NOW - 60000, price="99900")],
        newest=NOW - 1000, oldest=NOW - 60000))
    assert got["readable"] is True
    assert got["range_abs"] == "100"
    assert got["low"] == "99900" and got["high"] == "100000"
    assert got["span_s"] == 59.0
    assert "NOT a forecast" in got["labels"]


# ==================================================== touch turnover

def test_a_book_that_never_moves_between_reads_is_reported_as_such():
    a = {"readable": True, "bids": {"best": "0.30", "best_qty": "10"}}
    got = PE.touch_turnover(a, dict(a))
    assert got["observable"] is True and got["changed"] is False


def test_touch_turnover_reports_the_move():
    a = {"readable": True, "bids": {"best": "0.30", "best_qty": "10"}}
    b = {"readable": True, "bids": {"best": "0.32", "best_qty": "4"}}
    got = PE.touch_turnover(a, b)
    assert got["bid_moved"] is True and got["bid_delta"] == "0.02"
    assert got["qty_delta"] == "-6"


def test_one_unreadable_ladder_makes_turnover_unobservable():
    a = {"readable": True, "bids": {"best": "0.30"}}
    assert PE.touch_turnover(a, None)["observable"] is False


# ============================================ NO ADMISSION RULE IN HERE

def test_the_module_defines_NO_flow_threshold():
    """The owner said: do not invent a flow threshold yet. A constant that
    a future reader could mistake for one is the thing to keep out."""
    import inspect
    src = inspect.getsource(PE)
    for bad in ("MIN_PRINTS", "FLOW_THRESHOLD", "MIN_FLOW", "ADMIT_IF",
                "MIN_PRINTS_PER_MIN"):
        assert bad not in src, bad
    assert "NOT an admission rule" in PE.rank_key.__doc__


def test_rank_key_is_ordinal_with_no_weights():
    """Sorting a report is not scoring a window. If this ever becomes a
    weighted sum, it has become a model nobody authorized."""
    busy = {"flow_prior_window": {"prints_per_min": "9", "two_sided": True},
            "flow_own": {"n_total_on_tape": 4},
            "spot": {"prints_per_min": "40"}}
    quiet = {"flow_prior_window": {"prints_per_min": "0", "two_sided": False},
             "flow_own": {"n_total_on_tape": 0},
             "spot": {"prints_per_min": "2"}}
    assert PE.rank_key(busy) > PE.rank_key(quiet)
    # unreadable sorts BELOW a genuine zero, and is not treated as zero
    unknown = {"flow_prior_window": {"prints_per_min": None},
               "flow_own": {}, "spot": {}}
    assert PE.rank_key(unknown) < PE.rank_key(quiet)


def test_the_selector_is_not_wired_into_eligibility():
    """Shadow first. `eligible_symbol` must not import or consult this."""
    import inspect

    from src.gemini_execution import run_continuous as RC
    src = inspect.getsource(RC.eligible_symbol)
    assert "preentry" not in src and "rank_key" not in src


# ===================================================== the distribution

def _snap(prior_ppm, own_n=0, spot_ppm="10", rng="5", spread="0.20",
          depth="10"):
    return {"flow_prior_window": {"prints_per_min": prior_ppm,
                                  "two_sided": bool(prior_ppm
                                                    and float(prior_ppm) > 0)},
            "flow_own": {"n_total_on_tape": own_n},
            "spot": {"prints_per_min": spot_ppm, "range_bps": rng},
            "spread": spread, "touch_depth": depth}


def test_dispersion_reports_the_distribution_not_the_mean():
    snaps = [_snap(str(x)) for x in (0, 0, 0, 0, 2, 4, 9, 18)]
    got = PE.dispersion(snaps)
    d = got["prior_prints_per_min"]
    assert d["n"] == 8 and d["min"] == 0.0 and d["max"] == 18.0
    assert d["zero_share"] == 0.5
    assert "mean" not in d
    assert got["windows"] == 8


def test_a_field_with_no_readable_observation_says_so():
    got = PE.dispersion([_snap(None), _snap(None)])
    assert got["prior_prints_per_min"]["n"] == 0
    assert "no readable observation" in got["prior_prints_per_min"]["why"]


# ====================================================== the real question

def test_NO_persistence_is_stated_below_the_pair_floor():
    """The question a selector lives or dies on, and the one most likely to be
    answered too early. Five windows cannot establish that a pre-entry read
    predicts anything."""
    pairs = [{"prior": i, "actual": i} for i in range(5)]
    got = PE.persistence(pairs)
    assert got["computable"] is False
    assert got["n_pairs"] == 5 and got["min_pairs"] == PE.MIN_PAIRS
    assert "are needed before" in got["why"]
    assert "spearman_rho" not in got


def test_persistence_is_a_rank_correlation_with_its_sample_size():
    pairs = [{"prior": i, "actual": i * 2} for i in range(PE.MIN_PAIRS)]
    got = PE.persistence(pairs)
    assert got["computable"] is True
    assert got["spearman_rho"] == 1.0
    assert "not causal" in got["labels"]
    assert "%d windows" % PE.MIN_PAIRS in got["labels"]


def test_a_flat_series_has_no_rank_correlation_rather_than_a_zero_one():
    pairs = [{"prior": 0, "actual": i} for i in range(PE.MIN_PAIRS)]
    got = PE.persistence(pairs)
    assert got["spearman_rho"] is None
    assert "no rank correlation exists" in got["why"]


def test_unreadable_pairs_are_dropped_not_zero_filled():
    pairs = ([{"prior": None, "actual": 5}] * 10
             + [{"prior": i, "actual": i} for i in range(PE.MIN_PAIRS)])
    got = PE.persistence(pairs)
    assert got["n_pairs"] == PE.MIN_PAIRS


# ========================================================= the snapshot

def test_the_snapshot_carries_every_field_the_owner_named():
    snap = PE.snapshot(
        symbol="GEMI-BTC05M2609191200-UP", tte_s=265.0,
        book={"readable": True, "yes": {"bid": "0.31", "ask": "0.58",
                                        "spread": "0.27"}},
        depth={"readable": True,
               "bids": {"best": "0.31", "best_qty": "483", "n_levels": 4,
                        "levels": [{"price": "0.31", "qty": "483"},
                                   {"price": "0.30", "qty": "10"}]},
               "asks": {"best": "0.58", "best_qty": "9", "n_levels": 1,
                        "levels": [{"price": "0.58", "qty": "9"}]}},
        own_flow=_flow([_p(NOW - 3000)], newest=NOW - 3000),
        prior_flow=_flow([_p(NOW - 9000, side="buy"),
                          _p(NOW - 12000, side="sell")], newest=NOW - 9000),
        spot_flow=_flow([_p(NOW - 1000, price="100000")], newest=NOW - 1000),
        crossed=False, now_ms=NOW)
    for field in ("spread", "touch_depth", "depth_within_1", "depth_within_2",
                  "depth_within_3", "crossed", "tte_s", "flow_own",
                  "flow_prior_window", "spot", "touch_turnover"):
        assert field in snap, field
    assert snap["readable"] is True
    assert snap["spread"] == "0.27" and snap["touch_depth"] == "483"
    assert snap["flow_prior_window"]["two_sided"] is True


def test_a_crossed_book_is_NOT_readable_pre_entry_either():
    snap = PE.snapshot(symbol="S", tte_s=260.0,
                       book={"readable": True, "yes": {"spread": "-0.30"}},
                       depth=None, own_flow=None, prior_flow=None,
                       spot_flow=None, crossed=True)
    assert snap["readable"] is False and snap["crossed"] is True


@pytest.mark.parametrize("bad", [None, "", 5, []])
def test_garbage_in_is_refused_not_guessed(bad):
    assert PE.tape_window(bad)["readable"] is False
    assert PE.spot_movement(bad)["readable"] is False


# ============================================ the soak records it, unused

def test_the_soak_takes_the_snapshot_and_does_not_act_on_it():
    """A selector cannot be evaluated by a run it is already steering. The
    soak must record the pre-entry read and then proceed identically."""
    import inspect

    from src.gemini_execution import shadow_soak as SS
    src = inspect.getsource(SS.one_window)
    assert "PE.read_snapshot" in src
    # the snapshot's value must not gate anything in the window
    assert "if pre" not in src and "pre[" not in src
    assert "rank_key" not in src


def test_the_soak_still_sends_nothing_with_the_snapshot_added():
    import inspect

    from src.gemini_execution import shadow_soak as SS
    src = inspect.getsource(SS)
    for bad in ("driver.place", "driver.cancel", "ws_session",
                "GeminiMakerSession"):
        assert bad not in src, bad
    assert '"mutations_sent": 0' in src


def test_the_pre_entry_read_cannot_stop_a_window():
    """Every endpoint it touches is wrapped: a pre-entry look that raises
    would stop a window it was only supposed to observe."""
    import inspect
    src = inspect.getsource(PE.read_snapshot)
    assert "_safe(" in src and "never raises" in src.lower()
    assert "Never raises" in PE.read_snapshot.__doc__


def test_realised_flow_refuses_a_window_with_no_adjacent_pair():
    got = PE.realised_window_flow([])
    assert got["measurable"] is False
    assert "one usable cycle has no flow measurement" in got["why"]


# =============== three defects the first seven snapshots exposed

def test_THE_PREDECESSORS_RATE_IS_OVER_ITS_OWN_LIFE_NOT_A_TRAILING_WINDOW():
    """By the time we read a candidate at TTE ~275, its predecessor expired
    about 300 s ago, so a trailing 120-second window over the predecessor's
    tape is ALWAYS empty. The first seven snapshots duly reported
    prior_prints_per_min 0.000 in five of seven windows -- an artifact of the
    window definition, not a quiet venue."""
    # four prints spread over 60 s, all of them ~300 s in the past
    old = NOW - 300000
    prints = [_p(old - i * 20000) for i in range(4)]
    flow = _flow(prints, newest=old, oldest=old - 60000)

    trailing = PE.tape_window(flow, now_ms=NOW, recent_s=120.0)
    assert trailing["n_recent"] == 0
    assert trailing["prints_per_min"] == "0.000"

    whole = PE.tape_window(flow, now_ms=NOW, whole_tape=True)
    assert whole["n_recent"] == 4
    assert whole["span_s"] == 60.0
    assert whole["prints_per_min"] == "4.000"
    assert whole["measured_over"] == "the tape's own span"


def test_the_reader_measures_the_predecessor_over_its_whole_tape():
    import inspect
    src = inspect.getsource(PE.read_snapshot)
    assert "prior_whole = True" in src
    assert "ALWAYS empty" in inspect.getsource(PE.tape_window)


def test_a_single_print_tape_has_no_span_and_therefore_no_rate():
    got = PE.tape_window(_flow([_p(NOW - 1000)], newest=NOW - 1000),
                         now_ms=NOW, whole_tape=True)
    assert got["span_s"] is None and got["prints_per_min"] is None


def test_SPREAD_IS_DERIVED_WHEN_THE_RAW_BOOK_DOES_NOT_CARRY_ONE():
    """The raw contract book has no `spread` key -- only the presented summary
    does -- so the first seven snapshots reported spread None beside a
    perfectly readable 0.32 / 0.66."""
    snap = PE.snapshot(symbol="S", tte_s=279.9,
                       book={"readable": True,
                             "yes": {"bid": "0.32", "ask": "0.66"}},
                       depth=None, own_flow=None, prior_flow=None,
                       spot_flow=None, crossed=False)
    assert snap["spread"] == "0.34"
    assert snap["spread_source"] == "derived from the observed bid and ask"


def test_a_book_that_carries_its_own_spread_is_used_as_given():
    snap = PE.snapshot(symbol="S", tte_s=279.9,
                       book={"readable": True,
                             "yes": {"bid": "0.32", "ask": "0.66",
                                     "spread": "0.34"}},
                       depth=None, own_flow=None, prior_flow=None,
                       spot_flow=None, crossed=False)
    assert snap["spread"] == "0.34" and snap["spread_source"] == "the book"


def test_a_one_sided_book_yields_no_derived_spread():
    snap = PE.snapshot(symbol="S", tte_s=279.9,
                       book={"readable": True, "yes": {"bid": "0.32"}},
                       depth=None, own_flow=None, prior_flow=None,
                       spot_flow=None, crossed=False)
    assert snap["spread"] is None


def test_THE_CONTRACT_AGE_IS_A_FLOOR_BECAUSE_PRINTS_PRECEDE_effectiveDate():
    """19 of 26 captured windows had their first print at TTE 375.9-378.2 --
    before the 300 s effective window. Seven captured 11:35-12:05Z had none
    before TTE 268.7. The tradeable life is NOT a constant, so the age is
    stated as a floor rather than as the contract's life."""
    snap = PE.snapshot(symbol="S", tte_s=280.0,
                       book={"readable": True, "yes": {}}, depth=None,
                       own_flow=_flow([_p(NOW - 5000)], newest=NOW - 5000),
                       prior_flow=None, spot_flow=None, now_ms=NOW)
    own = snap["flow_own"]
    assert own["age_s"] == 20.0
    assert own["age_is_a_floor"] is True
    assert "FLOOR" in own["why_age"] and "TTE ~377" in own["why_age"]
