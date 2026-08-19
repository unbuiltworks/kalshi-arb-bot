import csv
import hmac
import io
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from psycopg.rows import dict_row
from pydantic import BaseModel, Field


DATABASE_URL = os.environ["DATABASE_URL"]
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "arb")
DASHBOARD_PASSWORD = os.environ["DASHBOARD_PASSWORD"]
LEDGER_INGEST_TOKEN = os.environ["LEDGER_INGEST_TOKEN"]
DAILY_TARGET = Decimal(os.getenv("DAILY_PROFIT_TARGET", "480"))
ET_TZ = "America/New_York"

app = FastAPI(title="ARB Command Center", docs_url=None, redoc_url=None)
security = HTTPBasic()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trade_events (
    event_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('SHADOW','PAPER','LIVE_PROBE','LIVE')),
    market TEXT,
    asset TEXT,
    venues TEXT[],
    side TEXT,
    status TEXT,
    quantity NUMERIC,
    price NUMERIC,
    amount NUMERIC,
    fee NUMERIC NOT NULL DEFAULT 0,
    gross_pnl NUMERIC,
    net_pnl NUMERIC,
    legs_required INTEGER,
    legs_filled INTEGER,
    opportunity_net_profit NUMERIC,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trade_events_trade_id ON trade_events(trade_id, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_trade_events_strategy_ts ON trade_events(strategy, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_trade_events_mode_ts ON trade_events(mode, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_trade_events_type_ts ON trade_events(event_type, event_ts DESC);
"""


class TradeEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    trade_id: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=80)
    event_ts: datetime
    strategy: str = Field(min_length=1, max_length=120)
    mode: str
    market: str | None = None
    asset: str | None = None
    venues: list[str] | None = None
    side: str | None = None
    status: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    fee: Decimal = Decimal("0")
    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    legs_required: int | None = None
    legs_filled: int | None = None
    opportunity_net_profit: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def require_dashboard_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    good_user = hmac.compare_digest(credentials.username.encode(), DASHBOARD_USERNAME.encode())
    good_pass = hmac.compare_digest(credentials.password.encode(), DASHBOARD_PASSWORD.encode())
    if not (good_user and good_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def require_ingest_token(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {LEDGER_INGEST_TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid ingest token")


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


@app.on_event("startup")
def startup() -> None:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


@app.get("/health")
def health() -> dict[str, str]:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
    return {"status": "ok"}


@app.post("/api/events")
def ingest_event(event: TradeEvent, _: None = Depends(require_ingest_token)) -> dict[str, Any]:
    if event.mode not in {"SHADOW", "PAPER", "LIVE_PROBE", "LIVE"}:
        raise HTTPException(status_code=422, detail="Invalid mode")

    # P&L is only authoritative on one explicit realization event. This prevents
    # signal/shadow/projected P&L from leaking into realized accounting.
    gross_pnl = event.gross_pnl if event.event_type == "TRADE_REALIZED" else None
    net_pnl = event.net_pnl if event.event_type == "TRADE_REALIZED" else None

    sql = """
    INSERT INTO trade_events (
        event_id, trade_id, event_type, event_ts, strategy, mode, market, asset,
        venues, side, status, quantity, price, amount, fee, gross_pnl, net_pnl,
        legs_required, legs_filled, opportunity_net_profit, metadata
    ) VALUES (
        %(event_id)s, %(trade_id)s, %(event_type)s, %(event_ts)s, %(strategy)s,
        %(mode)s, %(market)s, %(asset)s, %(venues)s, %(side)s, %(status)s,
        %(quantity)s, %(price)s, %(amount)s, %(fee)s, %(gross_pnl)s, %(net_pnl)s,
        %(legs_required)s, %(legs_filled)s, %(opportunity_net_profit)s,
        %(metadata)s::jsonb
    ) ON CONFLICT (event_id) DO NOTHING
    RETURNING event_id
    """
    payload = event.model_dump()
    payload["gross_pnl"] = gross_pnl
    payload["net_pnl"] = net_pnl
    payload["metadata"] = json.dumps(event.metadata, separators=(",", ":"), default=str)

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, payload)
            inserted = cur.fetchone()
        conn.commit()

    return {"ok": True, "duplicate": inserted is None, "event_id": event.event_id}


TRADE_ROLLUP_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (trade_id)
        trade_id, strategy, mode, market, asset, venues, side, status,
        event_ts AS latest_event_ts, legs_required, legs_filled, metadata
    FROM trade_events
    ORDER BY trade_id, event_ts DESC, inserted_at DESC
), agg AS (
    SELECT
        trade_id,
        MIN(event_ts) FILTER (WHERE event_type='ORDER_SUBMITTED') AS submitted_at,
        MIN(event_ts) FILTER (WHERE event_type='FILL') AS first_fill_at,
        MAX(event_ts) FILTER (WHERE event_type='TRADE_REALIZED') AS realized_at,
        COALESCE(SUM(amount) FILTER (WHERE event_type='FILL'), 0) AS capital_filled,
        COALESCE(SUM(fee), 0) AS fees,
        MAX(gross_pnl) FILTER (WHERE event_type='TRADE_REALIZED') AS gross_pnl,
        MAX(net_pnl) FILTER (WHERE event_type='TRADE_REALIZED') AS net_pnl,
        BOOL_OR(event_type='TRADE_REALIZED') AS realized,
        MAX(legs_required) AS max_legs_required,
        MAX(legs_filled) AS max_legs_filled
    FROM trade_events
    GROUP BY trade_id
)
SELECT
    l.trade_id, l.strategy, l.mode, l.market, l.asset, l.venues, l.side,
    l.status, l.latest_event_ts, a.submitted_at, a.first_fill_at, a.realized_at,
    a.capital_filled, a.fees, a.gross_pnl, a.net_pnl, a.realized,
    COALESCE(l.legs_required, a.max_legs_required) AS legs_required,
    COALESCE(l.legs_filled, a.max_legs_filled) AS legs_filled,
    l.metadata
FROM latest l
JOIN agg a USING (trade_id)
"""


@app.get("/api/trades")
def trades(
    limit: int = Query(default=100, ge=1, le=1000),
    include_shadow: bool = False,
    _: str = Depends(require_dashboard_auth),
) -> list[dict[str, Any]]:
    where = "" if include_shadow else " WHERE mode IN ('LIVE','LIVE_PROBE')"
    sql = f"SELECT * FROM ({TRADE_ROLLUP_SQL}) t{where} ORDER BY latest_event_ts DESC LIMIT %s"
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
    return [jsonable(r) for r in rows]


@app.get("/api/summary")
def summary(_: str = Depends(require_dashboard_auth)) -> dict[str, Any]:
    sql = f"""
    WITH rolled AS ({TRADE_ROLLUP_SQL}),
    today_realized AS (
        SELECT * FROM rolled
        WHERE mode IN ('LIVE','LIVE_PROBE')
          AND realized
          AND (realized_at AT TIME ZONE '{ET_TZ}')::date = (NOW() AT TIME ZONE '{ET_TZ}')::date
    ),
    today_fills AS (
        SELECT COALESCE(SUM(amount),0) AS wagered
        FROM trade_events
        WHERE mode IN ('LIVE','LIVE_PROBE')
          AND event_type='FILL'
          AND (event_ts AT TIME ZONE '{ET_TZ}')::date = (NOW() AT TIME ZONE '{ET_TZ}')::date
    ),
    open_live AS (
        SELECT COALESCE(SUM(capital_filled),0) AS exposure
        FROM rolled
        WHERE mode IN ('LIVE','LIVE_PROBE') AND NOT realized AND capital_filled > 0
    ),
    exec_opp AS (
        SELECT COALESCE(SUM(opportunity_net_profit),0) AS executable_profit
        FROM trade_events
        WHERE mode IN ('LIVE','LIVE_PROBE')
          AND event_type='EXECUTABLE_OPPORTUNITY'
          AND status='EXECUTABLE'
          AND (event_ts AT TIME ZONE '{ET_TZ}')::date = (NOW() AT TIME ZONE '{ET_TZ}')::date
    )
    SELECT
        COALESCE((SELECT SUM(net_pnl) FROM today_realized),0) AS realized_net_pnl,
        COALESCE((SELECT SUM(gross_pnl) FROM today_realized),0) AS realized_gross_pnl,
        COALESCE((SELECT SUM(fees) FROM today_realized),0) AS realized_trade_fees,
        COALESCE((SELECT wagered FROM today_fills),0) AS capital_wagered,
        COALESCE((SELECT exposure FROM open_live),0) AS open_exposure,
        COALESCE((SELECT COUNT(*) FROM today_realized),0) AS trades_realized,
        COALESCE((SELECT COUNT(*) FROM today_realized WHERE net_pnl > 0),0) AS wins,
        COALESCE((SELECT COUNT(*) FROM today_realized WHERE net_pnl < 0),0) AS losses,
        COALESCE((SELECT COUNT(*) FROM today_realized WHERE net_pnl = 0),0) AS pushes,
        COALESCE((SELECT executable_profit FROM exec_opp),0) AS executable_opportunity_profit
    """
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            cur.execute(
                f"""
                SELECT strategy, COALESCE(SUM(net_pnl),0) AS net_pnl, COUNT(*) AS trades
                FROM ({TRADE_ROLLUP_SQL}) t
                WHERE mode IN ('LIVE','LIVE_PROBE') AND realized
                  AND (realized_at AT TIME ZONE '{ET_TZ}')::date = (NOW() AT TIME ZONE '{ET_TZ}')::date
                GROUP BY strategy ORDER BY net_pnl DESC
                """
            )
            by_strategy = cur.fetchall()

    realized = Decimal(str(row["realized_net_pnl"] or 0))
    wagered = Decimal(str(row["capital_wagered"] or 0))
    row["net_roi_pct"] = (realized / wagered * 100) if wagered else Decimal("0")
    row["daily_target"] = DAILY_TARGET
    row["target_progress_pct"] = (realized / DAILY_TARGET * 100) if DAILY_TARGET else Decimal("0")
    row["remaining_to_target"] = max(DAILY_TARGET - realized, Decimal("0"))
    row["by_strategy"] = by_strategy
    row["as_of"] = datetime.now(timezone.utc)
    return jsonable(row)


@app.get("/api/export.csv")
def export_csv(_: str = Depends(require_dashboard_auth)) -> StreamingResponse:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM ({TRADE_ROLLUP_SQL}) t ORDER BY latest_event_ts DESC")
            rows = cur.fetchall()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=arb-trades.csv"})


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARB Command Center</title>
<style>
:root{color-scheme:dark;--bg:#0b0d10;--panel:#12161b;--muted:#8f9ba8;--text:#eef2f6;--line:#222a33;--good:#47d18c;--bad:#ff6b6b;--warn:#f4c95d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1500px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:22px}h1{font-size:28px;margin:0}.sub{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(6,minmax(150px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:25px;font-weight:750;margin-top:5px}.good{color:var(--good)}.bad{color:var(--bad)}.progress{height:10px;background:#1b222a;border-radius:999px;overflow:hidden;margin-top:10px}.bar{height:100%;background:var(--good);width:0}.section{margin-top:22px;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}.sectionhead{padding:15px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}.sectionhead h2{font-size:16px;margin:0}table{width:100%;border-collapse:collapse}th,td{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}tr:last-child td{border-bottom:0}.scroll{overflow:auto}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#1b222a;color:#cbd5df;font-size:11px}.toolbar{display:flex;gap:8px;align-items:center}button,a.btn{border:1px solid var(--line);background:#171c22;color:var(--text);padding:8px 10px;border-radius:9px;text-decoration:none;cursor:pointer}.empty{padding:36px;color:var(--muted);text-align:center}.small{font-size:12px;color:var(--muted)}@media(max-width:1100px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.wrap{padding:16px}.cards{grid-template-columns:repeat(2,1fr)}.top{align-items:start;flex-direction:column}}
</style>
</head>
<body><div class="wrap">
<div class="top"><div><h1>ARB Command Center</h1><div class="sub">Realized means filled + settled. Shadow and paper never count toward live P&amp;L.</div></div><div class="toolbar"><span id="asof" class="small"></span><a class="btn" href="/api/export.csv">Export CSV</a><button onclick="load()">Refresh</button></div></div>
<div class="cards">
<div class="card"><div class="label">Realized P&amp;L Today</div><div id="pnl" class="value">—</div><div class="progress"><div id="bar" class="bar"></div></div><div id="target" class="small"></div></div>
<div class="card"><div class="label">Capital Wagered</div><div id="wagered" class="value">—</div></div>
<div class="card"><div class="label">Open Exposure</div><div id="exposure" class="value">—</div></div>
<div class="card"><div class="label">Realized Trades</div><div id="trades" class="value">—</div><div id="record" class="small"></div></div>
<div class="card"><div class="label">Net ROI</div><div id="roi" class="value">—</div></div>
<div class="card"><div class="label">Executable Opportunity</div><div id="opp" class="value">—</div><div class="small">Qualified events only; not counted as profit</div></div>
</div>
<div class="section"><div class="sectionhead"><h2>Today by strategy</h2></div><div id="strategies"></div></div>
<div class="section"><div class="sectionhead"><h2>Live trades</h2><div class="small">LIVE + LIVE_PROBE only</div></div><div class="scroll"><table><thead><tr><th>Time</th><th>Strategy</th><th>Market</th><th>Mode</th><th>Status</th><th>Legs</th><th>Capital</th><th>Fees</th><th>Net P&amp;L</th></tr></thead><tbody id="rows"></tbody></table><div id="empty" class="empty" style="display:none">No live/probe trade events have been ingested yet.</div></div></div>
</div>
<script>
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2}).format(Number(n||0));
const pct=n=>`${Number(n||0).toFixed(2)}%`;
const cls=n=>Number(n)>0?'good':Number(n)<0?'bad':'';
async function load(){
 const [s,t]=await Promise.all([fetch('/api/summary').then(r=>r.json()),fetch('/api/trades?limit=200').then(r=>r.json())]);
 const pnl=document.getElementById('pnl');pnl.textContent=money(s.realized_net_pnl);pnl.className='value '+cls(s.realized_net_pnl);
 document.getElementById('wagered').textContent=money(s.capital_wagered);document.getElementById('exposure').textContent=money(s.open_exposure);
 document.getElementById('trades').textContent=s.trades_realized;document.getElementById('record').textContent=`${s.wins} W / ${s.losses} L / ${s.pushes} P`;
 document.getElementById('roi').textContent=pct(s.net_roi_pct);document.getElementById('opp').textContent=money(s.executable_opportunity_profit);
 document.getElementById('target').textContent=`${money(s.realized_net_pnl)} / ${money(s.daily_target)} • ${money(s.remaining_to_target)} remaining`;
 document.getElementById('bar').style.width=Math.max(0,Math.min(100,Number(s.target_progress_pct||0)))+'%';
 document.getElementById('asof').textContent='Updated '+new Date(s.as_of).toLocaleTimeString();
 const st=document.getElementById('strategies');st.innerHTML=s.by_strategy.length?`<table><thead><tr><th>Strategy</th><th>Trades</th><th>Net P&L</th></tr></thead><tbody>${s.by_strategy.map(x=>`<tr><td>${x.strategy}</td><td>${x.trades}</td><td class="${cls(x.net_pnl)}">${money(x.net_pnl)}</td></tr>`).join('')}</tbody></table>`:'<div class="empty">No realized live trades today.</div>';
 const rows=document.getElementById('rows');rows.innerHTML=t.map(x=>`<tr><td>${new Date(x.latest_event_ts).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</td><td>${x.strategy}</td><td>${x.market||x.asset||'—'}</td><td><span class="pill">${x.mode}</span></td><td>${x.realized?'REALIZED':(x.status||'OPEN')}</td><td>${x.legs_filled??'—'}/${x.legs_required??'—'}</td><td>${money(x.capital_filled)}</td><td>${money(x.fees)}</td><td class="${cls(x.net_pnl)}">${x.realized?money(x.net_pnl):'—'}</td></tr>`).join('');
 document.getElementById('empty').style.display=t.length?'none':'block';
}
load();setInterval(load,15000);
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard(_: str = Depends(require_dashboard_auth)) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)
