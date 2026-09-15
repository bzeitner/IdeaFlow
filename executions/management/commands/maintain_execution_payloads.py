"""Expire raw content, mirror retained payloads, and exercise backup recovery."""

import hashlib
import json
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def atomic_metadata(path, metadata):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".metadata-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(metadata, stream, sort_keys=True)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def scan(root, *, apply, now):
    retained = []
    expired = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CommandError("Symlinks are not permitted in payload storage.")
    for meta_path in root.rglob("*.payload.meta"):
        metadata = json.loads(meta_path.read_text())
        payload_path = meta_path.with_suffix("")
        expiry = parse_datetime(metadata.get("expires_at", ""))
        if expiry is None or timezone.is_naive(expiry):
            raise CommandError(f"Missing timezone-aware expiry: {meta_path}")
        if expiry <= now or metadata.get("deleted_at"):
            if payload_path.exists():
                expired += 1
                if apply:
                    metadata["deleted_at"] = now.isoformat()
                    metadata["deletion_reason"] = "retention_expired"
                    atomic_metadata(meta_path, metadata)
                    payload_path.unlink()
            continue
        if not payload_path.is_file():
            raise CommandError(f"Retained payload is missing: {payload_path}")
        if hashlib.sha256(payload_path.read_bytes()).hexdigest() != metadata["sha256"]:
            raise CommandError(f"Payload checksum mismatch: {payload_path}")
        if apply:
            payload_path.chmod(0o600)
            meta_path.chmod(0o600)
        retained.append((payload_path, metadata))
    # Failed uploads can leave a payload without metadata. Leave fresh uploads
    # alone; remove only orphans older than the configured retention period.
    cutoff = now.timestamp() - timedelta(days=settings.IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS).total_seconds()
    for path in root.rglob("*.payload"):
        if not Path(f"{path}.meta").exists() and path.stat().st_mtime < cutoff:
            expired += 1
            if apply:
                path.unlink()
    return retained, expired


class Command(BaseCommand):
    help = "Dry-run payload retention by default; --apply expires content, backs up and verifies restoration."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if settings.IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS < 1:
            raise CommandError("Payload retention must be at least one day.")
        if not settings.IDEAFLOW_EXECUTION_PAYLOAD_BACKUP_ROOT:
            raise CommandError("IDEAFLOW_EXECUTION_PAYLOAD_BACKUP_ROOT must be configured.")
        root = Path(settings.IDEAFLOW_EXECUTION_PAYLOAD_ROOT).resolve()
        backup = Path(settings.IDEAFLOW_EXECUTION_PAYLOAD_BACKUP_ROOT).resolve()
        media = Path(settings.MEDIA_ROOT).resolve()
        if root == backup or root in backup.parents or backup in root.parents:
            raise CommandError("Payload and backup roots must not overlap.")
        if any(path == media or media in path.parents or path == Path("/") for path in (root, backup)):
            raise CommandError("Payload roots must be private directories outside MEDIA_ROOT.")
        apply = options["apply"]
        if apply:
            for directory in (root, backup):
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory.chmod(0o700)
        now = timezone.now()
        retained, expired = scan(root, apply=apply, now=now)
        _, backup_expired = scan(backup, apply=apply, now=now)
        restored = 0
        if apply:
            # Mirror only retained content. Metadata tombstones remain with the
            # primary store so immutable run references retain their meaning.
            for source, metadata in retained:
                destination = backup / source.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Create every ancestor privately, including intermediate dirs.
                for parent in destination.parents:
                    if parent == backup:
                        break
                    parent.chmod(0o700)
                fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".backup-")
                os.close(fd)
                try:
                    shutil.copyfile(source, temporary)
                    if hashlib.sha256(Path(temporary).read_bytes()).hexdigest() != metadata["sha256"]:
                        raise CommandError("Backup checksum verification failed.")
                    atomic_metadata(Path(f"{destination}.meta"), metadata)
                    os.replace(temporary, destination)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            # Recover from the backup into a separate private directory, then
            # verify the recovered bytes; never overwrite live execution data.
            with tempfile.TemporaryDirectory(prefix="payload-restore-") as temporary:
                for source, metadata in retained:
                    destination = backup / source.relative_to(root)
                    recovered = Path(temporary) / source.name
                    shutil.copyfile(destination, recovered)
                    if hashlib.sha256(recovered.read_bytes()).hexdigest() != metadata["sha256"]:
                        raise CommandError("Backup restore verification failed.")
                    restored += 1
            for directory in (root, backup):
                for child in directory.rglob("*"):
                    if child.is_dir():
                        child.chmod(0o700)
        self.stdout.write(json.dumps({
            "checked_at": now.isoformat(), "applied": apply,
            "retained_verified": len(retained), "expired": expired,
            "backup_expired": backup_expired, "backup_restored_verified": restored,
        }, sort_keys=True))
