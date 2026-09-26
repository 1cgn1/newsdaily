"""Bind a reviewed digest to one recipient without storing the address."""
from __future__ import annotations

import hashlib
import hmac


def recipient_binding(digest_date: str, recipient: str, smtp_auth_code: str) -> str:
    payload = f"full-v3|{digest_date}|{recipient}".encode("utf-8")
    digest = hmac.new(smtp_auth_code.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{digest_date}|full-v3|{digest}"
