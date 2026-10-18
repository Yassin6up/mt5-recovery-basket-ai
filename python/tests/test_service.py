"""Integration tests for the decision service (auth, replay, validation, fail-safe semantics)."""

import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lrb.ai.model import LogisticModel, RulesModel
from lrb.service.app import create_app
from lrb.service.security import request_canonical, response_canonical, sign, verify
from lrb.service.settings import Settings
from lrb.service.store import Store

SECRET = b"x" * 40
NOW = 1_760_003_600.0


def make_bars(n=150, tf=900, start_price=1.1, trend=0.00005):
    last_open = int(NOW) // tf * tf - tf  # most recent fully closed bar
    t0 = last_open - (n - 1) * tf
    bars, p = [], start_price
    for i in range(n):
        o = p
        c = p + trend + (0.0001 if i % 3 == 0 else -0.00005)
        bars.append({"t": t0 + i * tf, "o": o, "h": max(o, c) + 0.0002, "l": min(o, c) - 0.0002, "c": c})
        p = c
    return bars


def body(rid="req-000001", **kw):
    d = {
        "request_id": rid, "kind": "ENTRY", "symbol": "EURUSD", "magic": 1, "proposed_direction": "BUY",
        "leg_index": 0, "sent_at_utc": NOW, "timeframe_sec": 900, "bid": 1.1075, "ask": 1.1076,
        "distance": 0.002, "account": {"equity": 10000, "balance": 10000, "margin": 0, "drawdown_pct": 0.0},
        "bars": make_bars(),
    }
    d.update(kw)
    return json.dumps(d).encode()


@pytest.fixture
def client():
    s = Settings(hmac_secret=SECRET, db_path=":memory:")
    app = create_app(s, model=RulesModel(), clock=lambda: NOW, store=Store(":memory:"))
    return TestClient(app)


def post(client, path, raw, rid="req-000001", ts=None, secret=SECRET, key="ea1"):
    ts = str(int(NOW if ts is None else ts))
    sig = sign(secret, request_canonical("POST", path, ts, rid, raw))
    return client.post(path, content=raw, headers={"X-LRB-Key-Id": key, "X-LRB-Request-Id": rid,
                                                   "X-LRB-Timestamp": ts, "X-LRB-Signature": sig,
                                                   "Content-Type": "application/json"})


def test_health_no_secrets(client):
    r = client.get("/health")
    assert r.status_code == 200 and "x" * 40 not in r.text


def test_valid_decision_is_signed_and_well_formed(client):
    r = post(client, "/v1/decision", body())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["decision"] in ("ALLOW", "BLOCK", "WAIT")
    assert d["direction"] in ("BUY", "SELL", "NONE")
    assert d["model_version"] == "rules-v1" and "probability" not in d  # rules model never reports probabilities
    assert d["expires_at_utc_epoch"] == NOW + 60
    assert d["data_timestamp_utc"].endswith("Z")
    assert verify(SECRET, response_canonical(200, "req-000001", r.headers["X-LRB-Timestamp"], r.content),
                  r.headers["X-LRB-Signature"])


@pytest.mark.parametrize("mut", ["sig", "key", "skew", "secret"])
def test_auth_failures(client, mut):
    raw = body()
    kw = {}
    if mut == "key":
        kw["key"] = "other"
    if mut == "skew":
        kw["ts"] = NOW - 120
    if mut == "secret":
        kw["secret"] = b"y" * 40
    if mut == "sig":
        r = client.post("/v1/decision", content=raw, headers={"X-LRB-Key-Id": "ea1", "X-LRB-Request-Id": "req-000001",
                                                               "X-LRB-Timestamp": str(int(NOW)), "X-LRB-Signature": "00"})
    else:
        r = post(client, "/v1/decision", raw, **kw)
    assert r.status_code == 401


def test_tampered_body_rejected(client):
    raw = body()
    ts = str(int(NOW))
    sig = sign(SECRET, request_canonical("POST", "/v1/decision", ts, "req-000001", raw))
    r = client.post("/v1/decision", content=raw.replace(b"BUY", b"SELL"),
                    headers={"X-LRB-Key-Id": "ea1", "X-LRB-Request-Id": "req-000001", "X-LRB-Timestamp": ts,
                             "X-LRB-Signature": sig})
    assert r.status_code == 401


