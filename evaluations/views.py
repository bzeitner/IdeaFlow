"""Session-authenticated UI endpoints. No machine-token authentication."""
import uuid
from datetime import datetime, timezone as dt_timezone

from django import forms
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import JsonResponse, Http404
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from . import interactions
from .models import EvaluationExposure, HumanFeedback, EvaluationResult
from executions.services import canonical_hash
from executions.models import OutcomeEvent

SALT = "evaluations.rendered-output.v1"
STATE_LABELS = {
    "unknown": "No viewing or feedback recorded",
    "not_exposed": "Not viewed in this reporting window",
    "exposed_without_feedback": "Viewed, no feedback yet",
    "positive": "Positive feedback",
    "negative": "Negative feedback",
    "mixed": "Mixed feedback",
}


def panel_for(user, kind, obj):
    """GET renders signed metadata; it never creates an exposure or feedback row."""
    try:
        fresh, identity, source = interactions.target_for(user, kind, obj.pk)
    except PermissionDenied:
        return None
    if (interactions.projected_hash(kind, obj) != identity["output_hash"] or
            obj.produced_by_run_id != fresh.produced_by_run_id):
        # An update between view loading and panel construction must never bind
        # the older rendered report to the newer version's feedback token.
        return None
    token = signing.dumps({"actor": user.pk, "identity": identity, "session": str(uuid.uuid4()), "source": source}, salt=SALT)
    history = HumanFeedback.objects.filter(actor_user_id=user.pk, target_kind=kind, target_id=obj.pk).prefetch_related("outcome_links").order_by("-created_at")[:20]
    results = EvaluationResult.objects.none()
    if identity["producing_run_id"]:
        results = EvaluationResult.objects.filter(evaluated_run_id=identity["producing_run_id"], output_hash=identity["run_output_hash"]).select_related("evaluator_version__evaluator", "grader_run").order_by("-created_at")[:5]
    can_edit = False
    if interactions.enabled() and kind == "research":
        try:
            interactions.target_for(user, kind, obj.pk, edit=True)
            can_edit = True
        except PermissionDenied:
            pass
    corrections = HumanFeedback.objects.filter(actor_user_id=user.pk, **identity, correction__isnull=True).exclude(action="edit").order_by("-created_at")[:20]
    outcomes = OutcomeEvent.objects.filter(idea_id=obj.idea_id, run_id=identity["producing_run_id"]).order_by("-occurred_at")[:30] if kind == "research" and identity["producing_run_id"] else []
    state = interactions.feedback_state(user, identity, since=datetime.min.replace(tzinfo=dt_timezone.utc))
    return {"token": token, "enabled": interactions.enabled(), "history": history,
            "corrections": corrections, "outcomes": outcomes,
            "results": results, "output_hash": identity["output_hash"],
            "state": state, "state_label": STATE_LABELS[state],
            "observe_id": f"output-{kind}-{obj.pk}", "request_key": str(uuid.uuid4()),
            "can_edit": can_edit, "target_id": obj.pk}


def parse_target(request):
    data = signing.loads(request.POST.get("target", ""), salt=SALT, max_age=86400)
    if data["actor"] != request.user.pk:
        raise PermissionDenied
    return data


@login_required
@require_POST
def interaction(request):
    try:
        data = parse_target(request)
        operation = request.POST.get("operation")
        if operation == "exposure":
            if request.POST.get("visible") != "true":
                raise ValidationError("A visible output is required.")
            row, created = interactions.record_exposure(request.user, data["identity"], view_session=data["session"])
        elif operation == "feedback":
            # Link only a known exposure from this actor/output/view session;
            # posting feedback by itself must never invent one.
            exposure = EvaluationExposure.objects.filter(actor_user_id=request.user.pk, **data["identity"], view_session=data["session"]).first()
            request_key = str(uuid.UUID(request.POST.get("request_key", "")))
            existing = HumanFeedback.objects.filter(idempotency_key=canonical_hash({"actor": request.user.pk, "request": request_key})).first()
            exposure_id = existing.exposure_id if existing else exposure.pk if exposure else None
            rating = request.POST.get("rating", "")
            row, created = interactions.record_feedback(
                request.user, data["identity"], action=request.POST.get("action", ""),
                request_key=request_key, reason=request.POST.get("reason", ""),
                rating=int(rating) if rating else None, exposure_id=exposure_id,
                supersedes_id=int(request.POST["supersedes"]) if request.POST.get("supersedes") else None,
                source=data["source"],
            )
        elif operation == "outcome":
            feedback = HumanFeedback.objects.get(pk=request.POST.get("feedback_id"))
            if any(str(getattr(feedback, key)) != str(value) for key, value in data["identity"].items()):
                raise ValidationError("Outcome link target mismatch.")
            row, created = interactions.link_outcome(request.user, feedback.pk, request.POST.get("outcome_id"))
        else:
            raise ValidationError("Unknown interaction.")
    except PermissionDenied:
        return JsonResponse({"error": "Feedback is disabled or this output is not accessible."}, status=403)
    except (signing.BadSignature, ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return JsonResponse({"error": "Invalid or expired request. Reload the output."}, status=400)
    except (ValidationError, IntegrityError):
        return JsonResponse({"error": "Feedback could not be saved. Check the fields and reload if the output changed."}, status=409)
    return JsonResponse({"id": row.pk, "created": created})


class ResearchEditForm(forms.Form):
    context = forms.CharField(max_length=200000, widget=forms.Textarea(attrs={"rows": 24}))
    target = forms.CharField(widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)


@login_required
def edit_research(request, pk):
    interactions.require_enabled()
    try:
        obj, identity, source = interactions.target_for(request.user, "research", pk, edit=True)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    if request.method == "POST":
        form = ResearchEditForm(request.POST)
        if form.is_valid():
            try:
                data = parse_target(request)
                if data["identity"]["target_kind"] != "research" or data["identity"]["target_id"] != pk:
                    raise PermissionDenied
                interactions.edit_research(request.user, data["identity"], context=form.cleaned_data["context"], request_key=form.cleaned_data["request_key"])
            except (ValidationError, signing.BadSignature, IntegrityError):
                form.add_error(None, "Edit could not be saved. Reload to check for changed content.")
            else:
                return redirect("ideas:view_research_entry", pk=obj.idea_id, entry_pk=obj.pk)
    else:
        panel = panel_for(request.user, "research", obj)
        form = ResearchEditForm(initial={"context": obj.context, "target": panel["token"], "request_key": uuid.uuid4()})
    return render(request, "evaluations/research_edit.html", {"entry": obj, "form": form})
