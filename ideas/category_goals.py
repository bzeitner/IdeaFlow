"""Default per-category research goal text (seed values and "reset" targets)."""

GOAL_TEXT_MAX_LENGTH = 2000

# slug -> (display name, default goal text)
DEFAULT_GOALS = {
    "research": (
        "Research",
        "Track cutting-edge developments in this space for personal/technical understanding. "
        "Prioritize recency, primary sources, and depth over commercial viability.",
    ),
    "research-effort": (
        "Research Effort",
        "Sustain an ongoing research thread across repeat cycles — surface what's genuinely new "
        "since the last pass rather than re-summarizing prior findings.",
    ),
    "app": (
        "App",
        "Evaluate a standalone app or product concept for feasibility, scope, and whether it's "
        "worth building — not necessarily as a revenue driver.",
    ),
    "app-improvement": (
        "App Improvement",
        "Improve IdeaFlow itself. Ground findings in this repo's actual code/data model, not "
        "speculation, and converge on a concrete, scoped implementation plan.",
    ),
    "project": (
        "Project",
        "Converge toward a build/don't-build decision for a project with defined scope and an end state.",
    ),
    "side-project": (
        "Side Project",
        "Same as Project but lower-stakes/exploratory — bias toward a fast, cheap validation step "
        "over exhaustive research.",
    ),
    "passive-income": (
        "Passive Income",
        "Assess business viability: market size, competitors, unit economics, and realistic "
        "monetization path. Converge on a go/no-go.",
    ),
    "book": (
        "Book",
        "Develop content-production goals — outline, audience, and distribution plan rather than "
        "market feasibility.",
    ),
    "podcast": (
        "Podcast",
        "Same as Book, tuned for episodic/audio format and recurring production cadence.",
    ),
}


def default_goal_for(category):
    """Default goal for a Category instance (matched by slug, then name), or ''."""
    if category.slug in DEFAULT_GOALS:
        return DEFAULT_GOALS[category.slug][1]
    lowered = category.name.strip().lower()
    for _slug, (name, text) in DEFAULT_GOALS.items():
        if name.lower() == lowered:
            return text
    return ""
