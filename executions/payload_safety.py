"""Credential rejection shared by exact payload capture and audit metadata."""
import os
import re

from django.conf import settings
from django.core.exceptions import ValidationError


def reject_configured_credentials(content):
    secrets = [value for key, value in os.environ.items()
               if re.search(r"(?:TOKEN|SECRET|PASSWORD|API_KEY)$", key)]
    secrets.extend(str(getattr(settings, key, "") or "") for key in (
        "SECRET_KEY", "IDEAFLOW_API_TOKEN", "IDEAFLOW_PODCAST_WORKER_TOKEN",
        "IDEAFLOW_SEMANTIC_API_KEY",
    ))
    if any(len(value) >= 12 and value.encode() in content for value in secrets) or re.search(
        rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", content
    ):
        # Never echo rejected material in a validation error or command output.
        raise ValidationError("Content contains a configured credential or private key; persistence rejected.")
