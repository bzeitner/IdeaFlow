import hashlib
import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from pgvector.django import CosineDistance

from ideas.models import (
    Idea,
    IdeaRelation,
    IdeaRelationSuggestion,
    IdeaSemanticState,
    RelationProvenance,
    RelationType,
    SemanticGraphSettings,
    SemanticStatus,
    SuggestionStatus,
)
from ideas.prompts import approved_prompt

EMBEDDING_DIMENSIONS = 1536
CLASSIFIABLE_TYPES = {choice for choice, _label in RelationType.choices}


def semantic_text(idea):
    sections = [
        f"Title: {idea.title}",
        f"Summary: {idea.summary}",
        f"Executive summary: {idea.exec_summary}",
        f"Notes: {idea.notes}",
        f"Next action: {idea.next_action}",
    ]
    for entry in idea.research_entries.all():
        sections.append(
            f"Research — {entry.topic}\nFocus: {entry.focus}\n{entry.context}"
        )
    return "\n\n".join(section for section in sections if section.split(":", 1)[-1].strip())


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bounded_semantic_text(text, max_chars=None):
    """Keep semantic input deterministic and safely below provider token caps."""
    limit = max_chars or settings.IDEAFLOW_SEMANTIC_MAX_INPUT_CHARS
    if len(text) <= limit:
        return text
    marker = "\n\n[... older semantic context truncated ...]\n\n"
    available = max(0, limit - len(marker))
    head = (available * 2) // 3
    return f"{text[:head]}{marker}{text[-(available - head):]}"


class SemanticAPI:
    def __init__(self, *, api_key=None, api_base=None, embedding_model=None, classifier_model=None):
        self.api_key = api_key if api_key is not None else settings.IDEAFLOW_SEMANTIC_API_KEY
        self.api_base = api_base or settings.IDEAFLOW_SEMANTIC_API_BASE
        self.embedding_model = embedding_model or settings.IDEAFLOW_SEMANTIC_EMBEDDING_MODEL
        self.classifier_model = classifier_model or settings.IDEAFLOW_SEMANTIC_CLASSIFIER_MODEL
        if not self.api_key:
            raise ValueError("IDEAFLOW_SEMANTIC_API_KEY is not configured.")
        self.idea = None
        self.trace = None
        self.last_classification_run = None

    def begin(self, idea):
        self.idea = idea
        self.trace = None
        self.last_classification_run = None

    def _ensure_trace(self):
        if self.trace or not settings.IDEAFLOW_EXECUTION_FLAGS.get("instrumentation", False):
            return self.trace
        from executions.models import ApprovalStatus, WorkflowVersion
        from executions.services import start_trace

        version = WorkflowVersion.objects.select_related("workflow").filter(
            workflow__key="relationship_classification",
            workflow__is_active=True,
            status=ApprovalStatus.APPROVED,
        ).order_by("-version").first()
        if version is None:
            raise RuntimeError("No approved relationship_classification workflow exists.")
        self.trace, _created = start_trace(
            version, trigger="management_command", subject=self.idea,
            actor_label="process_semantic_graph",
        )
        return self.trace

    def _measured_post(self, path, payload, *, purpose, prompt_keys=()):
        trace = self._ensure_trace()
        if trace is None:
            return self._post(path, payload), None
        from executions.models import ModelConfiguration
        from executions.services import canonical_hash, complete_run, fail_run, start_run
        from ideas.models import PromptRevisionStatus, PromptTemplate

        model = str(payload["model"])
        frozen = {"provider": "openai-compatible", "model_identifier": model, "settings": {"api_base": self.api_base}}
        configuration = ModelConfiguration.objects.filter(content_hash=canonical_hash(frozen)).first()
        if configuration is None:
            configuration = ModelConfiguration.objects.create(
                provider="openai-compatible", model_identifier=model,
                settings={"api_base": self.api_base}, content_hash=canonical_hash(frozen),
            )
        manifest = []
        for key in prompt_keys:
            revision = PromptTemplate.objects.get(key=key, is_active=True).revisions.filter(
                status=PromptRevisionStatus.APPROVED
            ).order_by("-version").first()
            if revision is None:
                raise RuntimeError(f"No approved prompt revision exists for {key}.")
            manifest.append({"key": key, "version": revision.version, "sha256": content_hash(revision.content)})
        run, _created = start_run(
            trace, configuration, purpose=purpose,
            prompt_revision_manifest=manifest,
            rendered_input_hash=canonical_hash(payload),
            context_manifest={"endpoint": path},
        )
        try:
            data = self._post(path, payload)
        except Exception as exc:
            fail_run(
                run, error_class=type(exc).__name__, error_detail=str(exc),
                measurement_unavailable_reasons=["provider_request_failed"],
            )
            raise
        usage_data = data.get("usage") or {}
        usage = {
            "input_tokens": usage_data.get("prompt_tokens"),
            "output_tokens": usage_data.get("completion_tokens"),
            "total_tokens": usage_data.get("total_tokens"),
        }
        reasons = ["provider_cost_unavailable"]
        if not any(value is not None for value in usage.values()):
            reasons.append("provider_usage_unavailable")
        complete_run(
            run, output_hash=canonical_hash(data),
            provider_request_id=str(data.get("id") or ""), usage=usage,
            measurement_status="partial", measurement_unavailable_reasons=reasons,
        )
        return data, run

    def finish(self, error=None):
        if not self.trace:
            return
        from executions.services import complete_trace, fail_trace
        if error is None:
            complete_trace(self.trace)
        else:
            fail_trace(self.trace, reason=str(error))

    def _post(self, path, payload):
        request = Request(
            f"{self.api_base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Semantic API returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Semantic API request failed: {exc}") from exc

    def embed(self, text):
        text = bounded_semantic_text(text)
        data, _run = self._measured_post(
            "/embeddings",
            {"model": self.embedding_model, "input": text, "dimensions": EMBEDDING_DIMENSIONS},
            purpose="embedding",
        )
        vector = data["data"][0]["embedding"]
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise RuntimeError(f"Embedding model returned {len(vector)} dimensions; expected {EMBEDDING_DIMENSIONS}.")
        return vector

    def classify(self, source, candidates):
        candidate_text = "\n\n".join(
            f"CANDIDATE {idea.pk}\n{semantic_text(idea)[:5000]}" for idea, _similarity in candidates
        )
        allowed = ", ".join(sorted(CLASSIFIABLE_TYPES))
        prompt = approved_prompt("semantic-relationship-classifier").format(
            allowed=allowed,
            source_id=source.pk,
            source_text=semantic_text(source)[:10000],
            candidate_text=candidate_text,
        )
        data, run = self._measured_post(
            "/chat/completions",
            {
                "model": self.classifier_model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You extract conservative, evidence-backed knowledge-graph relationships."},
                    {"role": "user", "content": prompt},
                ],
            },
            purpose="classification",
            prompt_keys=("semantic-relationship-classifier",),
        )
        self.last_classification_run = run
        content = data["choices"][0]["message"]["content"]
        return json.loads(content).get("relationships", [])


