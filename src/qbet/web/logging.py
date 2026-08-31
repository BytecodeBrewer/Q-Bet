"""Privacy-preserving structured request-log formatter."""

from __future__ import annotations

import json
import logging
from typing import Any


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
        payload.update(
            {field: getattr(record, field, None) for field in self.request_fields}
        )
        return json.dumps(payload, default=str, separators=(",", ":"))
