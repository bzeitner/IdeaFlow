"""Phase 1 podcast worker API — the Mac mini's only way to talk to IdeaFlow.

Guarded by a single shared token in IDEAFLOW_PODCAST_WORKER_TOKEN, deliberately
separate from the general-purpose IDEAFLOW_API_TOKEN so the render worker
never holds broader agent privileges (see podcast_plan.md, "Phased rollout of
machine auth"). There is no per-job lease token yet — Phase 1 accepts the
weaker guarantee that any request bearing the shared worker token can act on
any currently-claimed run, and relies on EpisodeRun.status plus
lease_expires_at to reject stale or out-of-turn requests. claim_generation and
lease_token_hash are Phase 2.

    POST /api/audio-jobs/claim/               claim the next eligible job
    GET  /api/audio-jobs/<id>/                 read one job's manifest/status
    POST /api/audio-jobs/<id>/heartbeat/        extend the lease, report progress
    POST /api/audio-jobs/<id>/complete/         upload the finished episode
    POST /api/audio-jobs/<id>/fail/             report a render failure

The token goes in an `Authorization: Bearer <token>` (or `X-API-Token`) header,
same convention as the general agent API.
"""

import hashlib
import json
import shutil
import subprocess
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt

from .models import Episode, EpisodeRun, EpisodeRunStatus

# Runs a worker may claim: freshly queued for audio, or a previous claim whose
# lease expired without completing (the worker went offline, crashed, or lost
# power) — reclaiming here, lazily at claim time, does the same job a
# dedicated lease-reaper process would, without needing one for Phase 1's
# single-worker scale.
_CLAIMABLE_FRESH = EpisodeRunStatus.AWAITING_AUDIO
_CLAIMABLE_STALE = EpisodeRunStatus.RENDERING


def _provided_token(request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :].strip()
    return request.headers.get("X-API-Token", "").strip()


