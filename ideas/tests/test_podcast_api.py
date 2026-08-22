import hashlib
import json
import shutil
import subprocess
import unittest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from ideas.models import EpisodeRun, EpisodeRunStatus

from .helpers import make_episode

TOKEN = "podcast-worker-test-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _tiny_mp3_bytes():
    """A real, ffprobe-decodable 0.3s silent MP3 — generated fresh rather than
    checked in, so the test actually exercises the verification subprocess."""
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
         "-t", "0.3", "-q:a", "9", "-f", "mp3", "pipe:1"],
        capture_output=True, timeout=10, check=True,
    )
    return proc.stdout


def make_run(episode=None, status=EpisodeRunStatus.AWAITING_AUDIO, **kwargs):
    return EpisodeRun.objects.create(
        episode=episode or make_episode(), status=status, **kwargs
    )


@override_settings(IDEAFLOW_PODCAST_WORKER_TOKEN=TOKEN)
class AuthTests(TestCase):
    def test_wrong_token_is_rejected(self):
        response = self.client.post(
            "/api/audio-jobs/claim/", content_type="application/json",
            HTTP_AUTHORIZATION="Bearer nope",
        )
        self.assertEqual(response.status_code, 401)

    def test_x_api_token_header_also_works(self):
        response = self.client.post(
            "/api/audio-jobs/claim/", content_type="application/json",
            HTTP_X_API_TOKEN=TOKEN,
        )
        self.assertEqual(response.status_code, 200)


@override_settings(IDEAFLOW_PODCAST_WORKER_TOKEN="")
class DisabledWhenNoTokenTests(TestCase):
    def test_disabled_without_a_configured_token(self):
        response = self.client.post(
            "/api/audio-jobs/claim/", content_type="application/json", **AUTH
        )
        self.assertEqual(response.status_code, 503)


@override_settings(IDEAFLOW_PODCAST_WORKER_TOKEN=TOKEN)
class ClaimTests(TestCase):
    def test_claims_the_oldest_awaiting_run(self):
        older = make_run()
        EpisodeRun.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=1)
        )
        make_run()  # newer, should not be claimed first

        response = self.client.post(
            "/api/audio-jobs/claim/", data=json.dumps({"worker_id": "mac-mini-1"}),
            content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["claimed"])
        self.assertEqual(body["run"]["id"], older.pk)
        older.refresh_from_db()
        self.assertEqual(older.status, EpisodeRunStatus.RENDERING)
        self.assertEqual(older.worker_id, "mac-mini-1")
        self.assertEqual(older.attempt_count, 1)
        self.assertIsNotNone(older.lease_expires_at)
        self.assertIsNotNone(older.started_at)

    def test_nothing_to_claim_returns_false(self):
        response = self.client.post(
            "/api/audio-jobs/claim/", content_type="application/json", **AUTH
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["claimed"])

    def test_reclaims_a_run_with_an_expired_lease(self):
        stale = make_run(
            status=EpisodeRunStatus.RENDERING,
            lease_expires_at=timezone.now() - timezone.timedelta(minutes=5),
            worker_id="old-worker",
            attempt_count=1,
        )
        response = self.client.post(
            "/api/audio-jobs/claim/", content_type="application/json", **AUTH
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["id"], stale.pk)
        stale.refresh_from_db()
        self.assertEqual(stale.attempt_count, 2)

    def test_does_not_reclaim_a_run_with_an_active_lease(self):
        make_run(
            status=EpisodeRunStatus.RENDERING,
            lease_expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        response = self.client.post(
            "/api/audio-jobs/claim/", content_type="application/json", **AUTH
        )
        self.assertFalse(response.json()["claimed"])

    def test_awaiting_runs_are_preferred_over_expired_reclaims(self):
        stale = make_run(
            status=EpisodeRunStatus.RENDERING,
            lease_expires_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        fresh = make_run(status=EpisodeRunStatus.AWAITING_AUDIO)
        response = self.client.post(
            "/api/audio-jobs/claim/", content_type="application/json", **AUTH
        )
        self.assertEqual(response.json()["run"]["id"], fresh.pk)
        self.assertNotEqual(response.json()["run"]["id"], stale.pk)


@override_settings(IDEAFLOW_PODCAST_WORKER_TOKEN=TOKEN)
class HeartbeatTests(TestCase):
    def test_extends_the_lease_and_records_progress(self):
        run = make_run(
            status=EpisodeRunStatus.RENDERING,
            lease_expires_at=timezone.now() + timezone.timedelta(minutes=1),
        )
        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/heartbeat/",
            data=json.dumps({"progress": {"segments_completed": 3, "segments_total": 10}}),
            content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertGreater(run.lease_expires_at, timezone.now() + timezone.timedelta(minutes=30))
        self.assertEqual(run.progress, {"segments_completed": 3, "segments_total": 10})
        self.assertIsNotNone(run.last_heartbeat_at)

    def test_rejects_heartbeat_on_a_non_rendering_run(self):
        run = make_run(status=EpisodeRunStatus.AWAITING_AUDIO)
        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/heartbeat/",
            content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 409)


@override_settings(IDEAFLOW_PODCAST_WORKER_TOKEN=TOKEN)
class FailTests(TestCase):
    def test_records_the_failure(self):
        run = make_run(status=EpisodeRunStatus.RENDERING)
        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/fail/",
            data=json.dumps({"error_class": "render_error", "error_detail": "OOM"}),
            content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, EpisodeRunStatus.FAILED)
        self.assertEqual(run.error_class, "render_error")
        self.assertEqual(run.error_detail, "OOM")
        self.assertIsNotNone(run.completed_at)

    def test_rejects_fail_on_a_non_rendering_run(self):
        run = make_run(status=EpisodeRunStatus.QUEUED)
        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/fail/",
            content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 409)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not available")
