from datetime import timedelta
from xml.etree import ElementTree as ET

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ideas.graph.capabilities import token_hash
from ideas.models import GraphAccessCapability, IdeaRelation, RelationType

from .helpers import MODEL_BACKEND, make_idea, make_user


GRAPH_LAB = {
    "IDEAFLOW_GRAPH_LAB_ENABLED": True,
    "IDEAFLOW_GRAPH_LAB_ORIGIN": "https://graph-lab.example.com",
    "STORAGES": {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
}


@override_settings(**GRAPH_LAB)
class GraphLabTests(TestCase):
    def setUp(self):
        self.user = make_user(roles=["role_graph"])
        self.client.force_login(self.user, backend=MODEL_BACKEND)

    def issue(self, **data):
        response = self.client.post(reverse("ideas:graph_lab_capability"), data)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def export(self, token, **headers):
        return self.client.get(
            reverse("ideas:graph_lab_export"),
            HTTP_ORIGIN=GRAPH_LAB["IDEAFLOW_GRAPH_LAB_ORIGIN"],
            HTTP_AUTHORIZATION=f"GraphCapability {token}",
            **headers,
        )

    def test_launcher_requires_role_and_has_isolated_origin_controls(self):
        response = self.client.get(reverse("ideas:graph_lab"))
        self.assertContains(response, 'sandbox="allow-scripts allow-same-origin allow-downloads"')
        self.assertContains(response, GRAPH_LAB["IDEAFLOW_GRAPH_LAB_ORIGIN"])
        self.assertNotContains(response, "GraphCapability")
        self.assertIn("frame-src https://graph-lab.example.com", response["Content-Security-Policy"])

        other = make_user(email="other@example.com", roles=["role_current"])
        self.client.force_login(other, backend=MODEL_BACKEND)
        denied = self.client.get(reverse("ideas:graph_lab"))
        self.assertRedirects(denied, reverse("ideas:home"), fetch_redirect_response=False)

    def test_capability_is_hashed_short_lived_and_never_put_in_url(self):
        payload = self.issue(archived="1")
        capability = GraphAccessCapability.objects.get()
        self.assertNotEqual(capability.token_hash, payload["capability"])
        self.assertEqual(capability.token_hash, token_hash(payload["capability"]))
        self.assertNotIn(payload["capability"], payload["export_url"])
        self.assertEqual(capability.filters["archived"], True)

    def test_export_requires_exact_origin_and_authorization_header(self):
        token = self.issue()["capability"]
        denied = self.client.get(
            reverse("ideas:graph_lab_export"),
            HTTP_ORIGIN="https://evil.example",
            HTTP_AUTHORIZATION=f"GraphCapability {token}",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", denied)
        missing = self.client.get(
            reverse("ideas:graph_lab_export"),
            HTTP_ORIGIN=GRAPH_LAB["IDEAFLOW_GRAPH_LAB_ORIGIN"],
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing["Access-Control-Allow-Origin"], GRAPH_LAB["IDEAFLOW_GRAPH_LAB_ORIGIN"])

    def test_preflight_is_exact_and_read_only(self):
        response = self.client.options(
            reverse("ideas:graph_lab_export"),
            HTTP_ORIGIN=GRAPH_LAB["IDEAFLOW_GRAPH_LAB_ORIGIN"],
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Methods"], "GET, OPTIONS")
        self.assertEqual(response["Access-Control-Allow-Headers"], "Authorization")

    def test_export_is_valid_bounded_graphml(self):
        source = make_idea(title='Source <& "safe"')
        target = make_idea(title="Target")
        IdeaRelation.objects.create(
            source=source,
            target=target,
            relation_type=RelationType.SUPPORTS,
            description="Evidence & context",
        )
        response = self.export(self.issue()["capability"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Graph-Nodes"], "2")
        self.assertEqual(response["X-Graph-Edges"], "1")
        root = ET.fromstring(response.content)
        self.assertTrue(root.tag.endswith("graphml"))
        self.assertIn(b"Source &lt;&amp;", response.content)

    def test_expired_revoked_and_role_removed_capabilities_fail(self):
        token = self.issue()["capability"]
        capability = GraphAccessCapability.objects.get()
        capability.expires_at = timezone.now() - timedelta(seconds=1)
        capability.save(update_fields=["expires_at"])
        self.assertEqual(self.export(token).status_code, 401)

        token = self.issue()["capability"]
        profile = self.user.profile
        profile.role_graph = False
        profile.save(update_fields=["role_graph"])
        self.assertEqual(self.export(token).status_code, 403)

    @override_settings(IDEAFLOW_GRAPH_CAPABILITY_MAX_REQUESTS=1)
    def test_capability_request_count_is_bounded(self):
        make_idea()
        token = self.issue()["capability"]
        self.assertEqual(self.export(token).status_code, 200)
        self.assertEqual(self.export(token).status_code, 429)

    @override_settings(IDEAFLOW_GRAPH_EXPORT_MAX_NODES=1)
    def test_export_rejects_oversized_graph(self):
        make_idea()
        make_idea()
        self.assertEqual(self.export(self.issue()["capability"]).status_code, 413)


class DisabledGraphLabTests(TestCase):
    def test_disabled_launcher_redirects_and_export_is_unavailable(self):
        user = make_user(roles=["role_graph"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        self.assertRedirects(
            self.client.get(reverse("ideas:graph_lab")),
            reverse("ideas:graph"),
            fetch_redirect_response=False,
        )
