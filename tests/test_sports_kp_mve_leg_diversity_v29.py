from types import SimpleNamespace

from src import sports_kp_live_combo_supervisor_v28 as v28


def _leg(n: int, i: int):
    return SimpleNamespace(event_ticker=f"E{n}-{i}", market_ticker=f"M{n}-{i}", side="yes")


def _row(n: int, i: int):
    return (i / 1000, tuple(_leg(n, i * 10 + j) for j in range(n)))


def test_leg_diversity_reserves_half_without_changing_row_payload(monkeypatch):
    raw = [_row(2, i) for i in range(12)] + [_row(3, 20), _row(4, 30), _row(5, 40)]
    monkeypatch.setattr(v28, "_ORIGINAL_CANDIDATES", lambda *a, **k: raw)

    out = v28._leg_count_diverse_candidates({}, {}, max_candidates=8, min_locked=0)

    assert len(out) == 8
    counts = [len(row[1]) for row in out]
    assert counts[:4] == [2, 3, 4, 5]
    assert counts.count(2) >= 4  # remaining slots still follow original best-first rank
    assert all(row in raw for row in out)


def test_leg_diversity_returns_short_original_unchanged(monkeypatch):
    raw = [_row(2, 1), _row(3, 2), _row(4, 3)]
    monkeypatch.setattr(v28, "_ORIGINAL_CANDIDATES", lambda *a, **k: raw)

    out = v28._leg_count_diverse_candidates({}, {}, max_candidates=8, min_locked=0)

    assert out == raw


def test_leg_diversity_dedupes_same_candidate(monkeypatch):
    a = _row(2, 1)
    raw = [a, a] + [_row(3, 2), _row(4, 3), _row(5, 4), _row(2, 5), _row(2, 6)]
    monkeypatch.setattr(v28, "_ORIGINAL_CANDIDATES", lambda *a, **k: raw)

    out = v28._leg_count_diverse_candidates({}, {}, max_candidates=6, min_locked=0)
    keys = [tuple(sorted(f"{leg.event_ticker}:{leg.market_ticker}:{leg.side}" for leg in row[1])) for row in out]

    assert len(keys) == len(set(keys))
