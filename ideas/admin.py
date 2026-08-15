import difflib

from django.contrib import admin
from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import (
    AIModel,
    Category,
    Feed,
    FeedItem,
    FeedItemAssessment,
    Idea,
    IdeaFeed,
    IdeaRelation,
    GraphAccessCapability,
    IdeaRelationSuggestion,
    IdeaSemanticState,
    SemanticGraphSettings,
    Profile,
    PromptRevision,
    PromptRevisionStatus,
    PromptTemplate,
    Resource,
    RepeatResult,
    ResearchEntry,
    Stage,
)

admin.site.site_header = "IdeaFlow Administration"
admin.site.site_title = "IdeaFlow"
admin.site.index_title = "Manage ideas and dropdown options"


class TooltipAdminMixin:
    class Media:
        css = {"all": ("ideas/admin_tooltips.css",)}
        js = ("ideas/admin_tooltips.js",)


class LookupAdmin(TooltipAdminMixin, admin.ModelAdmin):
    """Shared admin for the editable dropdown lists (Category, Stage)."""

    list_display = ("name", "swatch", "order", "is_active", "idea_count")
    list_editable = ("order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "color", "order", "is_active")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_ideas=Count("ideas"))

    @admin.display(description="Color")
    def swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:14px;height:14px;border-radius:3px;'
            'vertical-align:middle;border:1px solid #0002;background:{}"></span> {}',
            obj.color,
            obj.color,
        )

    @admin.display(description="Ideas", ordering="_ideas")
    def idea_count(self, obj):
        return obj._ideas


@admin.register(Category)
class CategoryAdmin(LookupAdmin):
    list_display = ("name", "swatch", "is_research", "order", "is_active", "idea_count")
    list_editable = ("is_research", "order", "is_active")
    fields = ("name", "slug", "color", "is_research", "order", "is_active")


@admin.register(Stage)
class StageAdmin(LookupAdmin):
    pass


@admin.register(AIModel)
class AIModelAdmin(LookupAdmin):
    list_display = ("name", "swatch", "tier", "order", "is_active")
    list_editable = ("tier", "order", "is_active")
    list_filter = ("tier", "is_active")
    fields = ("name", "slug", "color", "tier", "order", "is_active")

    def get_queryset(self, request):
        # AIModel has no "ideas" relation, so skip LookupAdmin's Count("ideas").
        return admin.ModelAdmin.get_queryset(self, request)


class ResourceInline(admin.TabularInline):
    model = Resource
    extra = 1


class ResearchEntryInline(admin.TabularInline):
    model = ResearchEntry
    extra = 1
    fields = ("topic", "focus", "occurred_at", "model", "effort", "quality", "tokens_used")


class IdeaFeedInline(admin.TabularInline):
    model = IdeaFeed
    extra = 0
    autocomplete_fields = ("feed",)
    fields = ("feed", "rating")


class RepeatResultInline(admin.TabularInline):
    model = RepeatResult
    extra = 0
    fields = ("title", "url", "status", "found_at")


@admin.register(Idea)
class IdeaAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("title", "category", "parent", "status", "stage", "interest_level", "is_public", "rank")
    list_editable = ("status", "is_public", "rank")
    list_filter = ("status", "is_public", "category", "stage", "interest_level")
    search_fields = ("title", "summary", "notes")
    list_select_related = ("category", "stage", "parent")
    autocomplete_fields = ("parent",)
    inlines = [ResourceInline, ResearchEntryInline, RepeatResultInline, IdeaFeedInline]


@admin.register(RepeatResult)
class RepeatResultAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("title", "idea", "status", "found_at")
    list_filter = ("status",)
    search_fields = ("title", "details", "url", "idea__title")
    list_select_related = ("idea",)


@admin.register(ResearchEntry)
class ResearchEntryAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("topic", "idea", "model", "occurred_at", "effort", "quality", "tokens_used")
    list_filter = ("model", "effort", "quality")
    search_fields = ("topic", "focus", "context", "idea__title")
    list_select_related = ("idea", "model")
    date_hierarchy = "occurred_at"


