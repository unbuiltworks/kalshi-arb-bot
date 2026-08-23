from types import SimpleNamespace

from src import mlb_gk_final_lag_family_audit as audit


def _row(family: str, gside: str = "YES", kside: str = "YES"):
    return SimpleNamespace(
        family=family,
        player="Test Player",
        game_slug="mlb-aaa-bbb-2026-08-23",
        gemini_side=SimpleNamespace(side=gside, venue_market_id=f"G-{family}"),
        kalshi_side=SimpleNamespace(side=kside, venue_market_id=f"K-{family}"),
    )


def test_summarize_counts_exact_and_direct_without_authorizing():
    hot = {
        "hits": _row("baseball_player_hits"),
        "tb-direct": _row("baseball_player_total_bases"),
        "tb-inverse": _row("baseball_player_total_bases", gside="NO"),
        "so": _row("baseball_player_strikeouts"),
    }
    out = audit.summarize(hot)
    assert out["diagnostic_only"] is True
    assert out["order_entry"] is False
    assert out["certifies_execution"] is False
    assert out["families"]["baseball_player_total_bases"]["exact_overlap_keys"] == 2
    assert out["families"]["baseball_player_total_bases"]["native_direct_keys"] == 1
    assert out["families"]["baseball_player_strikeouts"]["native_direct_keys"] == 1
    for family in audit.AUDIT_FAMILIES:
        assert out["families"][family]["execution_eligible"] is False


def test_examples_are_bounded():
    hot = {f"k{i}": _row("baseball_player_total_bases") for i in range(10)}
    out = audit.summarize(hot)
    assert len(out["families"]["baseball_player_total_bases"]["examples"]) == 5
