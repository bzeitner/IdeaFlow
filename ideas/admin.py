from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from .models import (
    AIModel,
    Category,
    Feed,
    FeedItem,
    Idea,
    Profile,
    Resource,
    ResearchEntry,
    Stage,
)

admin.site.site_header = "IdeaFlow Administration"
admin.site.site_title = "IdeaFlow"
admin.site.index_title = "Manage ideas and dropdown options"


class LookupAdmin(admin.ModelAdmin):
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
    pass


@admin.register(Stage)
class StageAdmin(LookupAdmin):
    pass


@admin.register(AIModel)
class AIModelAdmin(LookupAdmin):
    pass


class ResourceInline(admin.TabularInline):
    model = Resource
    extra = 1


class ResearchEntryInline(admin.TabularInline):
    model = ResearchEntry
    extra = 1
    fields = ("topic", "focus", "occurred_at", "model", "effort", "quality", "tokens_used")


@admin.register(Idea)
class IdeaAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "status", "stage", "interest_level", "is_public", "rank")
    list_editable = ("status", "is_public", "rank")
    list_filter = ("status", "is_public", "category", "stage", "interest_level")
    search_fields = ("title", "summary", "notes")
    list_select_related = ("category", "stage")
    inlines = [ResourceInline, ResearchEntryInline]


@admin.register(ResearchEntry)
class ResearchEntryAdmin(admin.ModelAdmin):
    list_display = ("topic", "idea", "model", "occurred_at", "effort", "quality", "tokens_used")
    list_filter = ("model", "effort", "quality")
    search_fields = ("topic", "focus", "context", "idea__title")
    list_select_related = ("idea", "model")
    date_hierarchy = "occurred_at"


@admin.register(Feed)
class FeedAdmin(admin.ModelAdmin):
    list_display = ("title", "url", "is_active", "item_count", "last_fetched_at")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("title", "url")
    filter_horizontal = ("ideas",)
    readonly_fields = ("etag", "last_modified", "last_fetched_at", "created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_items=Count("items"))

    @admin.display(description="Items", ordering="_items")
    def item_count(self, obj):
        return obj._items


@admin.register(FeedItem)
class FeedItemAdmin(admin.ModelAdmin):
    # interest / info_value are yours to set, right from the list.
    list_display = (
        "title",
        "feed",
        "published_at",
        "usefulness",
        "interest",
        "info_value",
        "is_summarized",
    )
    list_editable = ("interest", "info_value")
    list_filter = ("feed", "usefulness", "interest", "info_value", "summary_model")
    search_fields = ("title", "summary", "guid", "link")
    list_select_related = ("feed", "summary_model")
    date_hierarchy = "published_at"
    readonly_fields = ("guid", "content_hash", "summarized_at", "created_at")

    @admin.display(boolean=True, description="Summarized")
    def is_summarized(self, obj):
        return obj.is_summarized


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """Read-mostly view of roles — the /users/ page is the primary place to edit these."""

    list_display = (
        "user",
        "role_admin",
        "role_current",
        "role_tracking",
        "role_archive",
        "role_add_ideas",
    )
    list_editable = (
        "role_admin",
        "role_current",
        "role_tracking",
        "role_archive",
        "role_add_ideas",
    )
    list_filter = ("role_admin", "role_current", "role_tracking", "role_archive", "role_add_ideas")
    search_fields = ("user__email", "user__username")
    list_select_related = ("user",)
