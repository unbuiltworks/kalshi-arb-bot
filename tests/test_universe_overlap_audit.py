from src.universe_overlap_audit import (
    KalshiRec,
    PolyRec,
    _compatible,
    _score,
    _words,
    _nums,
    _dates,
)


def _poly(text: str, category: str = "crypto") -> PolyRec:
    return PolyRec(
        slug="p",
        category=category,
        question=text,
        description="",
        title="",
        subtitle="",
        end_date="",
        text=text,
        tokens=_words(text),
        numbers=_nums(text),
        dates=_dates(text),
        volume=0,
        liquidity=0,
    )


def _kalshi(text: str, category: str = "crypto") -> KalshiRec:
    return KalshiRec(
        ticker="k",
        event_ticker="e",
        series_ticker="s",
        category=category,
        event_title=text,
        event_subtitle="",
        market_title="",
        yes_subtitle="",
        no_subtitle="",
        rules="",
        close_time="",
        text=text,
        tokens=_words(text),
        numbers=_nums(text),
        dates=_dates(text),
        volume=0,
    )


def test_category_mapping():
    assert _compatible("macro", "economics")
    assert _compatible("climate", "weather")
    assert not _compatible("crypto", "sports")


def test_similar_threshold_scores_high():
    p = _poly("Will Bitcoin be above 120000 on 2026-12-31?")
    k = _kalshi("Bitcoin price above 120000 on 2026-12-31")
    score, why = _score(p, k)
    assert score > 0.44
    assert "120000" in why["number_overlap"]
    assert "2026-12-31" in why["date_overlap"]


def test_conflicting_threshold_is_penalized():
    p = _poly("Will Bitcoin be above 120000?")
    k = _kalshi("Bitcoin price above 90000")
    score, _ = _score(p, k)
    assert score < 0.44
