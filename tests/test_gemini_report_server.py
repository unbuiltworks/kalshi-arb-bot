import json
import urllib.request

from src import gemini_l5_shadow_service as svc


def test_report_server_publishes_the_latest_rolling_report_read_only():
    calls = {"n": 0}

    def getter():
        calls["n"] += 1
        return {"t": "x", "research_arm_stochastic": {"windows_entered_unique": 67}}
    srv = svc.start_report_server(getter, port=0)
    try:
        port = srv.server_address[1]
        host = "[::1]" if srv.address_family.name == "AF_INET6" else "127.0.0.1"
        with urllib.request.urlopen(f"http://{host}:{port}/rolling", timeout=5) as r:
            body = json.loads(r.read().decode())
        assert body["research_arm_stochastic"]["windows_entered_unique"] == 67
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=5) as r:
            assert json.loads(r.read().decode()) == {"ok": True}
        try:
            urllib.request.urlopen(f"http://{host}:{port}/other", timeout=5)
            assert False, "unknown path must 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        assert calls["n"] == 1
    finally:
        srv.shutdown()
