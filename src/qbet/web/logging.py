"""Privacy-preserving structured request-log formatter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_ACCOUNT_TOKEN_PATTERNS = (
    re.compile(r"/verify-email/[^/\s]+/[^/\s]+/?"),
    re.compile(r"/accounts/password/reset/[^/\s]+/[^/\s]+/?"),
)


def redact_account_tokens(value: str) -> str:
    """Remove one-time account tokens from arbitrary log messages."""

    redacted = _ACCOUNT_TOKEN_PATTERNS[0].sub("/verify-email/<redacted>/", value)
    return _ACCOUNT_TOKEN_PATTERNS[1].sub(
        "/accounts/password/reset/<redacted>/",
        redacted,
    )


class AccountTokenRedactionFilter(logging.Filter):
    """Redact secret-bearing account paths before any configured console output."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_account_tokens(record.getMessage())
        record.args = ()
        if hasattr(record, "path"):
            record.path = redact_account_tokens(str(record.path))
        return True


class SafeRequestJSONFormatter(logging.Formatter):
    """Serialize only fields deliberately supplied by request middleware."""

    request_fields = (
        "method",
        "path",
        "status",
        "duration_ms",
        "user_id",
        "correlation_id",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {"event": record.getMessage()}
        payload.update({field: getattr(record, field, None) for field in self.request_fields})
        return json.dumps(payload, default=str, separators=(",", ":"))