def _cosine_similarity(left, right):
    dot = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / denominator if denominator else 0.0


def nearest_candidates(state, *, limit=None, min_similarity=None):
    limit = limit or settings.IDEAFLOW_SEMANTIC_CANDIDATES
    min_similarity = settings.IDEAFLOW_SEMANTIC_MIN_SIMILARITY if min_similarity is None else min_similarity
    states = IdeaSemanticState.objects.filter(
        status=SemanticStatus.READY,
        embedding_model=state.embedding_model,
        embedding__isnull=False,
    ).exclude(pk=state.pk).select_related("idea")
    if connection.vendor == "postgresql":
        ranked = states.annotate(distance=CosineDistance("embedding", state.embedding)).order_by("distance")[:limit]
        return [(row.idea, 1.0 - float(row.distance)) for row in ranked if 1.0 - float(row.distance) >= min_similarity]
    ranked = sorted(
        ((row.idea, _cosine_similarity(state.embedding, row.embedding)) for row in states),
        key=lambda item: item[1], reverse=True,
    )
    return [item for item in ranked[:limit] if item[1] >= min_similarity]


def accept_suggestion(suggestion):
    """Promote one suggestion, returning False when model validation rejects it."""
    try:
        relation, _created = IdeaRelation.objects.get_or_create(
            source=suggestion.source,
            target=suggestion.target,
            relation_type=suggestion.relation_type,
            defaults={
                "description": suggestion.description,
                "confidence": max(1, min(5, round(suggestion.confidence * 5))),
                "provenance": RelationProvenance.AGENT,
            },
        )
    except ValidationError:
        return False
    suggestion.status = SuggestionStatus.ACCEPTED
    suggestion.accepted_relation = relation
    suggestion.reviewed_at = timezone.now()
    suggestion.save(
        update_fields=["status", "accepted_relation", "reviewed_at", "updated_at"]
    )
    return True


def is_cyclic_dependency(source_id, target_id, relation_type):
    if relation_type != RelationType.DEPENDS_ON:
        return False
    return IdeaRelation(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
    )._creates_dependency_cycle()


def supersede_cyclic_suggestions():
    count = 0
    suggestions = IdeaRelationSuggestion.objects.filter(
        status=SuggestionStatus.PENDING,
        relation_type=RelationType.DEPENDS_ON,
    )
    for suggestion in suggestions:
        if is_cyclic_dependency(
            suggestion.source_id, suggestion.target_id, suggestion.relation_type
        ):
            suggestion.status = SuggestionStatus.SUPERSEDED
            suggestion.save(update_fields=["status", "updated_at"])
            count += 1
    return count


