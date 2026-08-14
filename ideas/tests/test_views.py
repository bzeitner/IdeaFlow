from datetime import timedelta

from django.test import TestCase
from django.template.defaultfilters import date
from django.urls import reverse
from django.utils import timezone

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

    def test_home_lists_public_projects_to_any_signed_in_user(self):
        make_idea(title="Shared One", is_public=True)
        make_idea(title="Secret One", is_public=False)
        user = make_user(roles=[])  # even a roleless user
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ideas/home.html")
        self.assertContains(response, "Shared One")
        self.assertNotContains(response, "Secret One")

    def test_home_public_cards_have_no_edit_action(self):
        make_idea(title="Shared One", is_public=True)
        user = make_user(roles=[])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get("/")
        self.assertNotContains(response, "Edit")


class TabAccessTests(TestCase):
    TABS = [
        ("ideas:current", "role_current"),
        ("ideas:tracking", "role_tracking"),
        ("ideas:archive", "role_archive"),
        ("ideas:graph", "role_graph"),
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
        self.assertContains(response, "No effort summary has been recorded yet.")

    def test_header_shows_last_update_date_and_time(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))

        expected = date(timezone.localtime(idea.updated_at), "M j, Y g:i A T")
        self.assertContains(response, f"updated {expected}")

    def test_latest_effort_summary_is_presented_for_humans(self):
        idea = make_idea(status=Status.CURRENT)
        idea.exec_summary = (
            "Outcome: The validation completed.\n"
            "Recommended next steps:\n- Run the pilot."
        )
        idea.save()
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))

        self.assertContains(response, "Latest effort summary")
        self.assertContains(response, "Recommended next steps:")

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

    def test_json_status_update_supports_in_place_tracking_archive(self):
        idea = make_idea(status=Status.TRACKING)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:set_status", args=[idea.pk, "archived"]),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"ok": True, "idea_id": idea.pk, "status": "archived"}
        )


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
        make_feed_item(title="Rated one", interest=3, summarized_at=timezone.now())
        make_feed_item(title="Unrated one", summarized_at=timezone.now())
        response = self.client.get(reverse("ideas:feeds"), {"unrated": "1"})
        self.assertNotContains(response, "Rated one")
        self.assertContains(response, "Unrated one")

    def test_unrated_filter_hides_unsummarized_items(self):
        """Nothing to rate on an item that hasn't been summarized yet."""
        self._login()
        make_feed_item(title="Summarized one", summarized_at=timezone.now())
        make_feed_item(title="Bare title only")
        response = self.client.get(reverse("ideas:feeds"), {"unrated": "1"})
        self.assertContains(response, "Summarized one")
        self.assertNotContains(response, "Bare title only")


