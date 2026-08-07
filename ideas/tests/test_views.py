from django.test import TestCase
from django.urls import reverse

from ideas.models import Idea, Profile, Status

from .helpers import (
    MODEL_BACKEND,
    make_category,
    make_feed_item,
    make_idea,
    make_user,
)


class HomeViewTests(TestCase):
    def test_anonymous_sees_landing_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ideas/landing.html")
        self.assertContains(response, "Sign in with Google")

    def test_user_with_only_current_role_is_sent_to_current(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get("/")
        self.assertRedirects(response, reverse("ideas:current"))

    def test_user_with_only_tracking_role_is_sent_to_tracking(self):
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get("/")
        self.assertRedirects(response, reverse("ideas:tracking"))

    def test_user_with_only_archive_role_is_sent_to_archive(self):
        user = make_user(roles=["role_archive"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get("/")
        self.assertRedirects(response, reverse("ideas:archive"))

    def test_user_with_no_roles_sees_no_access_page(self):
        user = make_user(roles=[])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ideas/no_access.html")

    def test_admin_role_is_sent_to_current(self):
        user = make_user(roles=["role_admin"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get("/")
        self.assertRedirects(response, reverse("ideas:current"))


class TabAccessTests(TestCase):
    TABS = [
        ("ideas:current", "role_current"),
        ("ideas:tracking", "role_tracking"),
        ("ideas:archive", "role_archive"),
    ]

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("ideas:current"))
        self.assertRedirects(response, f"/?next={reverse('ideas:current')}")

    def test_matching_role_grants_access(self):
        for url_name, role in self.TABS:
            with self.subTest(url_name=url_name):
                user = make_user(email=f"{role}@example.com", roles=[role])
                self.client.force_login(user, backend=MODEL_BACKEND)
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)

    def test_mismatched_role_is_denied(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:tracking"))
        # fetch_redirect_response=False: home() itself redirects again for a
        # user who holds role_current, so this is a 302 chain, not a 200 leaf.
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)

    def test_admin_can_access_every_tab(self):
        user = make_user(roles=["role_admin"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        for url_name, _role in self.TABS:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)

    def test_tabs_nav_only_lists_accessible_tabs(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:current"))
        self.assertContains(response, "Current")
        self.assertNotContains(response, "Tracking")
        self.assertNotContains(response, "Archive")


class DetailViewTests(TestCase):
    def test_matching_status_role_can_view(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, idea.title)

    def test_mismatched_status_role_is_denied(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)


class IdeaCreateViewTests(TestCase):
    def _post_data(self, category, **overrides):
        data = {
            "title": "A brand new idea",
            "category": category.id,
            "summary": "",
            "interest_level": 3,
            "status": "current",
            "stage": "",
            "rank": 0,
            "notes": "",
            "resources-TOTAL_FORMS": "3",
            "resources-INITIAL_FORMS": "0",
            "resources-MIN_NUM_FORMS": "0",
            "resources-MAX_NUM_FORMS": "1000",
            "resources-0-label": "",
            "resources-0-url": "",
            "resources-0-id": "",
            "resources-1-label": "",
            "resources-1-url": "",
            "resources-1-id": "",
            "resources-2-label": "",
            "resources-2-url": "",
            "resources-2-id": "",
            "research_entries-TOTAL_FORMS": "1",
            "research_entries-INITIAL_FORMS": "0",
            "research_entries-MIN_NUM_FORMS": "0",
            "research_entries-MAX_NUM_FORMS": "1000",
            "research_entries-0-topic": "",
            "research_entries-0-focus": "",
            "research_entries-0-context": "",
            "research_entries-0-occurred_at": "",
            "research_entries-0-model": "",
            "research_entries-0-effort": "3",
            "research_entries-0-quality": "3",
            "research_entries-0-tokens_used": "",
            "research_entries-0-id": "",
        }
        data.update(overrides)
        return data

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("ideas:create"))
        self.assertRedirects(response, f"/?next={reverse('ideas:create')}")

    def test_without_add_ideas_role_is_denied(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:create"))
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)

    def test_with_add_ideas_role_can_view_form(self):
        user = make_user(roles=["role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Capture an Idea")

    def test_valid_post_creates_idea(self):
        # Also grant role_current so the post-save redirect to the idea's own
        # detail page (status="current") lands on a 200, not another redirect.
        user = make_user(roles=["role_add_ideas", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        category = make_category()
        response = self.client.post(reverse("ideas:create"), self._post_data(category))
        idea = Idea.objects.get(title="A brand new idea")
        self.assertRedirects(response, idea.get_absolute_url())

    def test_role_admin_can_also_create(self):
        user = make_user(roles=["role_admin"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        category = make_category()
        response = self.client.post(reverse("ideas:create"), self._post_data(category))
        self.assertTrue(Idea.objects.filter(title="A brand new idea").exists())

    def test_tampered_status_cannot_land_a_new_idea_outside_current(self):
        """A role_add_ideas-only user can't write straight into a tab (e.g.
        archived) they hold no role to view or manage, by submitting a
        non-default `status` on the create form."""
        user = make_user(roles=["role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        category = make_category()
        self.client.post(
            reverse("ideas:create"), self._post_data(category, status="archived")
        )
        idea = Idea.objects.get(title="A brand new idea")
        self.assertEqual(idea.status, Status.CURRENT)


class IdeaEditViewTests(TestCase):
    def test_matching_status_role_can_edit(self):
        idea = make_idea(status=Status.TRACKING)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:edit", args=[idea.pk]))
        self.assertEqual(response.status_code, 200)

    def test_mismatched_status_role_cannot_edit(self):
        idea = make_idea(status=Status.TRACKING)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:edit", args=[idea.pk]))
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)

    def test_editing_does_not_require_add_ideas_role(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])  # no role_add_ideas
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:edit", args=[idea.pk]))
        self.assertEqual(response.status_code, 200)


class SetStatusViewTests(TestCase):
    def test_get_is_not_allowed(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(
            reverse("ideas:set_status", args=[idea.pk, "tracking"])
        )
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)
        idea.refresh_from_db()
        self.assertEqual(idea.status, Status.CURRENT)

    def test_source_tab_role_is_sufficient_to_move_out(self):
        """Moving current -> tracking only needs role_current, not role_tracking."""
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.post(
            reverse("ideas:set_status", args=[idea.pk, "tracking"])
        )
        idea.refresh_from_db()
        self.assertEqual(idea.status, Status.TRACKING)
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)

    def test_missing_source_role_is_denied(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.post(
            reverse("ideas:set_status", args=[idea.pk, "tracking"])
        )
        idea.refresh_from_db()
        self.assertEqual(idea.status, Status.CURRENT)
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)

    def test_next_param_is_honored(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.post(
            reverse("ideas:set_status", args=[idea.pk, "archived"]),
            {"next": "/current/"},
        )
        self.assertRedirects(response, "/current/")


class UserManagementViewTests(TestCase):
    def test_non_admin_is_denied(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:user_management"))
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)

    def test_admin_can_view_all_users(self):
        admin = make_user(email="admin@example.com", roles=["role_admin"])
        other = make_user(email="other@example.com", roles=[])
        self.client.force_login(admin, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:user_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, other.email)
        self.assertContains(response, admin.email)

    def test_admin_post_updates_roles_for_multiple_users(self):
        admin = make_user(email="admin@example.com", roles=["role_admin"])
        other = make_user(email="other@example.com", roles=["role_current"])
        self.client.force_login(admin, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:user_management"),
            {
                f"role-{admin.id}-role_admin": "on",
                f"role-{admin.id}-role_current": "on",
                f"role-{admin.id}-role_tracking": "on",
                f"role-{admin.id}-role_archive": "on",
                f"role-{admin.id}-role_add_ideas": "on",
                # other's role_current is intentionally omitted -> should clear
                f"role-{other.id}-role_tracking": "on",
            },
        )
        self.assertRedirects(response, reverse("ideas:user_management"))

        other_profile = Profile.objects.get(user=other)
        self.assertFalse(other_profile.role_current)
        self.assertTrue(other_profile.role_tracking)
        self.assertFalse(other_profile.role_admin)


class GoogleOnlySignInTests(TestCase):
    def test_local_signup_url_is_gone(self):
        response = self.client.get("/accounts/signup/")
        self.assertEqual(response.status_code, 404)

    def test_login_page_uses_app_styling(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ideas/base.html")
        self.assertContains(response, "Sign in with Google")

    def test_signin_button_is_a_post_form_not_a_get_link(self):
        response = self.client.get("/")
        self.assertContains(
            response, '<form method="post" action="/accounts/google/login/"'
        )


class FeedPageTests(TestCase):
    def _login(self, roles=("role_current",)):
        user = make_user(roles=list(roles))
        self.client.force_login(user, backend=MODEL_BACKEND)
        return user

    def test_no_role_user_is_denied(self):
        user = make_user(roles=[])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:feeds"))
        self.assertRedirects(response, reverse("ideas:home"))

    def test_manager_sees_feed_items(self):
        self._login()
        item = make_feed_item(title="Hello World", summary="A summary.")
        response = self.client.get(reverse("ideas:feeds"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello World")
        self.assertContains(response, "A summary.")

    def test_rate_sets_interest(self):
        self._login()
        item = make_feed_item()
        response = self.client.post(
            reverse("ideas:rate_feed_item", args=[item.pk]), {"interest": "4"}
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.interest, 4)

    def test_rate_sets_info_value(self):
        self._login()
        item = make_feed_item()
        self.client.post(
            reverse("ideas:rate_feed_item", args=[item.pk]), {"info_value": "2"}
        )
        item.refresh_from_db()
        self.assertEqual(item.info_value, 2)

    def test_out_of_range_rating_is_ignored(self):
        self._login()
        item = make_feed_item()
        self.client.post(
            reverse("ideas:rate_feed_item", args=[item.pk]), {"interest": "9"}
        )
        item.refresh_from_db()
        self.assertIsNone(item.interest)

    def test_unrated_filter_hides_rated_items(self):
        self._login()
        make_feed_item(title="Rated one", interest=3)
        make_feed_item(title="Unrated one")
        response = self.client.get(reverse("ideas:feeds"), {"unrated": "1"})
        self.assertNotContains(response, "Rated one")
        self.assertContains(response, "Unrated one")