@admin.register(IdeaRelation)
class IdeaRelationAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("source", "relation_type", "target", "confidence", "provenance")
    list_filter = ("relation_type", "confidence", "provenance")
    search_fields = ("source__title", "target__title", "description")
    autocomplete_fields = ("source", "target", "created_by")


@admin.register(GraphAccessCapability)
class GraphAccessCapabilityAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("user", "scope", "graph_revision", "request_count", "expires_at", "revoked_at")
    list_filter = ("scope", "revoked_at")
    search_fields = ("user__email", "user__username", "token_hash")
    readonly_fields = ("token_hash", "user", "scope", "filters", "graph_revision", "request_count", "created_at", "expires_at", "last_accessed_at", "revoked_at")


@admin.register(IdeaSemanticState)
class IdeaSemanticStateAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("idea", "status", "embedding_model", "processed_at", "updated_at")
    list_filter = ("status", "embedding_model")
    search_fields = ("idea__title", "error")
    readonly_fields = ("content_hash", "embedding", "processed_at", "updated_at")


@admin.register(SemanticGraphSettings)
class SemanticGraphSettingsAdmin(TooltipAdminMixin, admin.ModelAdmin):
    fields = ("auto_accept_confidence_percent", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not SemanticGraphSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .graph.semantic import auto_accept_pending

        accepted = auto_accept_pending(obj.auto_accept_confidence_percent)
        if accepted:
            self.message_user(request, f"Automatically accepted {accepted} pending relationship(s).")


@admin.register(PromptTemplate)
class PromptTemplateAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("name", "key", "approved_version", "proposal_count", "is_active", "propose_link")
    list_filter = ("is_active",)
    search_fields = ("name", "key", "description")
    readonly_fields = ("created_at", "updated_at")
    fields = ("name", "key", "description", "variables", "is_active", "created_at", "updated_at")

    @admin.display(description="Approved")
    def approved_version(self, obj):
        revision = obj.approved_revision
        return f"v{revision.version}" if revision else "None"

    @admin.display(description="Awaiting review")
    def proposal_count(self, obj):
        return obj.revisions.filter(status=PromptRevisionStatus.PROPOSED).count()

    @admin.display(description="Actions")
    def propose_link(self, obj):
        url = reverse("admin:ideas_promptrevision_add") + f"?template={obj.pk}"
        return format_html('<a class="button" href="{}" title="Propose a new immutable revision">Propose change</a>', url)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PromptRevision)
class PromptRevisionAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("template", "version", "status", "created_by", "created_at", "review_link")
    list_filter = ("status", "template")
    search_fields = ("template__name", "template__key", "change_summary", "content")
    autocomplete_fields = ("template",)
    fields = ("template", "version", "status", "change_summary", "content", "created_by", "created_at", "reviewed_by", "reviewed_at")
    readonly_fields = ("version", "status", "created_by", "created_at", "reviewed_by", "reviewed_at")

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        template_id = request.GET.get("template")
        if template_id:
            initial["template"] = template_id
            template = PromptTemplate.objects.filter(pk=template_id).first()
            if template and template.approved_revision:
                initial["content"] = template.approved_revision.content
        return initial

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.status = PromptRevisionStatus.PROPOSED
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [
            path("<int:revision_id>/review/", self.admin_site.admin_view(self.review_view), name="ideas_promptrevision_review"),
        ] + super().get_urls()

    @admin.display(description="Review")
    def review_link(self, obj):
        return format_html('<a href="{}" title="Compare this proposal with the approved revision">Review diff</a>', reverse("admin:ideas_promptrevision_review", args=[obj.pk]))

    def review_view(self, request, revision_id):
        revision = get_object_or_404(PromptRevision.objects.select_related("template", "created_by", "reviewed_by"), pk=revision_id)
        baseline = revision.template.revisions.filter(status__in=[PromptRevisionStatus.APPROVED, PromptRevisionStatus.SUPERSEDED], version__lt=revision.version).order_by("-version").first()
        if request.method == "POST":
            decision = request.POST.get("decision")
            try:
                if decision == "approve":
                    revision.approve(request.user)
                    self.message_user(request, f"Approved {revision.template.name} v{revision.version}.", messages.SUCCESS)
                elif decision == "reject":
                    revision.reject(request.user)
                    self.message_user(request, f"Rejected {revision.template.name} v{revision.version}.", messages.WARNING)
                else:
                    self.message_user(request, "Choose approve or reject.", messages.ERROR)
                    return HttpResponseRedirect(request.path)
            except Exception as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return HttpResponseRedirect(request.path)
            return HttpResponseRedirect(reverse("admin:ideas_promptrevision_changelist"))
        diff = difflib.HtmlDiff(wrapcolumn=90).make_table(
            (baseline.content if baseline else "").splitlines(),
            revision.content.splitlines(),
            fromdesc=f"Approved v{baseline.version}" if baseline else "No approved version",
            todesc=f"Proposed v{revision.version}",
            context=False,
            numlines=3,
        )
        context = {
            **self.admin_site.each_context(request),
            "title": f"Review {revision.template.name} v{revision.version}",
            "revision": revision,
            "baseline": baseline,
            "diff_table": mark_safe(diff),
            "opts": self.model._meta,
        }
        return render(request, "admin/ideas/promptrevision/review.html", context)


@admin.register(IdeaRelationSuggestion)
class IdeaRelationSuggestionAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("source", "relation_type", "target", "status", "confidence", "similarity")
    list_filter = ("status", "relation_type", "classifier_model")
    search_fields = ("source__title", "target__title", "description", "evidence")
    autocomplete_fields = ("analyzed_idea", "source", "target", "reviewed_by", "accepted_relation")


@admin.register(Feed)
class FeedAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("title", "url", "is_active", "item_count", "last_fetched_at")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("title", "url")
    readonly_fields = ("etag", "last_modified", "last_fetched_at", "created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_items=Count("items"))

    @admin.display(description="Items", ordering="_items")
    def item_count(self, obj):
        return obj._items


@admin.register(FeedItem)
class FeedItemAdmin(TooltipAdminMixin, admin.ModelAdmin):
    # interest / info_value are yours to set, right from the list.
    list_display = (
        "title",
        "feed",
        "published_at",
        "interest",
        "info_value",
        "is_summarized",
    )
    list_editable = ("interest", "info_value")
    list_filter = ("feed", "interest", "info_value", "summary_model")
    search_fields = ("title", "summary", "guid", "link")
    list_select_related = ("feed", "summary_model")
    date_hierarchy = "published_at"
    readonly_fields = ("guid", "content_hash", "summarized_at", "created_at")

    @admin.display(boolean=True, description="Summarized")
    def is_summarized(self, obj):
        return obj.is_summarized


@admin.register(FeedItemAssessment)
class FeedItemAssessmentAdmin(TooltipAdminMixin, admin.ModelAdmin):
    list_display = ("item", "idea", "usefulness", "updated_at")
    list_filter = ("usefulness", "idea")
    search_fields = ("item__title", "idea__title", "relevance_note")
    list_select_related = ("item", "idea")


@admin.register(Profile)
class ProfileAdmin(TooltipAdminMixin, admin.ModelAdmin):
    """Read-mostly view of roles — the /users/ page is the primary place to edit these."""

    list_display = (
        "user",
        "role_admin",
        "role_current",
        "role_tracking",
        "role_archive",
        "role_add_ideas",
        "role_graph",
    )
    list_editable = (
        "role_admin",
        "role_current",
        "role_tracking",
        "role_archive",
        "role_add_ideas",
        "role_graph",
    )
    list_filter = ("role_admin", "role_current", "role_tracking", "role_archive", "role_add_ideas", "role_graph")
    search_fields = ("user__email", "user__username")
    list_select_related = ("user",)
