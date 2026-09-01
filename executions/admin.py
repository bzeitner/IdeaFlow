from django.contrib import admin

from .models import (
    ArtifactVersion, DeterministicJob, ExecutionEvent, ExecutionTrace, LLMRun,
    ModelConfiguration, OutcomeEvent, PricingVersion, ServicePrincipal,
    ToolInvocation, WorkflowCutover, WorkflowDefinition, WorkflowVersion,
)


class AuditAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkflowDefinition)
class WorkflowDefinitionAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "is_active", "updated_at")
    search_fields = ("key", "name")


@admin.register(WorkflowVersion, ModelConfiguration, PricingVersion)
class ImmutableConfigurationAdmin(AuditAdmin):
    readonly_fields = ("created_at",)

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields) if obj else self.readonly_fields


@admin.register(ExecutionTrace)
class ExecutionTraceAdmin(AuditAdmin):
    list_display = ("id", "workflow_version", "subject_label", "trigger", "status", "created_at")
    list_filter = ("status", "trigger", "workflow_version__workflow")
    search_fields = ("id", "subject_label", "correlation_key", "idempotency_key")
    readonly_fields = tuple(field.name for field in ExecutionTrace._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(LLMRun)
class LLMRunAdmin(AuditAdmin):
    list_display = ("id", "trace", "purpose", "attempt_number", "status", "total_tokens", "cost_micros")
    list_filter = ("status", "purpose", "measurement_status", "model_configuration__provider")
    search_fields = ("id", "trace__id", "provider_request_id")
    readonly_fields = tuple(field.name for field in LLMRun._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(ToolInvocation, ExecutionEvent)
class AppendOnlyAdmin(AuditAdmin):
    readonly_fields = ()

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(ServicePrincipal)
class ServicePrincipalAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "scopes", "last_used_at", "revoked_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("token_hash", "last_used_at", "created_at", "revoked_at")


@admin.register(WorkflowCutover)
class WorkflowCutoverAdmin(admin.ModelAdmin):
    list_display = ("workflow_key", "mode", "changed_at", "changed_by")
    list_filter = ("mode",)
    readonly_fields = ("changed_at",)


@admin.register(DeterministicJob, ArtifactVersion, OutcomeEvent)
class Phase4AuditAdmin(AuditAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False
