from django.db import transaction
from django.db.models import F

from ideas.models import GraphRevision


def current_revision():
    marker, _ = GraphRevision.objects.get_or_create(pk=1)
    return marker.revision


def mark_graph_changed():
    def bump():
        marker, _ = GraphRevision.objects.get_or_create(pk=1)
        GraphRevision.objects.filter(pk=marker.pk).update(revision=F("revision") + 1)

    transaction.on_commit(bump)
