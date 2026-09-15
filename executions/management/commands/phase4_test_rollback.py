"""Exercise cutover rollback through API handlers without committing probe data."""

import json
from datetime import date, timedelta
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import RequestFactory
from django.urls import resolve, reverse
from django.utils import timezone

from executions.management.commands.phase4_reconcile import PHASE4_WORKFLOWS
from executions.models import CutoverMode, DeterministicJob, WorkflowCutover
from ideas.models import (
    Category, Episode, Idea, IdeaPersona, IdeaRelationSuggestion, Persona,
    PersonaReview, PodcastShow, RelationType, RelationshipCouncilReview,
    VoiceProfile, WeeklySummary,
)


def make_probe(workflow):
    """Return an API request and its expected durable projection population.

    Only synthetic subjects are used. These endpoints write database rows, with
    no file uploads, provider calls, or external job dispatch. Podcast workers
    cannot see the uncommitted render job.
    """
    marker = f"rollback-probe-{uuid4().hex}"
    category = Category.objects.create(name=marker)
    idea = Idea.objects.create(
        title=marker, category=category, repeat_enabled=True,
        persona_review_enabled=True, is_public=False,
    )
    # Newly created ideas inherit production's default personas. Limit the
    # synthetic idea to exactly the three synthetic personas below.
    IdeaPersona.objects.filter(idea=idea).delete()

    if workflow in {"execute", "critique"}:
        url = f"https://github.com/ideaflow-rollback-probe/{marker}/pull/1"
        idea.resources.create(label=marker, url=url)
        return (
            reverse("api_idea_reconcile_pr", kwargs={"pk": idea.pk}),
            {"url": url, "state": "CLOSED", "workflow": workflow},
            DeterministicJob.objects.filter(idempotency_key=f"pr:{url}:CLOSED"),
            200,
        )

    if workflow == "weekly_summary":
        period = date(9990, 1, 1)
        while WeeklySummary.objects.filter(period_start=period, period_end=period).exists():
            period -= timedelta(days=1)
        return (
            reverse("api_weekly_summary_list"),
            {"period_start": str(period), "period_end": str(period),
             "title": marker, "content": "Synthetic rollback verification."},
            WeeklySummary.objects.filter(period_start=period, period_end=period),
            201,
        )

    if workflow == "podcast_script":
        show = PodcastShow.objects.create(idea=idea, title=marker, slug=marker)
        voice_name = f"probe-{uuid4().hex[:14]}"
        VoiceProfile.objects.create(name=voice_name, speaker_label="host")
        return (
            reverse("api_idea_podcast_episode", kwargs={"pk": idea.pk}),
            {"title": marker, "script": {
                "schema_version": 1, "title": marker,
                "target_duration_seconds": 1,
                "segments": [{"speaker": "host", "voice_profile": voice_name,
                              "text": "Synthetic rollback verification.",
                              "emotion": None, "pause_after_ms": 0}],
                "citations": [],
            }},
            Episode.objects.filter(show=show),
            201,
        )

    personas = [Persona.objects.create(name=f"{marker}-{i}") for i in range(3)]
    IdeaPersona.objects.bulk_create([
        IdeaPersona(idea=idea, persona=persona, required=True) for persona in personas
    ])
    if workflow == "persona_council":
        return (
            reverse("api_idea_persona_review", kwargs={"pk": idea.pk}),
            {"proposal": {"reversible": True, "action_type": "test"},
             "votes": [{"persona_id": persona.pk, "decision": "abstain",
                        "rationale": "Synthetic rollback verification."}
                       for persona in personas]},
            PersonaReview.objects.filter(idea=idea),
            201,
        )

    target = Idea.objects.create(title=f"{marker}-target", category=category)
    suggestion = IdeaRelationSuggestion.objects.create(
        analyzed_idea=idea, source=idea, target=target,
        relation_type=RelationType.values[0],
        source_content_hash=marker, target_content_hash=marker,
        classifier_model="synthetic-rollback-probe",
    )
    return (
        reverse("api_relationship_council_submit", kwargs={"suggestion_pk": suggestion.pk}),
        {"votes": [{"persona_id": persona.pk, "decision": "abstain",
                    "provider": "claude" if i == 0 else "codex",
                    "model": "synthetic-rollback-probe",
                    "rationale": "Synthetic fixture; no provider was called."}
                   for i, persona in enumerate(personas)]},
        RelationshipCouncilReview.objects.filter(suggestion=suggestion),
        201,
    )


