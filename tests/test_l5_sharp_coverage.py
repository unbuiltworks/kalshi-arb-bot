"""OddsClient.coverage(): read-only provider sport list, tennis / table
tennis keys, configured keys offered or missing, measured quota cost; the
key never appears in the output."""
import io
import json

from src import pmus_sports_sharp as S


class _R(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(json.dumps(body).encode())
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client(body, headers, seen):
    def opener(url, timeout=10):
        seen.append(url)
        return _R(body, headers)
    return S.OddsClient(key="SECRETKEY", opener=opener)


def test_coverage_lists_tennis_and_reports_missing_configured_keys():
    seen = []
    body = [{"key": "tennis_atp_paris", "group": "Tennis"},
            {"key": "tennis_wta_finals", "group": "Tennis"},
            {"key": "basketball_nba", "group": "Basketball"}]
    c = _client(body, {"x-requests-remaining": "480",
                       "x-requests-used": "20"}, seen)
    c.used = 20
    out = c.coverage()
    assert out["ok"] and out["tennis"] == ["tennis_atp_paris",
                                           "tennis_wta_finals"]
    assert out["table_tennis"] == []
    assert "basketball_nba" in out["configured_offered"]
    assert "icehockey_nhl" in out["configured_missing"]
    assert out["list_cost"] == 0
    assert "SECRETKEY" not in json.dumps(out)
    assert seen and "/sports?" in seen[0] and "/odds" not in seen[0]


def test_coverage_without_key_or_on_error_fails_quietly():
    c = S.OddsClient(key="", opener=lambda *a, **k: None)
    assert c.coverage() == {"ok": False, "why": "NO_KEY",
                            "label": c.coverage()["label"]}

    def boom(url, timeout=10):
        raise OSError("down")
    c2 = S.OddsClient(key="K", opener=boom)
    out = c2.coverage()
    assert not out["ok"] and out["why"] == "OSError"
    assert "K" not in json.dumps({k: v for k, v in out.items()
                                  if k != "label"})
