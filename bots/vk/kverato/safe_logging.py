"""Helpers for redacting credentials in third-party diagnostics."""
from __future__ import annotations

import re

# Keep normal status text readable while removing signed URLs and token-shaped
# parameters that may not equal the configured VK_TOKEN (e.g. upload secrets).
_URL_WITH_QUERY = re.compile(r"(https?://[^\s<>\"']+?)\?[^\s<>\"']+", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(access_token|auth_key|token|sign|secret)=([^\s&;,\"']+)"
)


def redact(text: str, secrets: tuple[str, ...]) -> str:
    """Hide configured secrets, signed URL queries and common VK token fields."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    text = _URL_WITH_QUERY.sub(lambda m: m.group(1) + '?[REDACTED]', text)
    return _SECRET_ASSIGNMENT.sub(lambda m: m.group(1) + '=[REDACTED]', text)
