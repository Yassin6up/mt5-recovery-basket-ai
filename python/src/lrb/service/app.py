"""FastAPI app: authenticated decisions + telemetry, read-only dashboard.

The service never places, modifies or closes orders.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError

from lrb.ai.decide import decide
from lrb.ai.model import LogisticModel, RulesModel
from lrb.core.types import Bar

from .schemas import DecisionRequest, DecisionResponse, Probability, TelemetryIn
from .security import REQUEST_ID_RE, request_canonical, response_canonical, sha256_hex, sign, verify
from .settings import Settings
from .store import Store

log = logging.getLogger("lrb.service")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_model(path: str | None):
    if path and Path(path).exists():
        return LogisticModel.load(path)
    return RulesModel()


def create_app(settings: Settings, model=None, clock: Callable[[], float] = time.time,
               store: Store | None = None) -> FastAPI:
    app = FastAPI(title="Limited Recovery Basket AI - decision service", version="0.1.0",
                  docs_url=None, redoc_url=None, openapi_url=None)
    st = store or Store(settings.db_path)
    mdl = model or load_model(settings.model_path)
    app.state.store, app.state.model, app.state.settings = st, mdl, settings

    def signed(status: int, request_id: str, payload: dict[str, Any] | str) -> Response:
        body = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
        raw = body.encode()
        ts = str(int(clock()))
        sig = sign(settings.hmac_secret, response_canonical(status, request_id, ts, raw))
        return Response(raw, status_code=status, media_type="application/json",
                        headers={"X-LRB-Request-Id": request_id, "X-LRB-Timestamp": ts, "X-LRB-Signature": sig})

    async def authenticate(request: Request) -> tuple[str, bytes]:
        body = await request.body()
        if len(body) > 256 * 1024:
            raise HTTPException(413, "body too large")
        h = request.headers
        if h.get("x-lrb-key-id") != settings.key_id:
            raise HTTPException(401, "unknown key id")
        rid, ts = h.get("x-lrb-request-id", ""), h.get("x-lrb-timestamp", "")
        if not REQUEST_ID_RE.match(rid):
            raise HTTPException(400, "bad request id")
        try:
            ts_f = float(ts)
        except ValueError:
            raise HTTPException(401, "bad timestamp")
        if abs(clock() - ts_f) > settings.max_skew_sec:
            raise HTTPException(401, "timestamp outside allowed skew (check time sync)")
        canon = request_canonical(request.method, request.url.path, ts, rid, body)
        if not verify(settings.hmac_secret, canon, h.get("x-lrb-signature", "")):
            raise HTTPException(401, "bad signature")
        return rid, body

    def replay_or_none(rid: str, endpoint: str, body: bytes) -> Response | None:
        prev = st.get_request(rid)
        if prev is None:
            return None
        if prev["body_sha256"] != sha256_hex(body) or prev["endpoint"] != endpoint:
            raise HTTPException(409, "request id reused with a different body")
        return signed(prev["status"], rid, prev["response_body"])  # idempotent replay

    @app.exception_handler(HTTPException)
    async def http_err(request: Request, exc: HTTPException):
        if exc.status_code in (401, 409, 413):
            st.log_error("auth", f"{exc.status_code} {exc.detail} path={request.url.path}")
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/health")
    def health():
        return {"status": "ok", "time_utc": iso(clock()), "model_version": mdl.version,
                "schema_version": st.schema_version()}

    @app.post("/v1/decision")
    async def decision(request: Request):
        rid, body = await authenticate(request)
        cached = replay_or_none(rid, "decision", body)
        if cached:
            return cached
        try:
            req = DecisionRequest.model_validate_json(body)
        except ValidationError as e:
            st.log_error("validation", str(e)[:500])
            raise HTTPException(422, "invalid request body")
        if req.request_id != rid:
            raise HTTPException(400, "request id mismatch")
        now = clock()
        if abs(now - req.sent_at_utc) > settings.max_skew_sec:
            raise HTTPException(400, "sent_at_utc outside allowed skew")
        if req.symbol not in settings.allowed_symbols:
            raise HTTPException(403, "symbol not allowed")
        if req.bars[-1].t + req.timeframe_sec > now + settings.max_skew_sec:
            raise HTTPException(400, "bars include a bar that has not closed yet")
        bars = [Bar(b.t, b.o, b.h, b.l, b.c) for b in req.bars]
        r = decide(kind=req.kind, proposed_direction=req.proposed_direction, bars=bars, period_sec=req.timeframe_sec,
                   spread=req.ask - req.bid, distance=req.distance, drawdown_pct=req.account.drawdown_pct, model=mdl,
                   now=now, ttl_sec=settings.decision_ttl_sec, max_data_age_sec=settings.max_data_age_sec,
                   leg_index=req.leg_index)
        prob = None
        if r.p_loss is not None and isinstance(mdl, LogisticModel):
            prob = Probability(p_basket_loss=round(r.p_loss, 4), outcome=mdl.outcome, horizon=mdl.horizon,
                               calibration="Platt scaling fitted on a validation period after the training period",
                               model_evaluation=mdl.meta.get("evaluation"))
        resp = DecisionResponse(
            request_id=rid, kind=req.kind, leg_index=req.leg_index, decision=r.decision, direction=r.direction,
            model_version=r.model_version, data_timestamp_utc=iso(r.data_timestamp_utc), expires_at_utc=iso(r.expires_at_utc),
            data_timestamp_utc_epoch=r.data_timestamp_utc, expires_at_utc_epoch=r.expires_at_utc,
            reason_codes=r.reason_codes, probability=prob,
        ).model_dump(exclude_none=True)
        payload = json.dumps(resp, separators=(",", ":"))
        if not st.put_request(rid, "decision", sha256_hex(body), 200, payload):
            cached = replay_or_none(rid, "decision", body)  # concurrent duplicate
            if cached:
                return cached
        st.log_decision(rid, req.model_dump(), resp)
        log.info("decision rid=%s kind=%s dir=%s -> %s %s", rid, req.kind, req.proposed_direction, r.decision,
                 ",".join(r.reason_codes))
        return signed(200, rid, payload)

    @app.post("/v1/telemetry")
    async def telemetry(request: Request):
        rid, body = await authenticate(request)
        cached = replay_or_none(rid, "telemetry", body)
        if cached:
            return cached
        try:
            t = TelemetryIn.model_validate_json(body)
        except ValidationError as e:
            st.log_error("validation", str(e)[:500])
            raise HTTPException(422, "invalid telemetry body")
        st.log_telemetry(t.terminal_id, t.model_dump())
        for err in t.errors:
            st.log_error("ea", err)
        payload = json.dumps({"ok": True})
        st.put_request(rid, "telemetry", sha256_hex(body), 200, payload)
        return signed(200, rid, payload)

    def dashboard_auth(request: Request) -> None:
        tok = settings.dashboard_token
        if tok:
            given = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or \
                request.query_params.get("token", "")
            import hmac as _h
            if not _h.compare_digest(given, tok):
                raise HTTPException(401, "dashboard token required")
            return
        host = request.client.host if request.client else ""
        try:
            ok = ipaddress.ip_address(host).is_loopback
        except ValueError:
            ok = host in ("testclient", "localhost")
        if not ok:
            raise HTTPException(403, "dashboard is loopback-only unless LRB_DASHBOARD_TOKEN is set")

    @app.get("/api/status")
    def status(request: Request):
        dashboard_auth(request)
        tel = st.latest_telemetry()
        now = clock()
        return {
            "time_utc": iso(now), "model_version": mdl.version,
            "telemetry_age_sec": None if not tel else round(now - tel["received_utc"], 1),
            "telemetry": tel, "decisions": st.recent("decisions", 15), "errors": st.recent("errors", 15),
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request):
        dashboard_auth(request)
        return HTMLResponse(DASHBOARD_HTML, headers={"Cache-Control": "no-store",
                                                     "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"})

    return app


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LRB Monitor</title>
<style>
:root{--bg:#0f1115;--fg:#e6e6e6;--mut:#9aa0a6;--card:#181b21;--ok:#3fb950;--bad:#f85149;--warn:#d29922}
body{margin:0;font:14px/1.4 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:1100px;margin:auto;padding:16px}
h1{font-size:18px;margin:0 0 4px}.mut{color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}
.card{background:var(--card);border-radius:8px;padding:10px}.card b{display:block;font-size:18px}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:8px;margin:8px 0 16px;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid #262a31;text-align:left}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.banner{padding:8px 10px;border-radius:6px;background:#3a2a00;color:#ffd479;margin:8px 0}
</style></head><body><main>
<h1>Limited Recovery Basket AI &mdash; read-only monitor</h1>
<div class="mut">Read-only. This page cannot trade. Emergency stop: see docs/OPERATIONS.md (kill switch).</div>
<div id="banner"></div>
<div class="grid" id="cards"></div>
<h2>Basket</h2><div id="basket"></div>
<h2>Recent AI decisions</h2><div id="dec"></div>
<h2>Execution / service errors</h2><div id="err"></div>
</main><script>
const tok=new URLSearchParams(location.search).get('token');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const f=(x,d=2)=>x==null?'-':Number(x).toFixed(d);
const t=e=>e?new Date(e*1000).toISOString().replace('T',' ').slice(0,19)+'Z':'-';
function table(rows,cols){if(!rows||!rows.length)return '<div class="mut">none</div>';
return '<table><tr>'+cols.map(c=>'<th>'+esc(c[0])+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(c[1](r))+'</td>').join('')+'</tr>').join('')+'</table>'}
async function load(){
 try{const r=await fetch('/api/status',{headers:tok?{'Authorization':'Bearer '+tok}:{}});
 if(!r.ok)throw new Error('HTTP '+r.status);const s=await r.json();const m=s.telemetry;
 let ban='';if(!m)ban='No telemetry received yet.';else if(s.telemetry_age_sec>60)ban='Telemetry is stale ('+s.telemetry_age_sec+'s). Check MT5 terminal / bridge EA.';
 if(m&&!m.is_demo)ban+=' LIVE ACCOUNT reported.';
 document.getElementById('banner').innerHTML=ban?'<div class="banner">'+esc(ban)+'</div>':'';
 if(!m)return;const b=m.basket;
 const cards=[['Equity',f(m.equity)+' '+m.currency],['Balance',f(m.balance)],['Margin level',m.margin_level?f(m.margin_level,0)+'%':'-'],
 ['Basket net P&L',f(b.net_pnl)],['Basket target / limit',f(b.target_money)+' / -'+f(b.risk_money)],
 ['Daily loss limit',m.daily_loss_limit_pct+'% (SOD '+f(m.sod_equity)+')'],['Max drawdown',m.max_drawdown_pct+'% (peak '+f(m.peak_equity)+')'],
 ['Halts',(m.daily_halt?'DAILY ':'')+(m.dd_halt?'DRAWDOWN ':'')+(m.kill!=='NONE'?'KILL:'+m.kill:'')||'none'],
 ['AI',m.ai_mode+' / '+m.ai_status],['Entries',m.entries_blocked_reason||'allowed'],['Account',m.is_demo?'DEMO':'LIVE'],['Updated',t(m.time_utc)]];
 document.getElementById('cards').innerHTML=cards.map(c=>'<div class="card"><span class="mut">'+esc(c[0])+'</span><b>'+esc(c[1])+'</b></div>').join('');
 document.getElementById('basket').innerHTML='<div class="mut">'+esc(b.basket_id||'-')+' phase='+esc(b.phase)+' dir='+esc(b.direction)+' upper='+esc(b.upper)+' lower='+esc(b.lower)+' stops='+esc(b.stop_low)+'/'+esc(b.stop_high)+' adding='+esc(b.adding_allowed)+' '+esc(b.block_reason)+'</div>'+
 table(b.legs,[['Leg',l=>l.index],['Side',l=>l.side],['Planned',l=>l.planned_volume],['Filled',l=>l.filled_volume],['Price',l=>l.fill_price],['State',l=>l.state],['Ticket',l=>l.ticket]]);
 document.getElementById('dec').innerHTML=table(s.decisions,[['Time',d=>t(d.created_utc)],['Kind',d=>d.kind],['Proposed',d=>d.proposed_direction],['Decision',d=>d.decision],['Model',d=>d.model_version],['Reasons',d=>d.reason_codes]]);
 document.getElementById('err').innerHTML=table(s.errors,[['Time',e=>t(e.created_utc)],['Source',e=>e.source],['Message',e=>e.message]]);
 }catch(e){document.getElementById('banner').innerHTML='<div class="banner">Status unavailable: '+esc(e.message)+'</div>'}}
load();setInterval(load,5000);
</script></body></html>"""
