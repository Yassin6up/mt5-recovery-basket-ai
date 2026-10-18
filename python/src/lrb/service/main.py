"""Entry point: ``lrb-service`` (or ``python -m lrb.service.main``)."""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone

import uvicorn

from .app import create_app
from .security import RedactingFilter
from .settings import Settings


class UTCJsonFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
        })


def setup_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(UTCJsonFormatter())
    handler.addFilter(RedactingFilter([settings.hmac_secret.decode(errors="ignore"), settings.dashboard_token or ""]))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True


def run() -> None:
    settings = Settings.from_env()
    setup_logging(settings)
    app = create_app(settings)
    app.state.store.prune(settings.retention_days)
    logging.getLogger("lrb.service").info("starting on %s:%s model=%s", settings.host, settings.port,
                                          app.state.model.version)
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None,
                ssl_certfile=settings.tls_cert, ssl_keyfile=settings.tls_key, timeout_keep_alive=5)


if __name__ == "__main__":
    run()
