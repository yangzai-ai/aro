"""SSE framing and boundary redaction; the kernel has no transport dependency."""

import json
import re
from typing import Any

from core.kernel.events import ExecutionEvent

SENSITIVE_FIELDS = {
    "api_key",
    "apikey",
    "password",
    "authorization",
    "access_token",
    "connection_string",
}


def redact(value: Any, secrets: tuple[str, ...]) -> Any:
    """Mask credential fields, configured secrets and URL user/password pairs."""
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower() in SENSITIVE_FIELDS else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        return re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", value)
    return value


def encode_event(event: ExecutionEvent, secrets: tuple[str, ...] = ()) -> str:
    """One complete JSON SSE frame; newlines in values cannot inject frames."""
    payload = json.dumps(redact(event.data, secrets), ensure_ascii=False)
    return f"event: {event.type}\ndata: {payload}\n\n"
