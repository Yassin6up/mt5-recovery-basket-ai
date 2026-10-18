"""Send signed requests to the decision service exactly like LRB_AIBridge.mq5 (no MT5 needed).

    LRB_HMAC_SECRET_FILE=... python -m lrb.tools.bridge_emulator --url http://127.0.0.1:8787

Useful as a post-install smoke test. It only calls /health, /v1/decision and /v1/telemetry.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

from lrb.service.security import request_canonical, response_canonical, sign, verify


def _secret() -> bytes:
    f = os.environ.get("LRB_HMAC_SECRET_FILE")
    s = Path(f).read_text().strip() if f else os.environ.get("LRB_HMAC_SECRET", "")
    if len(s) < 32:
        raise SystemExit("set LRB_HMAC_SECRET_FILE (or LRB_HMAC_SECRET)")
    return s.encode()


def signed_post(url: str, path: str, rid: str, body: bytes, secret: bytes, key_id: str = "ea1"):
    ts = str(int(time.time()))
    sig = sign(secret, request_canonical("POST", path, ts, rid, body))
    req = urllib.request.Request(url + path, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-LRB-Key-Id": key_id, "X-LRB-Request-Id": rid,
        "X-LRB-Timestamp": ts, "X-LRB-Signature": sig})
    with urllib.request.urlopen(req, timeout=3) as r:
        raw = r.read()
        ok = verify(secret, response_canonical(r.status, rid, r.headers["X-LRB-Timestamp"], raw),
                    r.headers["X-LRB-Signature"])
        return r.status, json.loads(raw), ok


def sample_bars(n: int = 150, tf: int = 900) -> list[dict]:
    last_open = int(time.time()) // tf * tf - tf
    out, p = [], 1.1
    for i in range(n):
        c = p + 0.00004 + (0.0001 if i % 3 == 0 else -0.00005)
        out.append({"t": last_open - (n - 1 - i) * tf, "o": round(p, 5), "h": round(max(p, c) + 0.0002, 5),
                    "l": round(min(p, c) - 0.0002, 5), "c": round(c, 5)})
        p = c
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    args = ap.parse_args()
    secret = _secret()
    with urllib.request.urlopen(args.url + "/health", timeout=3) as r:
        print("health:", json.loads(r.read()))
    rid = f"EMU-{int(time.time())}"
    body = json.dumps({
        "request_id": rid, "kind": "ENTRY", "symbol": "EURUSD", "magic": 26101801, "proposed_direction": "BUY",
        "leg_index": 0, "sent_at_utc": int(time.time()), "timeframe_sec": 900, "bid": 1.1075, "ask": 1.1076,
        "distance": 0.002, "account": {"equity": 10000, "balance": 10000, "margin": 0, "drawdown_pct": 0},
        "bars": sample_bars()}).encode()
    status, resp, sig_ok = signed_post(args.url, "/v1/decision", rid, body, secret)
    print("decision:", status, "signature_valid:", sig_ok, json.dumps(resp))
    return 0 if status == 200 and sig_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
