"""SQLite persistence with versioned migrations (WAL mode, single-writer lock)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE requests (
        request_id TEXT PRIMARY KEY,
        endpoint TEXT NOT NULL,
        body_sha256 TEXT NOT NULL,
        status INTEGER NOT NULL,
        response_body TEXT NOT NULL,
        created_utc REAL NOT NULL
    );
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        created_utc REAL NOT NULL,
        symbol TEXT, magic INTEGER, kind TEXT, proposed_direction TEXT,
        decision TEXT, direction TEXT, model_version TEXT, reason_codes TEXT,
        p_loss REAL, data_timestamp_utc REAL, expires_at_utc REAL
    );
    CREATE INDEX ix_decisions_time ON decisions(created_utc);
    CREATE TABLE telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        received_utc REAL NOT NULL,
        terminal_id TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX ix_telemetry_time ON telemetry(received_utc);
    CREATE TABLE errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_utc REAL NOT NULL,
        source TEXT NOT NULL,
        message TEXT NOT NULL
    );
    """,
]


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
                self._conn.execute("BEGIN")
                try:
                    # executescript() would auto-commit; run statements inside our transaction instead.
                    for stmt in (x for x in sql.split(";") if x.strip()):
                        self._conn.execute(stmt)
                    self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (i,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
            return len(MIGRATIONS)

    def schema_version(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]

    # --- idempotency / replay -------------------------------------------------------
    def get_request(self, request_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()

    def put_request(self, request_id: str, endpoint: str, body_sha: str, status: int, response_body: str) -> bool:
        with self._lock:
            try:
                self._conn.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)",
                                   (request_id, endpoint, body_sha, status, response_body, time.time()))
                return True
            except sqlite3.IntegrityError:
                return False

    # --- logs -----------------------------------------------------------------------------
    def log_decision(self, request_id: str, req: dict[str, Any], resp: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO decisions(request_id, created_utc, symbol, magic, kind, proposed_direction, decision,"
                " direction, model_version, reason_codes, p_loss, data_timestamp_utc, expires_at_utc)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, time.time(), req.get("symbol"), req.get("magic"), req.get("kind"),
                 req.get("proposed_direction"), resp["decision"], resp["direction"], resp["model_version"],
                 json.dumps(resp["reason_codes"]), (resp.get("probability") or {}).get("p_basket_loss"),
                 resp["data_timestamp_utc_epoch"], resp["expires_at_utc_epoch"]))

    def log_telemetry(self, terminal_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO telemetry(received_utc, terminal_id, payload) VALUES (?,?,?)",
                               (time.time(), terminal_id, json.dumps(payload)))

    def log_error(self, source: str, message: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO errors(created_utc, source, message) VALUES (?,?,?)",
                               (time.time(), source, message[:2000]))

    def latest_telemetry(self) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
        return {"received_utc": r["received_utc"], "terminal_id": r["terminal_id"], **json.loads(r["payload"])} if r else None

    def recent(self, table: str, limit: int = 20) -> list[dict[str, Any]]:
        if table not in ("decisions", "errors"):
            raise ValueError(table)
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def prune(self, retention_days: int) -> None:
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            self._conn.execute("DELETE FROM requests WHERE created_utc < ?", (cutoff,))
            self._conn.execute("DELETE FROM telemetry WHERE received_utc < ?", (cutoff,))
            self._conn.execute("DELETE FROM decisions WHERE created_utc < ?", (cutoff,))
            self._conn.execute("DELETE FROM errors WHERE created_utc < ?", (cutoff,))

    def backup(self, dest: str) -> None:
        """Online, consistent backup using the SQLite backup API."""
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(dest)
            try:
                self._conn.backup(target)
            finally:
                target.close()