def auto_accept_pending(confidence_percent):
    threshold = confidence_percent / 100
    accepted = 0
    suggestions = IdeaRelationSuggestion.objects.filter(
        status=SuggestionStatus.PENDING,
        confidence__gt=threshold,
    ).select_related("source", "target")
    for suggestion in suggestions:
        accepted += int(accept_suggestion(suggestion))
    return accepted


def _store_suggestions(source, source_state, candidates, relationships, classifier_model, produced_by_run=None):
    candidate_map = {idea.pk: (idea, similarity) for idea, similarity in candidates}
    auto_accept_threshold = SemanticGraphSettings.load().auto_accept_confidence_percent / 100
    seen = set()
    for result in relationships:
        try:
            target_id = int(result["candidate_id"])
            relation_type = result["relation_type"]
            confidence = max(0.0, min(1.0, float(result["confidence"])))
        except (KeyError, TypeError, ValueError):
            continue
        if target_id not in candidate_map or relation_type not in CLASSIFIABLE_TYPES or confidence < 0.55:
            continue
        target, similarity = candidate_map[target_id]
        target_state = target.semantic_state
        source_id, normalized_target_id = source.pk, target.pk
        source_hash, target_hash = source_state.content_hash, target_state.content_hash
        if relation_type in IdeaRelation.SYMMETRIC_TYPES and source_id > normalized_target_id:
            source_id, normalized_target_id = normalized_target_id, source_id
            source_hash, target_hash = target_hash, source_hash
        lookup = {"source_id": source_id, "target_id": normalized_target_id, "relation_type": relation_type}
        if is_cyclic_dependency(source_id, normalized_target_id, relation_type):
            # A dependency that closes a path can never become canonical, so
            # do not surface it as a recommendation.
            continue
        seen.add((source_id, normalized_target_id, relation_type))
        existing = IdeaRelationSuggestion.objects.filter(**lookup).first()
        hashes_unchanged = existing and existing.source_content_hash == source_hash and existing.target_content_hash == target_hash
        if existing and existing.status == SuggestionStatus.REJECTED and hashes_unchanged:
            continue
        suggestion, _created = IdeaRelationSuggestion.objects.update_or_create(
            **lookup,
            defaults={
                "description": str(result.get("description", ""))[:2000],
                "analyzed_idea": source,
                "evidence": str(result.get("evidence", ""))[:4000],
                "confidence": confidence,
                "similarity": similarity,
                "status": SuggestionStatus.PENDING,
                "source_content_hash": source_hash,
                "target_content_hash": target_hash,
                "classifier_model": classifier_model,
                "produced_by_run": produced_by_run,
                "reviewed_by": None,
                "reviewed_at": None,
                "accepted_relation": None,
            },
        )
        if existing and not hashes_unchanged:
            # A council verdict applies only to the exact evidence snapshot it
            # reviewed. Materially changed source/target content gets a fresh pass.
            council_review = getattr(suggestion, "relationship_council_review", None)
            if council_review:
                council_review.delete()
        if confidence > auto_accept_threshold:
            # Cycles and other invalid relationships stay pending rather than
            # failing the entire semantic-processing batch.
            accept_suggestion(suggestion)
    for pending in IdeaRelationSuggestion.objects.filter(analyzed_idea=source, status=SuggestionStatus.PENDING):
        if (pending.source_id, pending.target_id, pending.relation_type) not in seen:
            pending.status = SuggestionStatus.SUPERSEDED
            pending.save(update_fields=["status", "updated_at"])


def process_idea(idea, *, api=None):
    api = api or SemanticAPI()
    if hasattr(api, "begin"):
        api.begin(idea)
    state, _created = IdeaSemanticState.objects.get_or_create(idea=idea)
    text = semantic_text(idea)
    digest = content_hash(text)
    try:
        state.status = SemanticStatus.PROCESSING
        state.error = ""
        state.save(update_fields=["status", "error", "updated_at"])
        if state.content_hash != digest or state.embedding_model != api.embedding_model or state.embedding is None:
            state.embedding = api.embed(text)
            state.content_hash = digest
            state.embedding_model = api.embedding_model
            state.save(update_fields=["embedding", "content_hash", "embedding_model", "updated_at"])
        candidates = nearest_candidates(state)
        relationships = api.classify(idea, candidates) if candidates else []
        with transaction.atomic():
            _store_suggestions(
                idea, state, candidates, relationships, api.classifier_model,
                produced_by_run=getattr(api, "last_classification_run", None),
            )
            state.status = SemanticStatus.READY
            state.processed_at = timezone.now()
            state.save(update_fields=["status", "processed_at", "updated_at"])
        if hasattr(api, "finish"):
            api.finish()
        return len(relationships)
    except Exception as exc:
        state.status = SemanticStatus.FAILED
        state.error = str(exc)[:4000]
        state.save(update_fields=["status", "error", "updated_at"])
        if hasattr(api, "finish"):
            api.finish(error=exc)
        raise