def exercise_rollback(workflow):
    steps = []
    with transaction.atomic():
        # Do not commit a global mode change or race an operator's update.
        cutover = WorkflowCutover.objects.select_for_update(nowait=True).get(
            workflow_key=workflow
        )
        original_mode = cutover.mode
        path, payload, projections, success_status = make_probe(workflow)
        for mode in (
            CutoverMode.AUTHORITATIVE, CutoverMode.SHADOW,
            CutoverMode.AUTHORITATIVE, CutoverMode.LEGACY,
            CutoverMode.AUTHORITATIVE, CutoverMode.FROZEN,
        ):
            WorkflowCutover.objects.filter(pk=cutover.pk).update(mode=mode)
            expected = success_status if mode in {CutoverMode.SHADOW, CutoverMode.LEGACY} else 409
            with transaction.atomic():
                request = RequestFactory().post(
                    path, data=json.dumps(payload), content_type="application/json",
                    HTTP_AUTHORIZATION=f"Bearer {settings.IDEAFLOW_API_TOKEN}",
                )
                match = resolve(path)
                response = match.func(request, *match.args, **match.kwargs)
                if response.status_code != expected:
                    raise CommandError(
                        f"{workflow}/{mode}: expected HTTP {expected}, got {response.status_code}."
                    )
                count = projections.count()
                if count != (1 if expected == success_status else 0):
                    raise CommandError(f"{workflow}/{mode}: unexpected projection count {count}.")
                if expected == 409:
                    error = json.loads(response.content).get("error", "")
                    reason = "writes are frozen" if mode == CutoverMode.FROZEN else "requires an attributed execution run"
                    if workflow not in error or reason not in error:
                        raise CommandError(f"{workflow}/{mode}: rejected for a reason other than cutover.")
                steps.append({"mode": mode, "http_status": response.status_code,
                              "projection_count": count})
                # Reuse the identical request after restoring authority; no
                # earlier successful write can trigger an unrelated conflict.
                transaction.set_rollback(True)
        # Restores the full original cutover record and discards every fixture,
        # projection, outcome, and queued render job even on a successful probe.
        transaction.set_rollback(True)
    return original_mode, steps


class Command(BaseCommand):
    help = "Test all Phase 4 rollback paths in rolled-back transactions; emit evidence JSON."

    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True)

    def handle(self, *args, **options):
        owner = options["owner"].strip()
        if not owner:
            raise CommandError("--owner must not be blank.")
        if not settings.IDEAFLOW_API_TOKEN:
            raise CommandError("IDEAFLOW_API_TOKEN must be configured to test authenticated API handlers.")
        missing = set(PHASE4_WORKFLOWS) - set(
            WorkflowCutover.objects.values_list("workflow_key", flat=True)
        )
        if missing:
            raise CommandError("Missing cutover records: " + ", ".join(sorted(missing)))
        evidence = {}
        for workflow in PHASE4_WORKFLOWS:
            original_mode, steps = exercise_rollback(workflow)
            evidence[workflow] = {
                "owner": owner, "tested_at": timezone.now().isoformat(), "result": "pass",
                "test_scope": "transactional_api_handlers",
                "original_mode": original_mode, "restored_mode": original_mode,
                "steps": steps,
                "notes": (
                    "Authenticated API handlers rejected unattributed writes in authoritative mode, "
                    "accepted and persisted synthetic projections in shadow and legacy modes, "
                    "and rejected again after restoring authority. Frozen writes rejected. "
                    "All probe transactions rolled back; original configuration and history retained. "
                    "In-process test; HTTP transport and worker processes were not exercised."
                ),
            }
        # No partial or passing evidence is emitted if any workflow fails.
        self.stdout.write(json.dumps(evidence, indent=2, sort_keys=True))
