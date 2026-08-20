import asyncio
import pytest
from src import gemini_kalshi_gemini_ws_recovery as recovery
class Reader:
    def __init__(self,done=False): self._done=done
    def done(self): return self._done
class GoodWS:
    async def ping(self):
        fut=asyncio.get_running_loop().create_future(); fut.set_result(None); return fut
class BadWS:
    async def ping(self): raise RuntimeError("socket closed")
class Session:
    def __init__(self,ws,*,row=None): self.ws=ws; self._reader=Reader(False); self.row=row
    async def _reconcile_rest(self,params): return self.row
PARAMS={"clientOrderId":"test-g","symbol":"GEMI-ETH15M-X-UP"}
@pytest.mark.asyncio
async def test_pre_send_socket_health_requires_reader_and_ping():
    assert await recovery.pre_send_socket_healthy(Session(GoodWS())) is True
    assert await recovery.pre_send_socket_healthy(Session(BadWS())) is False
    s=Session(GoodWS()); s._reader=Reader(True); assert await recovery.pre_send_socket_healthy(s) is False
@pytest.mark.asyncio
async def test_unhealthy_pre_send_socket_is_zero_fill_retry(monkeypatch):
    called=False
    async def original(*args,**kwargs):
        nonlocal called; called=True; raise AssertionError("must not enter order path")
    monkeypatch.setattr(recovery,"_ORIGINAL_PLACE_FOK",original)
    r=await recovery.resilient_place_fok(Session(BadWS()),PARAMS)
    assert called is False and r.status=="CANCELED" and r.filled_quantity=="0" and r.reason=="PRE_SEND_TRADING_WS_UNHEALTHY_RETRY_REQUIRED"
@pytest.mark.asyncio
async def test_post_send_socket_failure_without_reconciliation_is_unknown(monkeypatch):
    async def original(*args,**kwargs): raise RuntimeError("connection closed during send")
    monkeypatch.setattr(recovery,"_ORIGINAL_PLACE_FOK",original)
    r=await recovery.resilient_place_fok(Session(GoodWS(),row=None),PARAMS)
    assert r.status=="UNKNOWN" and r.filled_quantity=="0" and "AMBIGUOUS" in r.reason
@pytest.mark.asyncio
async def test_post_send_socket_failure_uses_exact_rest_reconciliation(monkeypatch):
    async def original(*args,**kwargs): raise RuntimeError("connection closed after send")
    monkeypatch.setattr(recovery,"_ORIGINAL_PLACE_FOK",original)
    row={"clientOrderId":"test-g","status":"filled","filledQuantity":"1","avgExecutionPrice":"0.41"}
    r=await recovery.resilient_place_fok(Session(GoodWS(),row=row),PARAMS)
    assert r.status=="FILLED" and r.filled_quantity=="1" and r.average_fill_price=="0.41" and r.reconciled is True
