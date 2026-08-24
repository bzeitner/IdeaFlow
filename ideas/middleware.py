from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .models import Profile

# Login sessions last two weeks (Django's default SESSION_COOKIE_AGE), so
# last_login is a poor signal of whether someone is actually still using the
# site. Track real activity instead, throttled so it costs at most one write
# per user per window rather than one per request.
LAST_SEEN_THROTTLE = timedelta(minutes=5)


class TrackLastSeenMiddleware:
    """Stamps Profile.last_seen_at on every authenticated request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        profile = getattr(user, "profile", None) if user is not None and user.is_authenticated else None
        if profile is not None:
            try:
                timezone.activate(ZoneInfo(profile.timezone_name))
            except ZoneInfoNotFoundError:
                timezone.deactivate()
        else:
            timezone.deactivate()
        try:
            response = self.get_response(request)
            if profile is not None:
                now = timezone.now()
                if not profile.last_seen_at or now - profile.last_seen_at > LAST_SEEN_THROTTLE:
                    Profile.objects.filter(pk=profile.pk).update(last_seen_at=now)
            return response
        finally:
            timezone.deactivate()
