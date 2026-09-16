import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .payload_safety import reject_configured_credentials


class PayloadExpired(FileNotFoundError):
    """Content intentionally removed or inaccessible under retention policy."""


@dataclass(frozen=True)
class StoredPayload:
    reference: str
    sha256: str
    size_bytes: int


class ExecutionPayloadStore:
    """Private content-addressed metadata over a non-public filesystem store."""

    scheme = "execution://"

    def __init__(self, storage=None):
        self.storage = storage or FileSystemStorage(
            location=settings.IDEAFLOW_EXECUTION_PAYLOAD_ROOT,
            base_url=None,
            file_permissions_mode=0o600,
            directory_permissions_mode=0o700,
        )

    def put(self, kind, content, *, content_type="application/octet-stream"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes):
            content = json.dumps(
                content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            content_type = "application/json"
        if len(content) > settings.IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES:
            raise ValidationError("Execution payload exceeds the configured size limit.")
        if settings.IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS < 1:
            raise ValidationError("Payload retention must be at least one day.")
        # Keep stored bytes identical to their execution hash. Reject known
        # credentials instead of silently rewriting evidence after inference.
        reject_configured_credentials(content)
        safe_kind = self._safe_component(kind)
        digest = hashlib.sha256(content).hexdigest()
        today = date.today()
        name = (
            f"{today:%Y/%m/%d}/{safe_kind}/{digest[:2]}/"
            f"{uuid.uuid4().hex}-{digest}.payload"
        )
        stored_name = self.storage.save(name, ContentFile(content))
        now = timezone.now()
        metadata = json.dumps(
            {"content_type": content_type, "sha256": digest, "size_bytes": len(content),
             "created_at": now.isoformat(),
             "expires_at": (now + timedelta(days=settings.IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS)).isoformat()},
            sort_keys=True,
        ).encode("utf-8")
        self.storage.save(f"{stored_name}.meta", ContentFile(metadata))
        return StoredPayload(
            reference=f"{self.scheme}{stored_name}", sha256=digest, size_bytes=len(content)
        )

    def get(self, reference):
        name = self._name(reference)
        if self.storage.exists(f"{name}.meta"):
            with self.storage.open(f"{name}.meta", "rb") as source:
                metadata = json.load(source)
            expiry = parse_datetime(metadata.get("expires_at", ""))
            if metadata.get("deleted_at") or (expiry and expiry <= timezone.now()):
                raise PayloadExpired("Execution payload expired under retention policy.")
        with self.storage.open(name, "rb") as payload:
            content = payload.read(settings.IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES + 1)
        if len(content) > settings.IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES:
            raise ValidationError("Stored execution payload exceeds the configured limit.")
        return content

    def verify(self, reference, expected_sha256):
        return hashlib.sha256(self.get(reference)).hexdigest() == expected_sha256

    def _name(self, reference):
        if not reference.startswith(self.scheme):
            raise SuspiciousFileOperation("Invalid execution payload reference.")
        name = reference[len(self.scheme):]
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not name:
            raise SuspiciousFileOperation("Unsafe execution payload reference.")
        return str(path)

    @staticmethod
    def _safe_component(value):
        value = str(value).strip().lower().replace("_", "-")
        if not value or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in value):
            raise ValidationError("Payload kind must contain only letters, digits, and hyphens.")
        return value