def test_idempotent_replay_and_conflict(client):
    r1 = post(client, "/v1/decision", body())
    r2 = post(client, "/v1/decision", body())
    assert r1.json() == r2.json()
    r3 = post(client, "/v1/decision", body(proposed_direction="SELL"))
    assert r3.status_code == 409


@pytest.mark.parametrize("kw", [
    {"ask": 1.0},  # ask < bid
    {"proposed_direction": "LONG"},
    {"extra_field": 1},
    {"symbol": "XAUUSD"},
    {"timeframe_sec": 7},
    {"kind": "RECOVERY_LEG", "leg_index": 1},
])
def test_invalid_inputs_rejected(client, kw):
    r = post(client, "/v1/decision", body(**kw))
    assert r.status_code in (400, 403, 422)


def test_unclosed_bar_rejected(client):
    bars = make_bars()
    bars.append({"t": int(NOW) - 100, "o": 1.1, "h": 1.1, "l": 1.1, "c": 1.1})
    r = post(client, "/v1/decision", body(bars=bars))
    assert r.status_code == 400


def test_stale_bars_yield_wait(client):
    bars = make_bars()
    shift = 7200
    for b in bars:
        b["t"] -= shift
    r = post(client, "/v1/decision", body(bars=bars))
    assert r.json()["decision"] == "WAIT" and "STALE_INPUT" in r.json()["reason_codes"]


def test_insufficient_bars_yield_wait(client):
    r = post(client, "/v1/decision", body(bars=make_bars(30)))
    assert r.json()["decision"] == "WAIT"


def test_recovery_leg_defers_to_risk_engine(client):
    r = post(client, "/v1/decision", body(kind="RECOVERY_LEG", leg_index=2, proposed_direction="SELL"))
    d = r.json()
    assert d["decision"] == "ALLOW" and d["leg_index"] == 2 and d["direction"] == "SELL"


def test_logistic_model_reports_calibrated_probability_metadata():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 13))
    y = (X[:, 0] + rng.normal(size=200) > 0).astype(float)
    pnl = np.where(y > 0, -50.0, 40.0)
    m = LogisticModel.train(X[:150], y[:150], pnl[:150], X[150:], y[150:], version="logit-test")
    s = Settings(hmac_secret=SECRET, db_path=":memory:")
    c = TestClient(create_app(s, model=m, clock=lambda: NOW, store=Store(":memory:")))
    d = post(c, "/v1/decision", body()).json()
    assert d["model_version"] == "logit-test"
    assert 0 <= d["probability"]["p_basket_loss"] <= 1
    assert "Platt" in d["probability"]["calibration"]


def test_telemetry_and_dashboard(client):
    tel = {
        "terminal_id": "vps1", "ea_version": "0.1.0", "time_utc": NOW, "symbol": "EURUSD", "magic": 1,
        "balance": 10000, "equity": 9990, "margin": 50, "margin_level": 19980, "currency": "USD", "is_demo": True,
        "trading_allowed": True, "daily_halt": False, "dd_halt": False, "kill": "NONE", "sod_equity": 10000,
        "peak_equity": 10000, "daily_loss_limit_pct": 2, "max_drawdown_pct": 6, "risk_pct": 1, "target_pct": 0.5,
        "ai_mode": "FILTER", "ai_status": "OK", "errors": ["close retcode 10004"],
        "basket": {"basket_id": "b1", "phase": "ACTIVE", "direction": "BUY", "net_pnl": -10,
                   "legs": [{"index": 1, "side": "BUY", "planned_volume": 0.06, "state": "FILLED"}]},
    }
    r = post(client, "/v1/telemetry", json.dumps(tel).encode(), rid="tel-000001")
    assert r.status_code == 200, r.text
    st = client.get("/api/status").json()
    assert st["telemetry"]["equity"] == 9990
    assert any(e["message"] == "close retcode 10004" for e in st["errors"])
    assert "read-only" in client.get("/dashboard").text


