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


class SemanticAPI:
    def __init__(self, *, api_key=None, api_base=None, embedding_model=None, classifier_model=None):
        self.api_key = api_key if api_key is not None else settings.IDEAFLOW_SEMANTIC_API_KEY
        self.api_base = api_base or settings.IDEAFLOW_SEMANTIC_API_BASE
        self.embedding_model = embedding_model or settings.IDEAFLOW_SEMANTIC_EMBEDDING_MODEL
        self.classifier_model = classifier_model or settings.IDEAFLOW_SEMANTIC_CLASSIFIER_MODEL
        if not self.api_key:
            raise ValueError("IDEAFLOW_SEMANTIC_API_KEY is not configured.")

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
        data = self._post(
            "/embeddings",
            {"model": self.embedding_model, "input": text, "dimensions": EMBEDDING_DIMENSIONS},
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
        prompt = f"""Identify only clear, useful semantic relationships between SOURCE and the candidates.
Allowed types: {allowed}. Direction matters: source depends_on target means SOURCE needs TARGET; source enables target means SOURCE makes TARGET possible; source supports/contradicts target means SOURCE's evidence supports/contradicts TARGET. Use related_to only for a strong connection that has no more precise type. Omit weak links.
Return JSON with a single `relationships` array. Each item must contain candidate_id (integer), relation_type, confidence (0..1), description (one sentence), and evidence (a short paraphrase of the research basis; never invent evidence).

SOURCE {source.pk}\n{semantic_text(source)[:10000]}

{candidate_text}"""
        data = self._post(
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
        )
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


def _store_suggestions(source, source_state, candidates, relationships, classifier_model):
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
                "reviewed_by": None,
                "reviewed_at": None,
                "accepted_relation": None,
            },
        )
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
            _store_suggestions(idea, state, candidates, relationships, api.classifier_model)
            state.status = SemanticStatus.READY
            state.processed_at = timezone.now()
            state.save(update_fields=["status", "processed_at", "updated_at"])
        return len(relationships)
    except Exception as exc:
        state.status = SemanticStatus.FAILED
        state.error = str(exc)[:4000]
        state.save(update_fields=["status", "error", "updated_at"])
        raise
