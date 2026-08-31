from django.contrib import admin

from .models import (
    EvidenceAction, EvidenceAssignment, EvidenceCandidate, EvidenceExperiment,
    EvidenceObservation, LegacyEntitySnapshot, Source, SourceItem, Subscription,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "is_active", "canonical_url", "updated_at")
    list_filter = ("kind", "is_active")
    search_fields = ("title", "canonical_url")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("source", "idea", "intent", "relevance_prior", "item_budget", "is_paused")
    list_filter = ("intent", "is_paused")


@admin.register(SourceItem)
class SourceItemAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "published_at", "ingested_at", "eligible_for_processing")
    list_filter = ("eligible_for_processing", "source__kind")
    search_fields = ("title", "external_id", "url")


@admin.register(EvidenceCandidate)
class EvidenceCandidateAdmin(admin.ModelAdmin):
    list_display = ("source_item", "idea", "deterministic_score", "llm_score", "rank", "decision", "exposed_at")
    list_filter = ("decision",)


@admin.register(EvidenceExperiment)
class EvidenceExperimentAdmin(admin.ModelAdmin):
    list_display = ("key", "state", "primary_metric", "treatment_percent", "enrollment_started_at")
    list_filter = ("state",)


@admin.register(EvidenceAssignment)
class EvidenceAssignmentAdmin(admin.ModelAdmin):
    list_display = ("experiment", "candidate", "variant", "randomization_key", "assigned_at")
    list_filter = ("experiment", "variant")
    readonly_fields = ("assignment_hash", "assigned_at")


@admin.register(EvidenceAction, EvidenceObservation, LegacyEntitySnapshot)
class Phase3AuditAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False
