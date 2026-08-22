"""Small factory functions shared across the test modules."""

from django.contrib.auth.models import User

from ideas.models import AIModel, Category, Episode, Feed, FeedItem, Idea, PodcastShow, Stage, Status

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

_counter = iter(range(1, 100_000))


def make_category(**kwargs):
    kwargs.setdefault("name", f"Category {next(_counter)}")
    return Category.objects.create(**kwargs)


def make_stage(**kwargs):
    kwargs.setdefault("name", f"Stage {next(_counter)}")
    return Stage.objects.create(**kwargs)


def make_ai_model(**kwargs):
    kwargs.setdefault("name", f"Model {next(_counter)}")
    return AIModel.objects.create(**kwargs)


def make_idea(category=None, status=Status.CURRENT, **kwargs):
    kwargs.setdefault("title", f"Idea {next(_counter)}")
    return Idea.objects.create(
        category=category or make_category(), status=status, **kwargs
    )


def make_feed(**kwargs):
    kwargs.setdefault("url", f"https://example.com/feed-{next(_counter)}.xml")
    return Feed.objects.create(**kwargs)


def make_feed_item(feed=None, **kwargs):
    kwargs.setdefault("guid", f"guid-{next(_counter)}")
    return FeedItem.objects.create(feed=feed or make_feed(), **kwargs)


def make_podcast_show(**kwargs):
    kwargs.setdefault("idea", make_idea())
    kwargs.setdefault("slug", f"show-{next(_counter)}")
    kwargs.setdefault("title", "Test Show")
    return PodcastShow.objects.create(**kwargs)


def make_episode(show=None, **kwargs):
    kwargs.setdefault("show", show or make_podcast_show())
    kwargs.setdefault("slug", f"ep-{next(_counter)}")
    kwargs.setdefault("episode_number", next(_counter))
    kwargs.setdefault("title", "Test Episode")
    return Episode.objects.create(**kwargs)


def make_user(email="user@example.com", roles=(), **extra):
    """Create a user and grant it exactly the given Profile role fields."""
    username = extra.pop("username", email.split("@")[0])
    user = User.objects.create_user(username=username, email=email, **extra)
    profile = user.profile
    for role in roles:
        setattr(profile, role, True)
    if roles:
        profile.save()
    user.refresh_from_db()
    return user
