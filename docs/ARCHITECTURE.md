# Architecture

## Deployment constraints (verified on 2026-10-18)

* The official `MetaTrader5` Python package on PyPI (version 5.0.6231) publishes **only
  `win_amd64` wheels**. `pip` on Linux finds no distribution. It connects to a locally running
  MT5 terminal through interprocess communication, so Python↔terminal integration requires
  **Windows on the same host as the terminal**.
* MQL5 `WebRequest()` is **synchronous**: it blocks the calling program until the server responds
  or the timeout expires. It is not available in indicators or in the Strategy Tester, and every
  URL must be added to *Tools → Options → Expert Advisors → Allow WebRequest for listed URL*.
* MetaQuotes distributes MT5 for Linux through Wine. That setup is not used or claimed as
  supported here.

## Chosen architecture

The default runs everything on one Windows VPS: the MT5 terminal plus the Python service on loopback.

```
 Windows VPS (logged-on desktop session; MT5 must stay running)
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ MT5 terminal (terminal64.exe)                                            │
 │  ┌─────────────────────────────┐        ┌─────────────────────────────┐  │
 │  │ Chart 1: LRB_EA  (EURUSD M15)│ files │ Chart 2: LRB_AIBridge        │  │
 │  │  • SINGLE EXECUTION OWNER    │──────▶│  • NO trading code           │  │
 │  │  • risk engine, exits, legs  │ req_* │  • HMAC-signed WebRequest    │  │
 │  │  • never calls the network   │◀──────│  • verifies response sig     │  │
 │  │  • state.txt, journal, status│ resp_*│  • posts status as telemetry │  │
 │  └─────────────────────────────┘        └──────────────┬──────────────┘  │
 │        <Common>\Files\LRB\<magic>\  (shared folder)    │ http://127.0.0.1│
 │                                                        ▼                 │
 │  Python FastAPI service (scheduled task, auto-restart) 127.0.0.1:8787    │
 │   /v1/decision  /v1/telemetry  /health  /dashboard (read-only)           │
 │   SQLite (WAL, migrations, daily backups)                                │
 └──────────────────────────────────────────────────────────────────────────┘
```

**Option B: a separate Linux host for the service.** MT5 and both EAs stay on Windows. The
service runs under systemd behind Caddy TLS on :443, the firewall allows only the Windows VPS IP,
and the bridge uses `https://…`. Hosting Python alone is **not** sufficient: the Windows terminal
must run continuously.

### Why two EAs?

`WebRequest` blocks its caller. Each chart's EA runs in its own thread, so moving every network
call into `LRB_AIBridge` on a different chart means a slow or hung service can **never** delay the
main EA's tick processing, exits or kill switch. The main EA only reads and writes small local
files, which takes microseconds to milliseconds.

## Execution authority

Only `LRB_EA` sends, modifies or closes orders. The bridge and the Python service have no order
code. The AI can only **narrow** what the EA does: it can block a new basket, or optionally a
recovery leg. It can never open, size, hold or close anything, and it cannot disable a stop.

## File protocol (`<Common>\Files\LRB\<magic>\`)

| File | Writer | Reader | Content |
|---|---|---|---|
| `req_entry.json`, `req_leg.json` | EA | bridge | DecisionRequest JSON. Written atomically (tmp + move). |
| `resp_<request_id>.json` | bridge | EA (then deleted) | Verified DecisionResponse, or `{"request_id","error"}` |
| `bridge_heartbeat.txt` | bridge | EA, health check | UTC epoch seconds |
| `status.json` | EA (every 5 s) | bridge → `/v1/telemetry`, health check | TelemetryIn |
| `state.txt` | EA | EA (restart) | persisted basket/guard state |
| `journal_<date>.csv`, `baskets.csv` | EA | humans/backup | UTC audit log, final realized P&L per basket |
| `KILL` | operator / `emergency-stop.ps1` | EA (polled every 0.5 s) | `HALT` or `FLATTEN` |
| `../hmac_secret.txt` | installer | bridge, service | shared HMAC secret (ACL: user + SYSTEM) |

## Request authentication and replay protection

* Headers: `X-LRB-Key-Id`, `X-LRB-Request-Id` (8–64 chars `[A-Za-z0-9:_-]`), `X-LRB-Timestamp`
  (UTC seconds), and `X-LRB-Signature`.
* Signature: `hex(HMAC-SHA256(secret, METHOD\nPATH\nTS\nREQUEST_ID\nsha256_hex(body)))`.
* Server checks: key id; timestamp skew ≤ 30 s; signature compared in constant time; body
  `request_id` equal to the header; `sent_at_utc` skew; symbol allow-list; every bar already closed.
* **Idempotency and replay:** each request id is stored. The same id with the same body returns the
  stored response. The same id with a different body is rejected with `409`. Stored ids expire
  after `LRB_RETENTION_DAYS`.
* Responses are signed by the server (`STATUS\nREQUEST_ID\nTS\nsha256_hex(body)`), and the bridge
  rejects a response whose signature, request id or timestamp skew is wrong.
* Network: plain HTTP is accepted **only on loopback**. The service refuses to bind a non-loopback
  address without TLS, and the bridge refuses plain HTTP to a non-loopback URL. On Windows an
  inbound firewall block rule is added for the port.
* Secrets: they are read from a file or environment variable and never logged (a redacting log
  filter is tested). Broker credentials are never needed by Python and never sent to any AI provider.

## Decision semantics (fail-safe)

| Situation | New basket | Recovery leg (if approval REQUIRED) | Existing basket |
|---|---|---|---|
| ALLOW, valid, unexpired, direction matches | allowed (after re-checking filters and sizing) | allowed | managed locally |
| BLOCK / WAIT / direction mismatch | blocked | stop adding | managed locally |
| Timeout, invalid JSON, bad signature, stale data, expired, bridge down, service down | **blocked** | **stop adding** | **managed locally; stops are never disabled** |

The decision response contains `decision` (ALLOW/BLOCK/WAIT), `direction` (BUY/SELL/NONE),
`model_version`, `data_timestamp_utc`, `expires_at_utc` (TTL 60 s) and `reason_codes`. A
`probability` block appears **only** for an evaluated, Platt-calibrated `LogisticModel`. That
block states its outcome and horizon. The default `rules-v1` model never reports probabilities,
and no LLM output is ever treated as a probability.

## Failure modes

| Failure | Effect | Handling |
|---|---|---|
| Python service down | AI decisions unavailable | Entries are blocked (AI mode) and the basket is managed; the health check restarts the service task. |
| Bridge EA removed or hung | No responses or heartbeat | Same as above. `ai_status=BRIDGE_DOWN`. |
| MT5 terminal closed or crashed | **Nothing manages positions** | The health check logs an error event; the operator restarts the terminal (see OPERATIONS.md). The optional broker-side catastrophic SL is the only protection while it is down. |
| VPS reboot | Terminal restarts at logon (auto-logon + scheduled task) | EA starts in `RECONCILE` and verifies broker positions before any entry. |
| Broker disconnect | Orders fail | Closes are retried; entries are blocked (`TERMINAL_DISCONNECTED`). |
| Partial fill / requote / reject | — | See STRATEGY_SPEC §3, §5, §7. |
| Clock skew > 30 s | Service rejects requests | Entries are blocked; fix time sync (`time-sync.ps1` / chrony). |
