"""HMAC-SHA256 request/response authentication, replay protection helpers, log redaction.

Request canonical string (UTF-8, '\n' separated):
    METHOD, PATH, X-LRB-Timestamp, X-LRB-Request-Id, sha256_hex(body)
Response canonical string:
    STATUS_CODE, X-LRB-Request-Id, X-LRB-Timestamp (response), sha256_hex(response_body)
Signature = lowercase hex HMAC-SHA256(secret, canonical). Header X-LRB-Signature.
The MQL5 implementation is in mql5/Include/LRB/Hmac.mqh.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_canonical(method: str, path: str, ts: str, request_id: str, body: bytes) -> bytes:
    return "\n".join([method.upper(), path, ts, request_id, sha256_hex(body)]).encode()


def response_canonical(status: int, request_id: str, ts: str, body: bytes) -> bytes:
    return "\n".join([str(status), request_id, ts, sha256_hex(body)]).encode()


def sign(secret: bytes, canonical: bytes) -> str:
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify(secret: bytes, canonical: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, canonical), (signature or "").lower())


REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9:_\-]{8,64}$")


class RedactingFilter(logging.Filter):
    """Removes secrets, signatures and tokens from log records."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]
        self._pat = re.compile(r"(?i)(signature|token|secret|password|authorization)([\"'=:\s]+)([^\s\"',}]+)")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self._secrets:
            msg = msg.replace(s, "***")
        msg = self._pat.sub(r"\1\2***", msg)
        record.msg, record.args = msg, ()
        return True
