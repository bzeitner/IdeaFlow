from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from ideas.models import PromptRevision, PromptRevisionStatus, PromptTemplate
from ideas.prompts import approved_prompt

from .helpers import MODEL_BACKEND, make_user


TOKEN = "prompt-test-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


class PromptRevisionTests(TestCase):
    def test_seeded_registry_covers_every_agent_prompt_path(self):
        expected = {
            "agent-repeat", "agent-execute", "agent-critique", "agent-review",
            "agent-research", "agent-portfolio-reflection", "agent-feed-scoring",
            "shared-standards", "pr-resource-standard", "human-summary-standard",
            "effort-quality-standard", "child-suggestion-standard",
            "next-action-standard", "semantic-relationship-classifier",
            "open-question-single", "open-question-batch",
            "agent-weekly-summary",
        }
        self.assertTrue(expected.issubset(set(PromptTemplate.objects.values_list("key", flat=True))))
        for key in expected:
            with self.subTest(key=key):
                self.assertIsNotNone(PromptTemplate.objects.get(key=key).approved_revision)

    def setUp(self):
        self.admin = make_user(roles=["role_admin"])
        self.template = PromptTemplate.objects.create(
            key="test-runtime-prompt", name="Runtime prompt", variables=["name"]
        )
        self.approved = PromptRevision.objects.create(
            template=self.template,
            content="Hello {name}",
            status=PromptRevisionStatus.APPROVED,
            created_by=self.admin,
        )

    def test_proposal_is_inert_until_approved_then_supersedes_prior_revision(self):
        proposal = PromptRevision.objects.create(
            template=self.template,
            content="Welcome {name}",
            change_summary="Friendlier wording",
            created_by=self.admin,
        )
        self.assertEqual(proposal.version, 2)
        self.assertEqual(approved_prompt(self.template.key), "Hello {name}")

        proposal.approve(self.admin)

        self.approved.refresh_from_db()
        self.assertEqual(self.approved.status, PromptRevisionStatus.SUPERSEDED)
        self.assertEqual(approved_prompt(self.template.key), "Welcome {name}")

    def test_revision_body_is_immutable(self):
        self.approved.content = "Silently altered"
        with self.assertRaises(ValidationError):
            self.approved.save()


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class PromptApiTests(TestCase):
    def test_api_returns_only_approved_content_and_requires_token(self):
        template = PromptTemplate.objects.create(key="api-prompt", name="API prompt")
        PromptRevision.objects.create(
            template=template,
            content="approved",
            status=PromptRevisionStatus.APPROVED,
        )
        PromptRevision.objects.create(template=template, content="proposed")
        url = f"/api/prompts/{template.key}/"

        self.assertEqual(self.client.get(url).status_code, 401)
        response = self.client.get(url, **AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "approved")
        self.assertEqual(response.json()["version"], 1)


class PromptAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user(email="prompt-admin@example.com", roles=["role_admin"])
        self.client.force_login(self.admin, backend=MODEL_BACKEND)
        self.template = PromptTemplate.objects.create(key="review-prompt", name="Review prompt")
        self.approved = PromptRevision.objects.create(
            template=self.template,
            content="Keep this line\nRemove this line",
            status=PromptRevisionStatus.APPROVED,
        )
        self.proposal = PromptRevision.objects.create(
            template=self.template,
            content="Keep this line\nAdd this line",
            change_summary="Replace the second instruction.",
            created_by=self.admin,
        )

    def test_review_page_shows_side_by_side_highlighted_diff_and_can_approve(self):
        url = reverse("admin:ideas_promptrevision_review", args=[self.proposal.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approved v1")
        self.assertContains(response, "Proposed v2")
        self.assertContains(response, "diff_sub")
        self.assertContains(response, "diff_add")

        response = self.client.post(url, {"decision": "approve"})
        self.assertRedirects(response, reverse("admin:ideas_promptrevision_changelist"))
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, PromptRevisionStatus.APPROVED)

    def test_admin_loads_global_tooltips(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "ideas/admin_tooltips")
        response = self.client.get(reverse("admin:ideas_prompttemplate_changelist"))
        self.assertContains(response, "Propose change")

    def test_diff_escapes_prompt_html(self):
        malicious = PromptRevision.objects.create(
            template=self.template,
            content="<script>alert('prompt')</script>",
            created_by=self.admin,
        )
        response = self.client.get(
            reverse("admin:ideas_promptrevision_review", args=[malicious.pk])
        )
        self.assertNotContains(response, "<script>alert('prompt')</script>", html=False)
        self.assertContains(response, "&lt;script&gt;", html=False)

    def test_non_admin_cannot_open_prompt_review(self):
        user = make_user(email="not-admin@example.com", roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(
            reverse("admin:ideas_promptrevision_review", args=[self.proposal.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)