@override_settings(IDEAFLOW_PODCAST_WORKER_TOKEN=TOKEN)
class CompleteTests(TestCase):
    def test_verified_upload_promotes_the_episode_and_completes_the_run(self):
        run = make_run(status=EpisodeRunStatus.RENDERING)
        audio = _tiny_mp3_bytes()
        checksum = hashlib.sha256(audio).hexdigest()
        render_report = {"final": {"checksum_sha256": checksum}}

        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/complete/",
            {
                "audio": SimpleUploadedFile("episode.mp3", audio, content_type="audio/mpeg"),
                "render_report": json.dumps(render_report),
            },
            **AUTH,
        )
        self.assertEqual(response.status_code, 200, response.content)
        run.refresh_from_db()
        self.assertEqual(run.status, EpisodeRunStatus.READY_FOR_REVIEW)
        self.assertEqual(run.render_report, render_report)
        self.assertIsNotNone(run.completed_at)

        episode = run.episode
        episode.refresh_from_db()
        self.assertTrue(episode.audio_file)
        self.assertEqual(episode.audio_checksum_sha256, checksum)
        self.assertEqual(episode.audio_size_bytes, len(audio))
        self.assertGreater(episode.audio_duration_seconds, 0)
        # The staged copy is cleaned up; only the promoted file remains.
        with episode.audio_file.open("rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), checksum)
        episode.audio_file.delete(save=False)

    def test_checksum_mismatch_fails_the_run_and_promotes_nothing(self):
        run = make_run(status=EpisodeRunStatus.RENDERING)
        audio = _tiny_mp3_bytes()
        render_report = {"final": {"checksum_sha256": "0" * 64}}

        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/complete/",
            {
                "audio": SimpleUploadedFile("episode.mp3", audio, content_type="audio/mpeg"),
                "render_report": json.dumps(render_report),
            },
            **AUTH,
        )
        self.assertEqual(response.status_code, 422)
        run.refresh_from_db()
        self.assertEqual(run.status, EpisodeRunStatus.FAILED)
        self.assertEqual(run.error_class, "checksum_mismatch")
        episode = run.episode
        episode.refresh_from_db()
        self.assertFalse(episode.audio_file)

    def test_unplayable_upload_fails_the_run(self):
        run = make_run(status=EpisodeRunStatus.RENDERING)
        garbage = b"this is not an mp3 file at all"
        checksum = hashlib.sha256(garbage).hexdigest()
        render_report = {"final": {"checksum_sha256": checksum}}

        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/complete/",
            {
                "audio": SimpleUploadedFile("episode.mp3", garbage, content_type="audio/mpeg"),
                "render_report": json.dumps(render_report),
            },
            **AUTH,
        )
        self.assertEqual(response.status_code, 422)
        run.refresh_from_db()
        self.assertEqual(run.status, EpisodeRunStatus.FAILED)
        self.assertEqual(run.error_class, "unplayable_audio")

    def test_rejects_complete_on_a_non_rendering_run(self):
        run = make_run(status=EpisodeRunStatus.AWAITING_AUDIO)
        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/complete/",
            {"audio": SimpleUploadedFile("episode.mp3", b"x", content_type="audio/mpeg")},
            **AUTH,
        )
        self.assertEqual(response.status_code, 409)

    def test_missing_audio_file_is_rejected(self):
        run = make_run(status=EpisodeRunStatus.RENDERING)
        response = self.client.post(
            f"/api/audio-jobs/{run.pk}/complete/", {}, **AUTH
        )
        self.assertEqual(response.status_code, 400)
