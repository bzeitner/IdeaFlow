import re

from django.template.defaultfilters import linebreaksbr, urlize
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe


RESEARCH_REFERENCE_RE = re.compile(
    r"(?P<label>\b(?:(?:research\s+)?(?:entry|effort))\s+#(?P<id>\d+)\b)",
    re.IGNORECASE,
)


def render_research_context(value, valid_entry_ids):
    """Render report text and link references to efforts on the current idea."""
    rendered = str(linebreaksbr(urlize(escape(value or ""))))
    valid_ids = set(valid_entry_ids)

    def replace(match):
        entry_id = int(match.group("id"))
        if entry_id not in valid_ids:
            return match.group(0)
        return str(
            format_html(
                '<a href="#research-entry-{}">{}</a>',
                entry_id,
                match.group("label"),
            )
        )

    return mark_safe(RESEARCH_REFERENCE_RE.sub(replace, rendered))
