from django.db.models import OuterRef, Subquery

from .models import HelpMessage


def help_inbox(request):
    """Expose the unanswered support count for the admin banner."""
    if not request.user.is_authenticated or not request.user.profile.role_admin:
        return {}
    latest_message_id = (
        HelpMessage.objects.filter(user_id=OuterRef("user_id"))
        .order_by("-created_at", "-pk")
        .values("pk")[:1]
    )
    pending_count = (
        HelpMessage.objects.filter(admin_response=False, pk=Subquery(latest_message_id))
        .order_by()
        .values("user_id")
        .distinct()
        .count()
    )
    return {"pending_help_count": pending_count}
