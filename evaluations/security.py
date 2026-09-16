"""Reject secrets rather than silently changing immutable evaluation evidence."""
import json
import re

from django.core.exceptions import ValidationError
from executions.payload_safety import reject_configured_credentials

MAX_METADATA_BYTES = 65536
MAX_EXCERPT_BYTES = 4096
EXCERPT_POLICY = "evaluation-excerpt-v1"
SENSITIVE_KEY = re.compile(
    r"^(authorization|proxy_authorization|cookie|set_cookie|password|passwd|"
    r"api_key|access_token|refresh_token|client_secret|private_key)$", re.I
)
CREDENTIAL_TEXT = re.compile(
    r"\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_.=~\-]{8,}|"
    r"\b(?:api[_-]?key|password|passwd|client[_-]?secret|access[_-]?token)\s*[:=]\s*(?!\[REDACTED\])\S+|"
    r"\b(?:authorization|cookie|set-cookie)\s*:\s*(?!\[REDACTED\])\S+|"
    r"[?&](?:X-Amz-Signature|X-Goog-Signature|sig|access_token|api_key)=[^&\s]+|"
    r"https?://[^\s/@:]+:[^\s/@]+@",
    re.I,
)


def validate_metadata(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError("Evaluation metadata must be bounded, valid JSON.") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValidationError("Evaluation metadata exceeds its size limit; use protected payload storage.")

    def walk(item, depth=0):
        if depth > 16:
            raise ValidationError("Evaluation metadata nesting exceeds its limit.")
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).replace("-", "_")
                if SENSITIVE_KEY.fullmatch(normalized) and child not in (None, "", "[REDACTED]"):
                    raise ValidationError("Credential fields are not allowed in evaluation metadata.")
                walk(str(key), depth + 1)
                walk(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, str):
            reject_configured_credentials(item.encode("utf-8"))
            if CREDENTIAL_TEXT.search(item):
                raise ValidationError("Credential-bearing content is not allowed in evaluation metadata.")
    walk(value)


def validate_excerpt(item, actor_label):
    approval = item.get("approval")
    if not isinstance(approval, dict) or set(approval) != {"policy", "approved_by", "value_hash"}:
        raise ValidationError("Content-bearing evidence requires explicit redacted-excerpt approval.")
    if approval != {"policy": EXCERPT_POLICY, "approved_by": actor_label, "value_hash": item["hash"]}:
        raise ValidationError("Excerpt approval must match its operator, policy, and exact content hash.")
    if len(json.dumps(item["value"], ensure_ascii=False).encode("utf-8")) > MAX_EXCERPT_BYTES:
        raise ValidationError("Evidence excerpt exceeds its size limit; retain full content in protected storage.")
