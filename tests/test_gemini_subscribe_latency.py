"""The verification measure for the late-subscribe fix must not be the
clamped one. `tte_first_any` is capped at the window duration by
construction, so on a 5m window it can never exceed 300 however early we
attach -- using it to verify an earlier subscribe would silently cap the very
number under test."""
import importlib.util
import pathlib

_p = (pathlib.Path(__file__).resolve().parents[1]
      / "analysis" / "l5" / "gemini_trajectory" / "subscribe_latency.py")
_spec = importlib.util.spec_from_file_location("subscribe_latency", _p)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


def _digest(**over):
    d = {"market": "GEMI-BTC05M2609141200-UP", "asset": "BTC",
         "window_minutes": 5, "expiry_t": 1000.0, "expiry": 1000.0,
         "first_frame_at": 670.5, "tte_first_any": 300.0,
         "span_s": 295.0, "span_any_s": 299.0, "complete": True}
    d.update(over)
    return d


def test_first_frame_tte_is_unclamped_and_can_exceed_the_window_duration():
    d = _digest()
    assert d["tte_first_any"] == 300.0          # clamped, useless here
    assert S.first_frame_tte(d) == 329.5        # the real lead we achieved


def test_first_frame_tte_is_unknown_not_zero_when_a_stamp_is_missing():
    """0 would read as 'attached exactly at expiry' -- the worst possible
    result -- when the truth is that we do not know."""
    assert S.first_frame_tte(_digest(first_frame_at=None)) is None
    assert S.first_frame_tte(_digest(expiry=None, expiry_t=None)) is None


def test_first_frame_tte_falls_back_to_expiry_t_on_older_digests():
    """Digests written before the lifecycle fields carry expiry_t only."""
    d = _digest()
    d.pop("expiry")
    assert S.first_frame_tte(d) == 329.5


def test_late_attach_reports_a_smaller_lead_not_an_error():
    assert S.first_frame_tte(_digest(first_frame_at=752.35)) == 247.65


def test_iter_digests_accepts_prefixed_and_bare_lines_and_skips_noise():
    import json
    line = "GEMINI_WINDOW_CLOSE " + json.dumps(_digest())
    bare = json.dumps(_digest(market="GEMI-ETH05M2609141200-UP"))
    got = list(S.iter_digests([line, bare, "not json", "",
                               'GEMINI_WINDOW_SERIES {"market":"X","part":1}']))
    assert [g["market"] for g in got] == ["GEMI-BTC05M2609141200-UP",
                                          "GEMI-ETH05M2609141200-UP"]


def test_report_counts_windows_that_beat_the_full_requirement():
    rs = S.rows([_digest(), _digest(market="B", first_frame_at=752.35,
                                    complete=False)])
    out = S.report(rs)
    assert ">300 s (beats the FULL requirement): 1/2" in out
    assert "naturally COMPLETE (span_s >= dur-10): 1/2" in out
    # an UNKNOWN must be printed as UNKNOWN, never as a number
    assert "UNKNOWN" in S.report(S.rows([_digest(first_frame_at=None)]))
