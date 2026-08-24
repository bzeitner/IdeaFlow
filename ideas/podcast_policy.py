"""Shared duration policy for podcast configuration, serialization, and API validation."""

import math


PODCAST_MAX_DURATION_SECONDS = 60 * 60
PODCAST_WORDS_PER_SECOND = 2.5
PODCAST_MINIMUM_TARGET_FRACTION = 0.5


def minimum_script_word_count(duration_seconds):
    """Minimum spoken words needed to represent half the configured runtime."""
    if duration_seconds is None:
        return None
    return math.ceil(
        duration_seconds * PODCAST_WORDS_PER_SECOND * PODCAST_MINIMUM_TARGET_FRACTION
    )
