"""Service settings from environment variables (never from code or chat).

Secrets: LRB_HMAC_SECRET_FILE (preferred, file with restricted ACL) or LRB_HMAC_SECRET.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    hmac_secret: bytes = field(repr=False)
    key_id: str = "ea1"
    db_path: str = "lrb_service.db"
    model_path: str | None = None
    host: str = "127.0.0.1"
    port: int = 8787
    tls_cert: str | None = None
    tls_key: str | None = None
    max_skew_sec: float = 30.0
    decision_ttl_sec: float = 60.0
    max_data_age_sec: float = 1200.0
    dashboard_token: str | None = field(default=None, repr=False)
    allowed_symbols: tuple[str, ...] = ("EURUSD",)
    retention_days: int = 30
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        secret_file = _env("LRB_HMAC_SECRET_FILE")
        if secret_file:
            secret = Path(secret_file).read_text().strip()
        else:
            secret = _env("LRB_HMAC_SECRET", "") or ""
        if len(secret) < 32:
            raise RuntimeError("LRB_HMAC_SECRET(_FILE) missing or shorter than 32 characters")
        s = cls(
            hmac_secret=secret.encode(),
            key_id=_env("LRB_KEY_ID", "ea1"),
            db_path=_env("LRB_DB_PATH", "lrb_service.db"),
            model_path=_env("LRB_MODEL_PATH"),
            host=_env("LRB_BIND_HOST", "127.0.0.1"),
            port=int(_env("LRB_PORT", "8787")),
            tls_cert=_env("LRB_TLS_CERT"),
            tls_key=_env("LRB_TLS_KEY"),
            max_skew_sec=float(_env("LRB_MAX_SKEW_SEC", "30")),
            decision_ttl_sec=float(_env("LRB_DECISION_TTL_SEC", "60")),
            max_data_age_sec=float(_env("LRB_MAX_DATA_AGE_SEC", "1200")),
            dashboard_token=_env("LRB_DASHBOARD_TOKEN"),
            allowed_symbols=tuple(x.strip() for x in _env("LRB_ALLOWED_SYMBOLS", "EURUSD").split(",") if x.strip()),
            retention_days=int(_env("LRB_RETENTION_DAYS", "30")),
            log_level=_env("LRB_LOG_LEVEL", "INFO"),
        )
        s.check_network_policy()
        return s

    def check_network_policy(self) -> None:
        """Plain HTTP is only allowed on loopback. Remote access requires TLS."""
        try:
            loopback = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            loopback = self.host == "localhost"
        if not loopback and not (self.tls_cert and self.tls_key):
            raise RuntimeError(
                f"refusing to bind {self.host} without TLS; set LRB_TLS_CERT/LRB_TLS_KEY or use a TLS reverse proxy"
            )