def require_podcast_worker_token(view):
    """Gate a view on the Phase 1 worker token. CSRF is exempted because auth
    is by header token, not session cookie — there's no cookie to forge
    against. Mirrors ideas.api.require_api_token, with its own token so the
    two credentials stay independent."""

    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = getattr(settings, "IDEAFLOW_PODCAST_WORKER_TOKEN", "")
        if not expected:
            return JsonResponse(
                {"error": "Podcast worker API disabled: set IDEAFLOW_PODCAST_WORKER_TOKEN to enable it."},
                status=503,
            )
        if not constant_time_compare(_provided_token(request), expected):
            return JsonResponse({"error": "Invalid or missing worker token."}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


def _run_to_dict(run):
    return {
        "id": run.pk,
        "episode_id": run.episode_id,
        "status": run.status,
        "worker_id": run.worker_id,
        "lease_expires_at": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
        "attempt_count": run.attempt_count,
        "manifest": run.manifest,
        "progress": run.progress,
    }


@require_podcast_worker_token
@transaction.atomic
def claim_audio_job(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    worker_id = str(payload.get("worker_id") or "").strip()[:100]

    now = timezone.now()
    candidate = (
        EpisodeRun.objects.select_for_update(skip_locked=True)
        .filter(status=_CLAIMABLE_FRESH)
        .order_by("created_at")
        .first()
        or EpisodeRun.objects.select_for_update(skip_locked=True)
        .filter(status=_CLAIMABLE_STALE, lease_expires_at__lte=now)
        .order_by("created_at")
        .first()
    )
    if candidate is None:
        return JsonResponse({"claimed": False})

    candidate.status = EpisodeRunStatus.RENDERING
    candidate.worker_id = worker_id
    candidate.lease_expires_at = now + timezone.timedelta(seconds=settings.IDEAFLOW_PODCAST_LEASE_SECONDS)
    candidate.attempt_count += 1
    if candidate.started_at is None:
        candidate.started_at = now
    candidate.save(
        update_fields=["status", "worker_id", "lease_expires_at", "attempt_count", "started_at"]
    )
    return JsonResponse({"claimed": True, "run": _run_to_dict(candidate)})


@require_podcast_worker_token
def audio_job_detail(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    run = get_object_or_404(EpisodeRun, pk=pk)
    return JsonResponse({"run": _run_to_dict(run)})


@require_podcast_worker_token
@transaction.atomic
def audio_job_heartbeat(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    run = get_object_or_404(EpisodeRun.objects.select_for_update(), pk=pk)
    if run.status != EpisodeRunStatus.RENDERING:
        return JsonResponse({"error": f"Run is {run.status}, not rendering."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    progress = payload.get("progress")
    now = timezone.now()
    run.lease_expires_at = now + timezone.timedelta(seconds=settings.IDEAFLOW_PODCAST_LEASE_SECONDS)
    run.last_heartbeat_at = now
    update_fields = ["lease_expires_at", "last_heartbeat_at"]
    if isinstance(progress, dict):
        run.progress = progress
        update_fields.append("progress")
    run.save(update_fields=update_fields)
    return JsonResponse({"run": _run_to_dict(run)})


@require_podcast_worker_token
@transaction.atomic
def audio_job_fail(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    run = get_object_or_404(EpisodeRun.objects.select_for_update(), pk=pk)
    if run.status != EpisodeRunStatus.RENDERING:
        return JsonResponse({"error": f"Run is {run.status}, not rendering."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    run.status = EpisodeRunStatus.FAILED
    run.error_class = str(payload.get("error_class") or "")[:100]
    run.error_detail = str(payload.get("error_detail") or "")
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "error_class", "error_detail", "completed_at"])
    return JsonResponse({"run": _run_to_dict(run)})


def _probe_duration_seconds(path):
    """Runs ffprobe against a staged file. Returns the duration in seconds, or
    None if the file isn't a decodable, playable audio file. Fails closed —
    any subprocess error, timeout, or unparsable output is treated the same
    as "not verified."."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        duration = float(result.stdout.decode().strip())
    except (ValueError, UnicodeDecodeError):
        return None
    return duration if duration > 0 else None


def _sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@require_podcast_worker_token
@transaction.atomic
def audio_job_complete(request, pk):
    """Direct authenticated multipart upload — one trusted worker uploading to
    one Django app the same person controls (see podcast_plan.md, "Audio
    storage," Phase 1). The uploaded file is staged under a server-controlled
    path, never reachable at its public enclosure URL until ffprobe/checksum
    verification passes; verification failure leaves the episode unpublished
    and the run failed."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    run = get_object_or_404(
        EpisodeRun.objects.select_for_update().select_related("episode"), pk=pk
    )
    if run.status != EpisodeRunStatus.RENDERING:
        return JsonResponse({"error": f"Run is {run.status}, not rendering."}, status=409)

    uploaded = request.FILES.get("audio")
    if uploaded is None:
        return JsonResponse({"error": "audio file is required."}, status=400)
    if uploaded.size > settings.IDEAFLOW_EPISODE_AUDIO_MAX_BYTES:
        return JsonResponse({"error": "Episode audio exceeds the configured size limit."}, status=413)
    try:
        render_report = json.loads(request.POST.get("render_report") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "render_report must be valid JSON."}, status=400)
    if not isinstance(render_report, dict):
        return JsonResponse({"error": "render_report must be a JSON object."}, status=400)

    def _fail(error_class, error_detail, status=422):
        run.status = EpisodeRunStatus.FAILED
        run.error_class = error_class
        run.error_detail = error_detail
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error_class", "error_detail", "completed_at"])
        return JsonResponse({"error": error_detail}, status=status)

    # Stage under a path the worker cannot influence beyond the run id it
    # already owns — never a name derived from the upload's own filename.
    staging_dir = Path(settings.MEDIA_ROOT) / "podcast_incoming" / str(run.pk)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / "episode.mp3"
    try:
        with open(staged_path, "wb") as fh:
            for chunk in uploaded.chunks():
                fh.write(chunk)

        actual_checksum = _sha256_of(staged_path)
        actual_size = staged_path.stat().st_size
        reported = render_report.get("final") if isinstance(render_report.get("final"), dict) else {}
        reported_checksum = reported.get("checksum_sha256")
        if not reported_checksum or not constant_time_compare(reported_checksum, actual_checksum):
            return _fail("checksum_mismatch", "Uploaded file's checksum does not match render_report.")

        duration = _probe_duration_seconds(staged_path)
        if duration is None:
            return _fail("unplayable_audio", "ffprobe could not verify the uploaded file is playable audio.")

        # Promote: a local rename, not a copy — never reachable at its public
        # path until every check above has passed.
        episode = run.episode
        final_dir = (
            Path(settings.MEDIA_ROOT) / "podcast_episodes"
            / f"{episode.created_at:%Y}" / f"{episode.created_at:%m}"
        )
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / f"{episode.guid}.mp3"
        staged_path.replace(final_path)

        episode.audio_file.name = str(final_path.relative_to(settings.MEDIA_ROOT))
        episode.audio_checksum_sha256 = actual_checksum
        episode.audio_mime_type = "audio/mpeg"
        episode.audio_duration_seconds = duration
        episode.audio_size_bytes = actual_size
        episode.save(
            update_fields=[
                "audio_file", "audio_checksum_sha256", "audio_mime_type",
                "audio_duration_seconds", "audio_size_bytes", "updated_at",
            ]
        )

        run.status = EpisodeRunStatus.READY_FOR_REVIEW
        run.render_report = render_report
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "render_report", "completed_at"])
        return JsonResponse({"run": _run_to_dict(run), "episode_id": episode.pk})
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


urlpatterns = [
    path("audio-jobs/claim/", claim_audio_job, name="api_audio_job_claim"),
    path("audio-jobs/<int:pk>/", audio_job_detail, name="api_audio_job_detail"),
    path("audio-jobs/<int:pk>/heartbeat/", audio_job_heartbeat, name="api_audio_job_heartbeat"),
    path("audio-jobs/<int:pk>/complete/", audio_job_complete, name="api_audio_job_complete"),
    path("audio-jobs/<int:pk>/fail/", audio_job_fail, name="api_audio_job_fail"),
]