def test_dashboard_token_required_when_configured():
    s = Settings(hmac_secret=SECRET, db_path=":memory:", dashboard_token="t" * 20)
    c = TestClient(create_app(s, model=RulesModel(), clock=lambda: NOW, store=Store(":memory:")))
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"Authorization": "Bearer " + "t" * 20}).status_code == 200


def test_remote_bind_without_tls_refused():
    with pytest.raises(RuntimeError):
        Settings(hmac_secret=SECRET, host="0.0.0.0").check_network_policy()
    Settings(hmac_secret=SECRET, host="127.0.0.1").check_network_policy()


def test_store_migrations_and_backup(tmp_path):
    db = tmp_path / "s.db"
    st = Store(str(db))
    assert st.schema_version() == 1
    st.log_error("x", "y")
    st.backup(str(tmp_path / "b" / "copy.db"))
    assert Store(str(tmp_path / "b" / "copy.db")).recent("errors")[0]["message"] == "y"
    assert Store(str(db)).schema_version() == 1  # re-open is idempotent


def test_redaction_filter():
    import logging
    from lrb.service.security import RedactingFilter
    rec = logging.LogRecord("x", logging.INFO, "", 0, "sig=%s token=abc secret xxxxxxxx", ("deadbeef",), None)
    RedactingFilter(["xxxxxxxx"]).filter(rec)
    assert "xxxxxxxx" not in rec.getMessage() and "abc" not in rec.getMessage()


# ---------------------------------------------------------------- EA contract tests
def _ea_request_like_mql5(rid, sent_at, kind="ENTRY", leg_index=0, direction="BUY"):
    """Builds the request exactly like WriteDecisionRequest() in LRB_EA.mq5 (field order/format)."""
    bars = make_bars()
    b = ",".join('{"t":%d,"o":%.5f,"h":%.5f,"l":%.5f,"c":%.5f}' % (x["t"], x["o"], x["h"], x["l"], x["c"]) for x in bars)
    return ('{"request_id":"%s","kind":"%s","symbol":"EURUSD","magic":26101801,"proposed_direction":"%s",'
            '"leg_index":%d,"sent_at_utc":%d,"timeframe_sec":900,"bid":1.10750,"ask":1.10760,"distance":0.00200000,'
            '"account":{"equity":10000.00,"balance":10000.00,"margin":0.00,"drawdown_pct":0.0000,"daily_pnl_pct":0.0000,'
            '"gross_lots":0.00,"net_lots":0.00},"bars":[%s]}') % (rid, kind, direction, leg_index, sent_at, b)


def test_ea_request_format_accepted(client):
    rid = "E-1801-1760003600-1"
    r = post(client, "/v1/decision", _ea_request_like_mql5(rid, int(NOW)).encode(), rid=rid)
    assert r.status_code == 200, r.text
    d = r.json()
    # every key the EA's ReadDecision() requires must be present
    for k in ("request_id", "kind", "decision", "direction", "model_version", "expires_at_utc_epoch",
              "data_timestamp_utc_epoch", "leg_index", "reason_codes"):
        assert k in d
    rid2 = "L-1801-1760003600-2"
    r = post(client, "/v1/decision", _ea_request_like_mql5(rid2, int(NOW), "RECOVERY_LEG", 2, "SELL").encode(), rid=rid2)
    assert r.status_code == 200 and r.json()["leg_index"] == 2


def test_ea_status_fixture_accepted(client):
    from pathlib import Path
    raw = (Path(__file__).parent / "fixtures" / "ea_status.json").read_bytes()
    r = post(client, "/v1/telemetry", raw, rid="T-1801-1760003600-1")
    assert r.status_code == 200, r.text


def test_mql5_hmac_vector_matches_python():
    """The RFC 4231 vector used by HmacSelfTest() in Hmac.mqh."""
    import hashlib
    import hmac
    assert hmac.new(b"Jefe", b"what do ya want for nothing?", hashlib.sha256).hexdigest() == \
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
