from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ideas.category_goals import DEFAULT_GOALS
from ideas.models import Category

from .helpers import MODEL_BACKEND, make_category, make_idea, make_user


class CategoryGoalPageTests(TestCase):
    def setUp(self):
        self.url = reverse("ideas:category_goals")
        self.category = make_category(name="Alpha", slug="alpha", goal_text="Old goal")
        self.admin = make_user(email="admin@example.com", roles=["role_admin"])

    def post_as(self, user, data, client=None):
        client = client or self.client
        client.force_login(user, backend=MODEL_BACKEND)
        return client.post(self.url, data)

    def key(self, category=None):
        return f"cat{(category or self.category).pk}-goal_text"

    def test_anonymous_redirected_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertIn("next=", self.client.post(self.url, {self.key(): "x"})["Location"])
        self.category.refresh_from_db()
        self.assertEqual(self.category.goal_text, "Old goal")

    def test_non_admin_with_tab_roles_denied(self):
        user = make_user(email="u@example.com", roles=["role_current", "role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        self.assertRedirects(self.client.get(self.url), reverse("ideas:home"), fetch_redirect_response=False)
        self.post_as(user, {self.key(): "hacked"})
        self.category.refresh_from_db()
        self.assertEqual(self.category.goal_text, "Old goal")

    def test_admin_can_view_and_update(self):
        self.client.force_login(self.admin, backend=MODEL_BACKEND)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.post_as(self.admin, {self.key(): "  New goal  "})
        self.category.refresh_from_db()
        self.assertEqual(self.category.goal_text, "New goal")

    def test_blank_clears_goal(self):
        self.post_as(self.admin, {self.key(): ""})
        self.category.refresh_from_db()
        self.assertEqual(self.category.goal_text, "")

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin, backend=MODEL_BACKEND)
        self.assertEqual(client.post(self.url, {self.key(): "x"}).status_code, 403)
        self.category.refresh_from_db()
        self.assertEqual(self.category.goal_text, "Old goal")

    def test_other_fields_not_writable(self):
        self.post_as(self.admin, {self.key(): "ok", "name": "Hacked", "slug": "hacked", "color": "#000000"})
        self.category.refresh_from_db()
        self.assertEqual((self.category.name, self.category.slug, self.category.color), ("Alpha", "alpha", "#44506a"))

    def test_overlong_or_control_chars_rejected_and_nothing_saved(self):
        other = make_category(name="Beta", slug="beta", goal_text="Beta old")
        self.post_as(self.admin, {self.key(): "fine", self.key(other): "x" * 2001})
        self.category.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.category.goal_text, other.goal_text), ("Old goal", "Beta old"))
        self.post_as(self.admin, {self.key(): "bad\x00text"})
        self.category.refresh_from_db()
        self.assertEqual(self.category.goal_text, "Old goal")

    def test_reset_restores_default_from_server(self):
        research = make_category(name="Research", slug="research", goal_text="custom")
        self.post_as(self.admin, {"reset": str(research.pk), self.key(research): "client text"})
        research.refresh_from_db()
        self.assertEqual(research.goal_text, DEFAULT_GOALS["research"][1])

    def test_no_audit_entries_when_batch_rolls_back(self):
        other = make_category(name="Beta", slug="beta", goal_text="Beta old")
        self.client.force_login(self.admin, backend=MODEL_BACKEND)
        with self.assertNoLogs("ideaflow.audit", level="INFO"):
            self.client.post(self.url, {self.key(): "valid edit", self.key(other): "x" * 2001})

    def test_audit_entries_emitted_after_commit(self):
        self.client.force_login(self.admin, backend=MODEL_BACKEND)
        with self.assertLogs("ideaflow.audit", level="INFO") as logs:
            self.client.post(self.url, {self.key(): "valid edit"})
        self.assertEqual(len(logs.records), 1)
        self.assertIn("updated", logs.output[0])

    def test_crlf_newlines_do_not_count_double(self):
        # 1000 lines of "a" = 1999 chars with \n, but 2999 as submitted with \r\n.
        text = "\r\n".join("a" * 1000)
        self.post_as(self.admin, {self.key(): text})
        self.category.refresh_from_db()
        self.assertEqual(self.category.goal_text, "\n".join("a" * 1000))

    def test_reset_keeps_edits_to_other_rows(self):
        research = make_category(name="Research", slug="research", goal_text="custom")
        self.post_as(self.admin, {"reset": str(research.pk), self.key(research): "ignored", self.key(): "kept edit"})
        research.refresh_from_db()
        self.category.refresh_from_db()
        self.assertEqual(research.goal_text, DEFAULT_GOALS["research"][1])
        self.assertEqual(self.category.goal_text, "kept edit")

    def test_reset_unknown_category_404(self):
        self.assertEqual(self.post_as(self.admin, {"reset": "999999"}).status_code, 404)

    def test_html_is_escaped(self):
        self.post_as(self.admin, {self.key(): "<script>alert(1)</script>"})
        self.client.force_login(self.admin, backend=MODEL_BACKEND)
        self.assertNotContains(self.client.get(self.url), "<script>alert(1)</script>")
        idea = make_idea(category=self.category)
        detail = self.client.get(idea.get_absolute_url())
        self.assertNotContains(detail, "<script>alert(1)</script>")


class CategoryGoalIdeaFormTests(TestCase):
    def test_form_exposes_goals_for_active_categories_only(self):
        make_category(name="Active", slug="active", goal_text="Active goal")
        make_category(name="Gone", slug="gone", goal_text="Inactive goal", is_active=False)
        user = make_user(roles=["role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:create"))
        self.assertContains(response, "Active goal")
        self.assertNotContains(response, "Inactive goal")

    def test_json_script_escapes_markup(self):
        make_category(name="X", slug="x", goal_text="</script><b>hi</b>")
        user = make_user(roles=["role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        self.assertNotContains(self.client.get(reverse("ideas:create")), "</script><b>hi")


class CategoryGoalSeedTests(TestCase):
    def test_default_lookup_by_slug_and_name(self):
        from ideas.category_goals import default_goal_for

        self.assertEqual(default_goal_for(Category(name="Whatever", slug="book")), DEFAULT_GOALS["book"][1])
        self.assertEqual(default_goal_for(Category(name="podcast", slug="p")), DEFAULT_GOALS["podcast"][1])
        self.assertEqual(default_goal_for(Category(name="Nope", slug="nope")), "")


@override_settings(IDEAFLOW_API_TOKEN="tok")
class CategoryGoalApiTests(TestCase):
    def test_detail_includes_goal(self):
        category = make_category(name="G", slug="g", goal_text="Be brief")
        idea = make_idea(category=category)
        data = self.client.get(f"/api/ideas/{idea.pk}/", HTTP_AUTHORIZATION="Bearer tok").json()
        self.assertEqual(data["category"]["goal"], "Be brief")
