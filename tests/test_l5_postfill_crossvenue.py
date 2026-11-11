"""Post-first-fill, both venues, in the same terms -- and no further."""
from decimal import Decimal as D

import src.l5_postfill_crossvenue as X


def _f(win, out, px, sh, ts, tte=None):
    return {"window_id": win, "outcome": out, "side": "BUY",
            "price": D(px), "shares": D(sh), "ts": D(ts), "tte_s": tte,
            "slug": "s"}


def test_the_first_fill_sets_the_held_side_and_the_rest_are_classified():
    seq = X.sequence([_f("w", "Up", "0.5", "10", "0"),
                      _f("w", "Up", "0.5", "10", "10"),
                      _f("w", "Down", "0.45", "20", "30")])
    assert seq["held_side"] == "Up"
    assert seq["extend_fills"] == 1
    assert seq["complement_fills"] == 1
    assert seq["reached_complement"] is True
    assert seq["seconds_to_first_complement"] == 30.0


def test_a_window_that_never_completes_says_so_rather_than_being_dropped():
    seq = X.sequence([_f("w", "Up", "0.5", "10", "0"),
                      _f("w", "Up", "0.5", "10", "10")])
    assert seq["reached_complement"] is False
    assert seq["seconds_to_first_complement"] is None
    assert seq["complement_fills"] == 0
    assert D(seq["residual_units"]) == D("20")


def test_extend_units_are_counted_only_BEFORE_the_first_complement():
    seq = X.sequence([_f("w", "Up", "0.5", "10", "0"),
                      _f("w", "Up", "0.5", "5", "5"),
                      _f("w", "Down", "0.5", "15", "10"),
                      _f("w", "Up", "0.5", "99", "20")])
    assert D(seq["extend_units_before_complement"]) == D("5")
    assert D(seq["extend_usd_before_complement"]) == D("2.5")


def test_matched_residual_and_basis_match_the_frozen_gemini_definitions():
    seq = X.sequence([_f("w", "Up", "0.40", "10", "0"),
                      _f("w", "Down", "0.50", "6", "10")])
    assert D(seq["matched_units"]) == D("6")
    assert D(seq["residual_units"]) == D("4")
    assert D(seq["effective_matched_pair_cost"]) == D("0.90")
    assert D(seq["gross_filled_usd"]) == D("7.0")
    assert D(seq["matched_usd"]) == D("5.4")
    assert D(seq["residual_usd"]) == D("1.6")


def test_a_single_fill_window_is_all_residual():
    seq = X.sequence([_f("w", "Down", "0.30", "10", "0")])
    assert seq["matched_units"] == "0"
    assert seq["matched_over_gross_usd"] == "0.0000"
    assert seq["residual_over_gross_usd"] == "1.0000"
    assert seq["effective_matched_pair_cost"] is None


def test_an_empty_window_refuses_rather_than_returning_zeroes():
    assert X.sequence([])["computable"] is False


def test_windows_are_sorted_so_the_first_fill_really_is_first():
    ws = X.windows([_f("w", "Down", "0.5", "1", "99"),
                    _f("w", "Up", "0.5", "1", "1")])
    assert [f["ts"] for f in ws["w"]] == [D("1"), D("99")]
    assert X.sequence(ws["w"])["held_side"] == "Up"


def test_the_census_reports_the_ratio_and_never_a_division_by_zero():
    seqs = [X.sequence([_f("a", "Up", "0.5", "10", "0"),
                        _f("a", "Up", "0.5", "10", "5")]),
            X.sequence([_f("b", "Up", "0.5", "10", "0")])]
    c = X.census(seqs)
    assert c["windows"] == 2
    assert c["complement_rate"] == 0.0
    assert c["extend_to_complement_fill_ratio"].startswith("INFINITE")
    assert c["single_fill_windows"] == 1


def test_THE_CONTRAST_NAMES_WHAT_IT_CANNOT_SEPARATE():
    """The observability is asymmetric. A row that cannot be compared has to
    say so rather than quietly reporting one side."""
    c = X.contrast({"complement_rate": 0.83,
                    "extend_to_complement_fill_ratio": "1.06"},
                   {"matched_pairs_over_initial_fills": 0.0556,
                    "extend_fills": 7, "complement_fills": 1})
    assert "quote persistence / queue behaviour" in c["not_separable"]
    assert "flow direction after the first fill" in c["not_separable"]
    assert "complement is reached at all" in c["separable"]
    for r in c["rows"]:
        if not r["separable"]:
            assert r.get("why"), r


def test_the_contrast_handles_a_gemini_side_with_no_complement_at_all():
    c = X.contrast({}, {"extend_fills": 7, "complement_fills": 0})
    row = [r for r in c["rows"] if r["candidate"] == "EXTEND per COMPLETE"][0]
    assert row["gemini"].startswith("INFINITE")


def test_loading_filters_to_one_asset_and_one_window_length(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(
        "asset,window_minutes,window_id,outcome,side,price,shares,"
        "timestamp,seconds_to_expiry,market_slug\n"
        "BTC,5,w1,Up,BUY,0.5,10,100,200,s\n"
        "BTC,15,w2,Up,BUY,0.5,10,100,200,s\n"
        "ETH,5,w3,Up,BUY,0.5,10,100,200,s\n", encoding="utf-8")
    got = X.load(str(p), asset="BTC", window_minutes="5")
    assert [g["window_id"] for g in got] == ["w1"]


def test_an_unreadable_row_is_skipped_not_defaulted(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(
        "asset,window_minutes,window_id,outcome,side,price,shares,"
        "timestamp,seconds_to_expiry,market_slug\n"
        "BTC,5,w1,Up,BUY,,10,100,200,s\n"
        "BTC,5,w2,Up,BUY,0.5,10,100,200,s\n", encoding="utf-8")
    assert [g["window_id"] for g in X.load(str(p))] == ["w2"]
