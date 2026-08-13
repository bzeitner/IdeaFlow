import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ideas.graph.revision import current_revision
from ideas.models import GraphAccessCapability


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_capability(user, *, filters):
    raw = secrets.token_urlsafe(32)
    capability = GraphAccessCapability.objects.create(
        token_hash=token_hash(raw),
        user=user,
        filters=filters,
        graph_revision=current_revision(),
        expires_at=timezone.now() + timedelta(
            seconds=settings.IDEAFLOW_GRAPH_CAPABILITY_TTL_SECONDS
        ),
    )
    return capability, raw


@transaction.atomic
def consume_capability(raw):
    try:
        capability = GraphAccessCapability.objects.select_for_update().select_related(
            "user", "user__profile"
        ).get(token_hash=token_hash(raw))
    except GraphAccessCapability.DoesNotExist:
        return None, "invalid"
    now = timezone.now()
    if capability.revoked_at or capability.expires_at <= now:
        return None, "expired"
    if not capability.user.is_active or not capability.user.profile.has_role("role_graph"):
        capability.revoked_at = now
        capability.save(update_fields=["revoked_at"])
        return None, "forbidden"
    if capability.scope != "graph:read":
        return None, "forbidden"
    if capability.request_count >= settings.IDEAFLOW_GRAPH_CAPABILITY_MAX_REQUESTS:
        return None, "exhausted"
    capability.request_count += 1
    capability.last_accessed_at = now
    capability.save(update_fields=["request_count", "last_accessed_at"])
    return capability, None