class FeedLinkXssTests(TestCase):
    def test_javascript_link_is_not_rendered_as_href(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        make_feed_item(title="Sneaky", link="javascript:alert(document.cookie)")
        response = self.client.get(reverse("ideas:feeds"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sneaky")          # title still shown
        self.assertNotContains(response, "javascript:")   # but not as a link


class NextActionTests(TestCase):
    def test_block_shows_before_research_exists(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertContains(r, "next-action-form")
        self.assertContains(r, "No next actions queued yet.")
        self.assertContains(r, "Queue action")

    def test_set_next_action(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.post(
            reverse("ideas:set_next_action", args=[idea.pk]),
            {"next_action": "Email three prospects"},
        )
        self.assertRedirects(r, reverse("ideas:detail", args=[idea.pk]))
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "Email three prospects")
        self.assertEqual(idea.next_actions, ["Email three prospects"])

    def test_actions_can_be_queued_reordered_and_completed(self):
        idea = make_idea(status=Status.CURRENT, next_action="First")
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        url = reverse("ideas:queue_next_action", args=[idea.pk])

        self.client.post(url, {"operation": "add", "next_action": "Second"})
        self.client.post(url, {"operation": "add", "next_action": "Third"})
        idea.refresh_from_db()
        self.assertEqual(idea.next_actions, ["First", "Second", "Third"])
        self.assertEqual(idea.next_action, "First")

        self.client.post(url, {"operation": "up", "index": 2})
        idea.refresh_from_db()
        self.assertEqual(idea.next_actions, ["First", "Third", "Second"])

        self.client.post(url, {"operation": "complete", "index": 0})
        idea.refresh_from_db()
        self.assertEqual(idea.next_actions, ["Third", "Second"])
        self.assertEqual(idea.next_action, "Third")

    def test_pr_urls_in_queued_actions_are_clickable(self):
        pr_url = "https://github.com/example/project/pull/42"
        idea = make_idea(
            status=Status.CURRENT,
            next_action=f"Review PR: {pr_url}",
            next_actions=[f"Review PR: {pr_url}"],
        )
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))

        self.assertContains(response, f'href="{pr_url}"')
        self.assertContains(response, ">https://github.com/example/project/pull/42</a>")

    def test_queue_mutation_requires_status_role(self):
        idea = make_idea(status=Status.CURRENT, next_action="First")
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        self.client.post(
            reverse("ideas:queue_next_action", args=[idea.pk]),
            {"operation": "add", "next_action": "Not allowed"},
        )

        idea.refresh_from_db()
        self.assertEqual(idea.next_action_queue, ["First"])

    def test_set_next_action_denied_without_status_role(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_tracking"])  # wrong tab
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.post(
            reverse("ideas:set_next_action", args=[idea.pk]), {"next_action": "x"}
        )
        self.assertRedirects(r, reverse("ideas:home"), fetch_redirect_response=False)
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "")


class TrackingWorkflowTests(TestCase):
    def setUp(self):
        self.user = make_user(roles=["role_tracking"])
        self.client.force_login(self.user, backend=MODEL_BACKEND)

    def test_search_and_attention_filters(self):
        make_idea(title="Matching roadmap", status=Status.TRACKING, next_action="")
        make_idea(title="Other idea", status=Status.TRACKING, next_action="Ship it")
        response = self.client.get(
            reverse("ideas:tracking"), {"q": "roadmap", "attention": "no-next-action"}
        )
        self.assertContains(response, "Matching roadmap")
        self.assertNotContains(response, "Other idea")

    def test_filters_are_marked_for_immediate_application(self):
        response = self.client.get(reverse("ideas:tracking"))

        self.assertContains(response, "data-auto-submit-filters")
        self.assertContains(response, "ideas/tracking.js")

    def test_default_sort_groups_parents_with_their_children(self):
        later_parent = make_idea(
            title="Later parent", status=Status.TRACKING, rank=20
        )
        make_idea(
            title="Later child", status=Status.TRACKING, rank=1,
            parent=later_parent,
        )
        earlier_parent = make_idea(
            title="Earlier parent", status=Status.TRACKING, rank=10
        )
        make_idea(
            title="Earlier child", status=Status.TRACKING, rank=99,
            parent=earlier_parent,
        )

        response = self.client.get(reverse("ideas:tracking"))

        self.assertEqual(response.context["filters"]["sort"], "family")
        self.assertEqual(
            [idea.title for idea in response.context["ideas"]],
            ["Earlier parent", "Earlier child", "Later parent", "Later child"],
        )
        self.assertContains(response, "Parent &amp; children", html=False)
        self.assertContains(response, "child-item")

    def test_family_parent_shows_tracking_child_count_and_collapse_hook(self):
        parent = make_idea(title="Parent", status=Status.TRACKING)
        make_idea(title="Visible child", status=Status.TRACKING, parent=parent)
        make_idea(title="Archived child", status=Status.ARCHIVED, parent=parent)

        response = self.client.get(reverse("ideas:tracking"))

        rendered_parent = next(
            idea for idea in response.context["ideas"] if idea.pk == parent.pk
        )
        self.assertEqual(rendered_parent.tracking_child_count, 1)
        self.assertContains(response, 'data-family-toggle="%s"' % parent.pk)
        self.assertContains(response, "1 child")
        self.assertContains(response, 'data-parent-id="%s"' % parent.pk)
        self.assertContains(response, "ideas/tracking.js")
        self.assertContains(response, "data-tracking-status-form")

    def test_child_toggle_is_only_shown_for_family_sort(self):
        parent = make_idea(status=Status.TRACKING)
        make_idea(status=Status.TRACKING, parent=parent)

        response = self.client.get(reverse("ideas:tracking"), {"sort": "rank"})

        self.assertNotContains(response, "data-family-toggle")

    def test_unknown_sort_falls_back_to_parent_child_grouping(self):
        response = self.client.get(reverse("ideas:tracking"), {"sort": "unknown"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filters"]["sort"], "family")

    def test_last_update_sort_lists_newest_first(self):
        older = make_idea(title="Older", status=Status.TRACKING)
        newer = make_idea(title="Newer", status=Status.TRACKING)
        Idea.objects.filter(pk=older.pk).update(
            updated_at=timezone.now() - timedelta(days=1)
        )

        response = self.client.get(reverse("ideas:tracking"), {"sort": "updated"})

        self.assertEqual(
            [idea.title for idea in response.context["ideas"]], ["Newer", "Older"]
        )
        self.assertContains(response, "Last update (newest first)")
        self.assertContains(response, '<option value="updated" selected>', html=False)

    def test_quick_update_saves_next_action_and_clears_pause(self):
        idea = make_idea(
            status=Status.TRACKING, next_action="", agent_runs_since_feedback=2
        )
        response = self.client.post(
            reverse("ideas:quick_update", args=[idea.pk]),
            {"field": "next_action", "value": "Call the first customer"},
        )
        self.assertRedirects(response, reverse("ideas:tracking") + "?")
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "Call the first customer")
        self.assertEqual(idea.agent_runs_since_feedback, 0)


class AddResearchViewTests(TestCase):
    def test_matching_status_role_can_log_research(self):
        from .helpers import make_ai_model

        idea = make_idea(status=Status.CURRENT)
        model = make_ai_model()
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.post(
            reverse("ideas:add_research", args=[idea.pk]),
            {
                "topic": "Market scan",
                "focus": "",
                "context": "Three useful competitors found.",
                "occurred_at": "",
                "model": model.pk,
                "effort": 3,
                "quality": 4,
                "tokens_used": "",
            },
        )
        self.assertRedirects(response, reverse("ideas:detail", args=[idea.pk]))
        self.assertEqual(idea.research_entries.get().topic, "Market scan")

    def test_wrong_status_role_is_denied(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:add_research", args=[idea.pk]))
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)


class PublicDetailAccessTests(TestCase):
    def test_roleless_user_can_view_public_idea(self):
        idea = make_idea(title="Open Book", status=Status.CURRENT, is_public=True)
        user = make_user(roles=[])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Open Book")

    def test_roleless_user_cannot_view_private_idea(self):
        idea = make_idea(status=Status.CURRENT, is_public=False)
        user = make_user(roles=[])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertRedirects(r, reverse("ideas:home"), fetch_redirect_response=False)

    def test_public_viewer_sees_no_edit_button(self):
        idea = make_idea(status=Status.CURRENT, is_public=True)
        user = make_user(roles=[])  # can view, can't manage
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertNotContains(r, "Edit")

    def test_public_viewer_cannot_edit(self):
        idea = make_idea(status=Status.CURRENT, is_public=True)
        user = make_user(roles=[])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:edit", args=[idea.pk]))
        self.assertRedirects(r, reverse("ideas:home"), fetch_redirect_response=False)


class CardNextActionTests(TestCase):
    def test_next_action_shows_on_the_current_tab_card(self):
        make_idea(title="Has Next", status=Status.CURRENT, next_action="Call the vendor")
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:current"))
        self.assertContains(response, "Call the vendor")
        self.assertContains(response, "next-line")


class PauseControlTests(TestCase):
    def _login_current(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

    def test_paused_banner_and_continue_work(self):
        idea = make_idea(status=Status.CURRENT)
        idea.agent_runs_since_feedback = 2
        idea.save()
        self._login_current()
        r = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertContains(r, "Paused")
        r = self.client.post(reverse("ideas:continue_work", args=[idea.pk]))
        self.assertRedirects(r, reverse("ideas:detail", args=[idea.pk]))
        idea.refresh_from_db()
        self.assertEqual(idea.agent_runs_since_feedback, 0)

    def test_setting_next_action_clears_pause(self):
        idea = make_idea(status=Status.CURRENT)
        idea.agent_runs_since_feedback = 5
        idea.save()
        self._login_current()
        self.client.post(
            reverse("ideas:set_next_action", args=[idea.pk]), {"next_action": "go"}
        )
        idea.refresh_from_db()
        self.assertEqual(idea.agent_runs_since_feedback, 0)


class PwaTests(TestCase):
    def test_manifest_served_with_share_target(self):
        r = self.client.get("/manifest.json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["content-type"], "application/manifest+json")
        body = r.content.decode()
        self.assertIn("share_target", body)
        self.assertIn("/new/", body)

    def test_service_worker_served_as_javascript(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["content-type"], "application/javascript")

    def test_share_target_prefills_new_idea(self):
        user = make_user(roles=["role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(
            "/new/", {"title": "Shared idea", "text": "the gist", "url": "https://ex.com/a"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Shared idea")
        self.assertContains(r, "the gist")
        self.assertContains(r, "https://ex.com/a")


class ArticleLinkXssTests(TestCase):
    def test_javascript_article_link_not_rendered_on_detail(self):
        from ideas.feeds import link_feed, record_feed_item_summary
        from ideas.models import FeedItem
        from .helpers import make_feed

        idea = make_idea(status=Status.CURRENT)
        feed = make_feed()
        link_feed(idea, feed, rating=5)
        item = FeedItem.objects.create(
            feed=feed, guid="x", title="Sneaky", link="javascript:alert(1)"
        )
        record_feed_item_summary(item, summary="s", idea=idea, usefulness=3)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertContains(r, "Sneaky")          # title shown
        self.assertNotContains(r, "javascript:")   # but not as a link


class ParentChildTests(TestCase):
    def test_detail_shows_parent_and_children(self):
        parent = make_idea(title="Passive Income", status=Status.CURRENT)
        child = make_idea(title="A SaaS", status=Status.CURRENT, parent=parent)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        # parent page lists the child
        r = self.client.get(reverse("ideas:detail", args=[parent.pk]))
        self.assertContains(r, "A SaaS")
        # child page links back to the parent
        r = self.client.get(reverse("ideas:detail", args=[child.pk]))
        self.assertContains(r, "Passive Income")

    def test_new_idea_form_prefills_parent(self):
        parent = make_idea(title="Passive Income")
        user = make_user(roles=["role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:create"), {"parent": parent.pk})
        self.assertEqual(r.status_code, 200)
        # the parent option is selected in the rendered form
        self.assertContains(r, f'value="{parent.pk}" selected')

    def test_create_suggested_child_creates_and_removes_suggestion(self):
        parent = make_idea(
            title="Passive Income",
            suggested_children="A SaaS\nA rental property",
        )
        user = make_user(roles=["role_add_ideas", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:create_suggested_child", args=[parent.pk]),
            {"title": "A SaaS"},
        )

        child = parent.children.get()
        self.assertRedirects(response, reverse("ideas:detail", args=[child.pk]))
        self.assertEqual(child.title, "A SaaS")
        self.assertEqual(child.category, parent.category)
        parent.refresh_from_db()
        self.assertEqual(parent.suggested_children, "A rental property")

    def test_cannot_create_text_that_is_not_a_suggestion(self):
        parent = make_idea(suggested_children="Expected child")
        user = make_user(roles=["role_add_ideas", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:create_suggested_child", args=[parent.pk]),
            {"title": "Injected child"},
        )

        self.assertRedirects(response, reverse("ideas:detail", args=[parent.pk]))
        self.assertFalse(parent.children.exists())

    def test_form_excludes_self_as_parent(self):
        from ideas.forms import IdeaForm

        idea = make_idea(title="Root")
        child = make_idea(title="Child", parent=idea)
        form = IdeaForm(instance=idea)
        options = list(form.fields["parent"].queryset.values_list("pk", flat=True))
        self.assertNotIn(idea.pk, options)   # can't parent itself
        self.assertNotIn(child.pk, options)  # nor a descendant (cycle)


class TrackingPrIconTests(TestCase):
    def test_pr_link_shows_when_a_pr_resource_exists(self):
        idea = make_idea(title="Repo Idea", status=Status.TRACKING)
        idea.resources.create(label="PR", url="https://github.com/x/y/pull/3")
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:tracking"))
        self.assertContains(r, "pr-icon")
        self.assertContains(r, "https://github.com/x/y/pull/3")

    def test_no_pr_link_without_a_pr(self):
        make_idea(title="Plain", status=Status.TRACKING)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:tracking"))
        self.assertNotContains(r, "pr-icon")


class IdeaNumberTests(TestCase):
    def test_id_shown_on_current_cards(self):
        idea = make_idea(title="Numbered", status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:current"))
        self.assertContains(r, f"#{idea.id}")

    def test_id_column_on_tracking(self):
        idea = make_idea(title="Tracked", status=Status.TRACKING)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        r = self.client.get(reverse("ideas:tracking"))
        self.assertContains(r, "idea-id")
        self.assertContains(r, str(idea.id))
