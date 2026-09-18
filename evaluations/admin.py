import json

from django.contrib import admin

from .models import (EvaluationResult, EvaluatorApproval, EvaluatorDefinition, EvaluatorVersion,
                     MetricDefinition, EvaluationExposure, HumanFeedback, FeedbackOutcomeLink,
                     EvaluationDataset, DatasetCase, DatasetSnapshot, DatasetCaseTombstone,
                     HumanCalibrationLabel)


class FrozenAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(MetricDefinition)
class MetricAdmin(FrozenAdmin):
    list_display = ("key", "version", "unit", "direction", "content_hash")
    search_fields = ("key",)


@admin.register(EvaluatorDefinition)
class DefinitionAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "is_active")
    readonly_fields = ("key",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EvaluatorVersion)
class VersionAdmin(FrozenAdmin):
    list_display = ("evaluator", "version", "method", "implementation", "content_hash")
    list_filter = ("method",)


@admin.register(EvaluatorApproval)
class ApprovalAdmin(FrozenAdmin):
    list_display = ("evaluator_version", "decision", "actor_label", "created_at")


@admin.register(EvaluationResult)
class ResultAdmin(FrozenAdmin):
    list_display = ("id", "evaluated_run", "evaluator_version", "progress_score", "actor_label", "created_at")
    list_filter = ("evaluator_version",)
    search_fields = ("idempotency_key", "output_hash")
    # General audit viewers receive metadata, not evidence bodies or free-form
    # rationale. Protected content remains outside this admin read surface.
    fields = ("id", "evaluated_run", "evaluator_version", "output_hash",
              "input_manifest_hash", "summary", "progress_score", "grader_run",
              "actor_label", "idempotency_key", "supersedes", "created_at",
              "content_hash", "evidence_metadata", "criterion_diagnostics")

    def get_readonly_fields(self, request, obj=None):
        return self.fields

    @admin.display(description="Evidence metadata (content withheld)")
    def evidence_metadata(self, obj):
        return json.dumps({ref: {key: value for key, value in item.items()
                                if key in {"kind", "hash", "source", "reference"}}
                           for ref, item in obj.input_manifest.get("evidence", {}).items()}, indent=2)

    @admin.display(description="Criterion diagnostics (rationales withheld)")
    def criterion_diagnostics(self, obj):
        return json.dumps([{key: row[key] for key in ("id", "status", "evidence_refs")}
                           for row in obj.criterion_results], indent=2)


@admin.register(EvaluationExposure, HumanFeedback, FeedbackOutcomeLink)
class InteractionAdmin(FrozenAdmin):
    list_display = ("id", "actor_label", "created_at")

    def get_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields if field.name != "reason")

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)


@admin.register(EvaluationDataset, DatasetCase, DatasetSnapshot, DatasetCaseTombstone, HumanCalibrationLabel)
class DatasetAuditAdmin(FrozenAdmin):
    """Metadata only; case bodies have no admin registration."""
    list_display = ('id', 'actor_label', 'created_at', 'content_hash')

    def has_view_permission(self, request, obj=None):
        user = request.user
        if not user.is_active or not user.has_perm('evaluations.operate_datasets'):
            return False
        if obj is None or user.is_superuser:
            return True
        dataset = obj if isinstance(obj, EvaluationDataset) else (obj.dataset if hasattr(obj, 'dataset_id') else obj.case.dataset)
        return dataset.owner_user_id == user.pk

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        field = 'owner_user_id' if self.model == EvaluationDataset else ('dataset__owner_user_id' if self.model in (DatasetCase, DatasetSnapshot) else 'case__dataset__owner_user_id')
        return queryset.filter(**{field: request.user.pk})
