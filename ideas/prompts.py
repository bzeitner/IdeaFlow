from django.db import OperationalError, ProgrammingError

from .models import PromptRevisionStatus, PromptTemplate


DEFAULT_PROMPTS = {
    "semantic-relationship-classifier": """Identify only clear, useful semantic relationships between SOURCE and the candidates.
Allowed types: {allowed}. Direction matters: source depends_on target means SOURCE needs TARGET; source enables target means SOURCE makes TARGET possible; source supports/contradicts target means SOURCE's evidence supports/contradicts TARGET. Use related_to only for a strong connection that has no more precise type. Omit weak links.
Return JSON with a single `relationships` array. Each item must contain candidate_id (integer), relation_type, confidence (0..1), description (one sentence), and evidence (a short paraphrase of the research basis; never invent evidence).

SOURCE {source_id}
{source_text}

{candidate_text}""",
    "open-question-single": """Extract questions from this historical research report that still require a human decision or private context.
Exclude questions the agent can answer through research, rhetorical questions, resolved questions, and vague requests for more information.
Return JSON with one `questions` array. Each item must have `question` (a specific standalone question) and `confidence` (0..1). Return an empty array when none qualify.

Idea: {idea_title}
Research topic: {topic}
Report:
{report}""",
    "open-question-batch": """Extract only unresolved questions that require a human decision or private context from these historical reports.
Exclude researchable facts, rhetorical/resolved questions, and vague requests for more information.
Return JSON with `entries`, an array of objects containing entry_id and questions. Each question item has question and confidence (0..1). Include every entry, using an empty questions array when appropriate.

{reports}""",
}


def approved_prompt(key, default=None):
    """Return only an active, approved prompt; safely fall back during migrations."""
    fallback = DEFAULT_PROMPTS.get(key, default)
    try:
        template = PromptTemplate.objects.filter(key=key, is_active=True).first()
        if template:
            revision = template.revisions.filter(
                status=PromptRevisionStatus.APPROVED
            ).order_by("-version").first()
            if revision:
                return revision.content
    except (OperationalError, ProgrammingError):
        pass
    return fallback
