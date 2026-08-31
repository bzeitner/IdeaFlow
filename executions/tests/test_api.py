import hashlib
import json

from django.test import TestCase, override_settings

from executions.models import ExecutionTrace, LLMRun, ServicePrincipal, ToolInvocation
from ideas.models import FeedItemAssessment, IdeaFeed
from ideas.tests.helpers import make_feed_item, make_idea


FLAGS_ON = {
    "instrumentation": True,
    "gateway": False,
    "projections": False,
    "feedback": False,
    "experiments": False,
}


@override_settings(IDEAFLOW_EXECUTION_FLAGS=FLAGS_ON)
class ExecutionApiTests(TestCase):
    token = "execution-test-token"

    def setUp(self):
        self.principal = ServicePrincipal.objects.create(
            name="test-worker",
            token_hash=ServicePrincipal.hash_token(self.token),
            scopes=["execution:write", "execution:read"],
        )
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        self.idea = make_idea(title="Measured idea")

    def post(self, path, body, **headers):
        return self.client.post(
            path,
            data=json.dumps(body),
            content_type="application/json",
            **(headers or self.auth),
        )

    def start_trace(self, idempotency_key="trace-one"):
        response = self.post(
            "/api/executions/v1/traces/",
            {
                "workflow": "feed_score",
                "subject": {"type": "idea", "id": self.idea.pk},
                "trigger": "test",
                "idempotency_key": idempotency_key,
            },
        )
        self.assertIn(response.status_code, (200, 201), response.content)
        return response.json()

    def start_run(self, trace_id, idempotency_key="run-one"):
        response = self.post(
            f"/api/executions/v1/traces/{trace_id}/runs/",
            {
                "provider": "claude",
                "model": "claude-haiku-4-5",
                "purpose": "classification",
                "prompt_keys": ["agent-feed-scoring", "shared-standards"],
                "rendered_input_hash": hashlib.sha256(b"prompt").hexdigest(),
                "idempotency_key": idempotency_key,
            },
        )
        self.assertIn(response.status_code, (200, 201), response.content)
        return response.json()

    def test_authentication_scope_and_disabled_flag(self):
        missing = self.post("/api/executions/v1/traces/", {}, HTTP_AUTHORIZATION="")
        self.assertEqual(missing.status_code, 401)
        self.principal.scopes = ["execution:read"]
        self.principal.save(update_fields=["scopes"])
        forbidden = self.post("/api/executions/v1/traces/", {})
        self.assertEqual(forbidden.status_code, 403)
        with override_settings(IDEAFLOW_EXECUTION_FLAGS={**FLAGS_ON, "instrumentation": False}):
            disabled = self.post("/api/executions/v1/traces/", {})
        self.assertEqual(disabled.status_code, 503)

    def test_trace_run_tool_and_completion_are_idempotent(self):
        trace = self.start_trace()
        same_trace = self.start_trace()
        self.assertEqual(trace["id"], same_trace["id"])
        self.assertEqual(ExecutionTrace.objects.count(), 1)

        run = self.start_run(trace["id"])
        same_run = self.start_run(trace["id"])
        self.assertEqual(run["id"], same_run["id"])
        self.assertEqual(LLMRun.objects.count(), 1)

        tool_response = self.post(
            f"/api/executions/v1/runs/{run['id']}/tools/",
            {"tool_name": "fetch", "idempotency_key": "tool-one"},
        )
        self.assertEqual(tool_response.status_code, 201, tool_response.content)
        tool_id = tool_response.json()["id"]
        completed_tool = self.post(
            f"/api/executions/v1/tools/{tool_id}/complete/",
            {"response_hash": hashlib.sha256(b"tool output").hexdigest()},
        )
        self.assertEqual(completed_tool.status_code, 200)
        self.assertEqual(ToolInvocation.objects.get().status, "succeeded")

        run_response = self.post(
            f"/api/executions/v1/runs/{run['id']}/complete/",
            {
                "output_hash": hashlib.sha256(b"answer").hexdigest(),
                "measurement_status": "partial",
                "measurement_unavailable_reasons": ["provider_usage_unavailable"],
                "usage": {},
            },
        )
        self.assertEqual(run_response.status_code, 200, run_response.content)
        trace_response = self.post(
            f"/api/executions/v1/traces/{trace['id']}/complete/", {}
        )
        self.assertEqual(trace_response.status_code, 200, trace_response.content)
        self.assertEqual(trace_response.json()["status"], "succeeded")

    @override_settings(IDEAFLOW_API_TOKEN="general-token")
    def test_feed_projection_inherits_active_run(self):
        trace = self.start_trace("projection-trace")
        run = self.start_run(trace["id"], "projection-run")
        item = make_feed_item(content="Evidence")
        IdeaFeed.objects.create(idea=self.idea, feed=item.feed, rating=5)
        response = self.client.post(
            f"/api/feed-items/{item.pk}/summarize/",
            data=json.dumps(
                {
                    "idea_id": self.idea.pk,
                    "summary": "Summary",
                    "model": "other",
                    "usefulness": 4,
                    "relevance_note": "Relevant",
                    "execution_run_id": run["id"],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer general-token",
        )
        self.assertEqual(response.status_code, 201, response.content)
        item.refresh_from_db()
        assessment = FeedItemAssessment.objects.get(item=item, idea=self.idea)
        self.assertEqual(str(item.summarized_by_run_id), run["id"])
        self.assertEqual(str(assessment.produced_by_run_id), run["id"])


class ExecutionFlagTests(TestCase):
    def test_disabled_precedes_authentication(self):
        response = self.client.post(
            "/api/executions/v1/traces/", data="{}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 503)
