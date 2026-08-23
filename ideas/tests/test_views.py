from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.template.defaultfilters import date
from django.urls import reverse
from django.utils import timezone

from ideas.models import EpisodeRun, EpisodeRunStatus, EpisodeStatus, Idea, PersonaReview, Profile, Status

from .helpers import (
    MODEL_BACKEND,
    make_ai_model,
    make_category,
    make_episode,
    make_feed_item,
    make_idea,
    make_podcast_show,
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

    def test_roleless_new_user_sees_guide_callout(self):
        user = make_user(roles=[])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:home"))

        self.assertContains(response, "New to IdeaFlow?")
        self.assertContains(response, reverse("ideas:guide"))


class GuideViewTests(TestCase):
    def test_guide_is_available_before_sign_in(self):
        response = self.client.get(reverse("ideas:guide"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ideas/guide.html")
        self.assertContains(response, "Turn a promising thought into focused work")
        self.assertContains(response, "Sign in with Google")

    def test_header_links_to_guide(self):
        response = self.client.get(reverse("ideas:home"))

        self.assertContains(response, 'href="{}">Guide</a>'.format(reverse("ideas:guide")))

    def test_ideas_page_summarizes_full_history_with_family_totals(self):
        category = make_category(name="Product")
        model = make_ai_model()
        parent = make_idea(title="Parent project", status=Status.CURRENT, category=category)
        child = make_idea(
            title="Child project", status=Status.CURRENT, category=category, parent=parent
        )
        parent.research_entries.create(
            topic="Parent research",
            model=model,
            execution_provider="codex",
            execution_model="gpt-5-codex",
            tokens_used=1200,
        )
        child.research_entries.create(
            topic="Child implementation", model=model, tokens_used=800
        )
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:current"))

        self.assertContains(response, "Full-history metrics")
        self.assertContains(response, "2</strong> tasks")
        self.assertContains(response, "2000</strong> tokens total")
        self.assertContains(response, "Idea #{} — Parent project".format(parent.pk))
        self.assertContains(
            response,
            'href="{}">Idea #{} — Parent project</a>'.format(
                reverse("ideas:detail", args=[parent.pk]), parent.pk
            ),
        )
        self.assertContains(response, "Idea #{} — Child project".format(child.pk))
        self.assertContains(response, "Parent project + children (total)")
        self.assertContains(response, "gpt-5-codex")
        self.assertContains(response, "Product")


class TabAccessTests(TestCase):
    TABS = [
        ("ideas:current", "role_current"),
        ("ideas:tracking", "role_tracking"),
        ("ideas:archive", "role_archive"),
        ("ideas:graph", "role_graph"),
        ("ideas:weekly_summaries", "role_weekly_summary"),
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
    def test_app_displays_dates_in_pacific_time(self):
        self.assertEqual(settings.TIME_ZONE, "America/Los_Angeles")

    def test_matching_status_role_can_view(self):
        idea = make_idea(status=Status.CURRENT)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, idea.title)
        self.assertContains(response, "No effort summary has been recorded yet.")

    def test_research_index_and_cross_entry_links_use_stable_anchors(self):
        idea = make_idea(status=Status.CURRENT)
        model = make_ai_model()
        first = idea.research_entries.create(topic="Initial scan", model=model)
        later = idea.research_entries.create(
            topic="Follow-up",
            context=f"Entry #{first.pk} established the baseline.",
            model=model,
        )
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))

        self.assertContains(response, "Effort index")
        self.assertContains(response, f'id="research-entry-{first.pk}"')
        self.assertContains(response, f'href="#research-entry-{later.pk}"')
        self.assertContains(
            response,
            f'href="#research-entry-{first.pk}">Entry #{first.pk}</a>',
        )

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

    def test_form_groups_repeat_and_advanced_settings(self):
        user = make_user(roles=["role_add_ideas"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:create"))
        content = response.content.decode()

        self.assertContains(response, "Repeat task")
        self.assertContains(response, "Organization")
        self.assertContains(response, "Priority and planning")
        self.assertContains(response, "Agent context")
        self.assertContains(response, "Visibility")
        self.assertLess(content.index("Repeat task"), content.index("Links and resources"))
        self.assertLess(content.index("Links and resources"), content.index("More details and settings"))

    def test_valid_post_creates_idea(self):
        # Also grant role_current so the post-save redirect to the idea's own
        # detail page (status="current") lands on a 200, not another redirect.
        user = make_user(roles=["role_add_ideas", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        category = make_category()
        response = self.client.post(reverse("ideas:create"), self._post_data(category))
        idea = Idea.objects.get(title="A brand new idea")
        self.assertRedirects(response, idea.get_absolute_url())
        self.assertEqual(idea.created_by, user)

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

    def test_user_rows_show_last_login_and_owned_idea_count(self):
        admin = make_user(email="admin@example.com", roles=["role_admin"])
        other = make_user(email="owner@example.com")
        other.last_login = timezone.now() - timedelta(days=2)
        other.save(update_fields=["last_login"])
        make_idea(title="First owned idea", created_by=other)
        make_idea(title="Second owned idea", created_by=other)
        self.client.force_login(admin, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:user_management"))

        row = next(
            row for row in response.context["rows"] if row["profile"].user_id == other.pk
        )
        self.assertEqual(row["profile"].idea_count, 2)
        self.assertContains(
            response,
            date(timezone.localtime(other.last_login), "M j, Y g:i A T"),
        )

    def test_user_who_never_logged_in_is_labeled_never(self):
        admin = make_user(email="admin@example.com", roles=["role_admin"])
        make_user(email="never@example.com")
        self.client.force_login(admin, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:user_management"))

        self.assertContains(response, "Never")

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


class PersonaCouncilSettingsTests(TestCase):
    def test_manager_can_enable_reviews_and_change_timeframe_from_detail(self):
        idea = make_idea(
            status=Status.CURRENT,
            persona_review_enabled=False,
            persona_stall_days=14,
        )
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        detail = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertContains(detail, "Save council settings")
        response = self.client.post(
            reverse("ideas:update_persona_council", args=[idea.pk]),
            {"persona_review_enabled": "on", "persona_stall_days": "30"},
        )

        self.assertRedirects(response, reverse("ideas:detail", args=[idea.pk]))
        idea.refresh_from_db()
        self.assertTrue(idea.persona_review_enabled)
        self.assertEqual(idea.persona_stall_days, 30)

    def test_manager_can_disable_reviews_without_changing_timeframe(self):
        idea = make_idea(
            status=Status.CURRENT,
            persona_review_enabled=True,
            persona_stall_days=21,
        )
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        self.client.post(
            reverse("ideas:update_persona_council", args=[idea.pk]),
            {"persona_stall_days": "21"},
        )

        idea.refresh_from_db()
        self.assertFalse(idea.persona_review_enabled)
        self.assertEqual(idea.persona_stall_days, 21)

    def test_invalid_timeframe_is_not_saved(self):
        idea = make_idea(status=Status.CURRENT, persona_stall_days=14)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        self.client.post(
            reverse("ideas:update_persona_council", args=[idea.pk]),
            {"persona_review_enabled": "on", "persona_stall_days": "0"},
        )

        idea.refresh_from_db()
        self.assertFalse(idea.persona_review_enabled)
        self.assertEqual(idea.persona_stall_days, 14)

    def test_update_requires_permission_for_the_idea_status(self):
        idea = make_idea(status=Status.CURRENT, persona_stall_days=14)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:update_persona_council", args=[idea.pk]),
            {"persona_review_enabled": "on", "persona_stall_days": "30"},
        )

        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)
        idea.refresh_from_db()
        self.assertFalse(idea.persona_review_enabled)
        self.assertEqual(idea.persona_stall_days, 14)

    def test_council_review_can_be_paused_and_resumed(self):
        idea = make_idea(status=Status.CURRENT, persona_review_enabled=True)
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        url = reverse("ideas:toggle_persona_review_pause", args=[idea.pk])

        self.client.post(url)
        idea.refresh_from_db()
        self.assertTrue(idea.persona_review_paused)

        self.client.post(url)
        idea.refresh_from_db()
        self.assertFalse(idea.persona_review_paused)

    def test_pause_requires_permission_for_the_idea_status(self):
        idea = make_idea(status=Status.CURRENT, persona_review_enabled=True)
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:toggle_persona_review_pause", args=[idea.pk])
        )

        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)
        idea.refresh_from_db()
        self.assertFalse(idea.persona_review_paused)


class ResearchQuestionViewTests(TestCase):
    def test_open_question_is_displayed_and_answer_is_saved_for_next_run(self):
        from ideas.reporting import record_effort

        idea = make_idea(status=Status.CURRENT, agent_runs_since_feedback=2)
        entry, _resource = record_effort(
            idea,
            topic="Market choice",
            model="other",
            open_questions=["Which customer segment should we prioritize?"],
        )
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        detail = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertContains(detail, "Open research questions")
        self.assertContains(detail, "Which customer segment should we prioritize?")
        self.assertContains(detail, "data-question-answer-form")

        response = self.client.post(
            reverse("ideas:answer_research_questions", args=[idea.pk, entry.pk]),
            {"answer_0": "Focus on independent retailers first."},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "saved": 1})
        entry.refresh_from_db()
        idea.refresh_from_db()
        self.assertEqual(
            entry.question_answers,
            {"0": "Focus on independent retailers first."},
        )
        self.assertEqual(idea.agent_runs_since_feedback, 0)

        detail = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertNotContains(detail, "Which customer segment should we prioritize?")

    def test_wrong_status_role_cannot_answer_question(self):
        from ideas.reporting import record_effort

        idea = make_idea(status=Status.CURRENT)
        entry, _resource = record_effort(
            idea, topic="Question", model="other", open_questions=["Decide?"]
        )
        user = make_user(roles=["role_tracking"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:answer_research_questions", args=[idea.pk, entry.pk]),
            {"answer_0": "No"},
        )
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)
        entry.refresh_from_db()
        self.assertEqual(entry.question_answers, {})


class RepeatTaskViewTests(TestCase):
    def test_repeat_task_uses_results_table_instead_of_effort_summary(self):
        idea = make_idea(
            status=Status.CURRENT,
            repeat_enabled=True,
            repeat_goal="Find five good local job leads",
            exec_summary="This should not be the primary display.",
        )
        result = idea.repeat_results.create(
            title="Local Python role", url="https://jobs.example/1", details="Hybrid"
        )
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:detail", args=[idea.pk]))

        self.assertContains(response, "Repeat task results")
        self.assertContains(response, "Local Python role")
        self.assertNotContains(response, "Latest effort summary")

        self.client.post(
            reverse("ideas:update_repeat_result", args=[idea.pk, result.pk]),
            {"status": "actioned"},
        )
        result.refresh_from_db()
        self.assertEqual(result.status, "actioned")

        ajax_response = self.client.post(
            reverse("ideas:update_repeat_result", args=[idea.pk, result.pk]),
            {"status": "interested"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(ajax_response.status_code, 200)
        self.assertEqual(
            ajax_response.json(),
            {"ok": True, "result_id": result.pk, "status": "interested"},
        )
        result.refresh_from_db()
        self.assertEqual(result.status, "interested")
        self.assertContains(response, "data-repeat-result-form")

    def test_repeat_task_can_be_paused_and_resumed(self):
        idea = make_idea(status=Status.CURRENT, repeat_enabled=True, repeat_goal="Find leads")
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        url = reverse("ideas:toggle_repeat_pause", args=[idea.pk])

        self.client.post(url)
        idea.refresh_from_db()
        self.assertTrue(idea.repeat_paused)
        self.assertFalse(idea.repeat_is_due)

        self.client.post(url)
        idea.refresh_from_db()
        self.assertFalse(idea.repeat_paused)
        self.assertTrue(idea.repeat_is_due)


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

    def test_council_disagreement_is_prominent_and_filterable(self):
        needs_help = make_idea(
            title="Council needs intervention",
            status=Status.TRACKING,
        )
        needs_help.last_persona_review_at = timezone.now()
        needs_help.save(update_fields=["last_persona_review_at"])
        PersonaReview.objects.create(
            idea=needs_help,
            status=PersonaReview.Status.NO_CONSENSUS,
            proposal={"summary": "Personas disagreed"},
        )
        ordinary = make_idea(title="Ordinary tracking idea", status=Status.TRACKING)

        response = self.client.get(reverse("ideas:tracking"))

        self.assertContains(response, "Council disagreed — intervention needed")
        self.assertContains(response, 'class="tracking-item council-intervention"')
        self.assertContains(response, f'href="{needs_help.get_absolute_url()}#persona-council"')

        filtered = self.client.get(
            reverse("ideas:tracking"), {"attention": "council"}
        )
        self.assertContains(filtered, needs_help.title)
        self.assertNotContains(filtered, ordinary.title)

        PersonaReview.objects.create(
            idea=needs_help,
            status=PersonaReview.Status.CONSENSUS,
            proposal={"summary": "Resolved"},
        )
        resolved = self.client.get(reverse("ideas:tracking"))
        self.assertNotContains(resolved, "Council disagreed — intervention needed")

    def test_scheduled_council_review_shows_days_until_action(self):
        make_idea(
            title="Scheduled council idea",
            status=Status.TRACKING,
            persona_review_enabled=True,
            persona_stall_days=7,
            last_meaningful_progress_at=timezone.now() - timedelta(days=2),
        )

        response = self.client.get(reverse("ideas:tracking"))

        self.assertContains(response, "Council action in 5 days")

    def test_acted_council_review_shows_following_direction_tag(self):
        directed = make_idea(
            title="Council-directed idea",
            status=Status.TRACKING,
            persona_review_enabled=True,
        )
        directed.last_persona_review_at = timezone.now()
        directed.save(update_fields=["last_persona_review_at"])
        PersonaReview.objects.create(
            idea=directed,
            status=PersonaReview.Status.CONSENSUS,
            proposal={"next_action": "Test the agreed approach"},
        )

        response = self.client.get(reverse("ideas:tracking"))

        self.assertContains(response, "Following council direction")
        self.assertNotContains(response, "Council action in 14 days")

    def test_filters_are_marked_for_immediate_application(self):
        response = self.client.get(reverse("ideas:tracking"))

        self.assertContains(response, "data-auto-submit-filters")
        self.assertContains(response, "data-filter-apply")
        self.assertContains(response, 'src="/static/ideas/tracking.')

    def test_unanswered_research_questions_show_linked_indicator(self):
        from ideas.reporting import record_effort

        waiting = make_idea(title="Needs answer", status=Status.TRACKING)
        entry, _resource = record_effort(
            waiting,
            topic="Open decisions",
            model="other",
            open_questions=["Which region?", "What budget?"],
        )
        answered = make_idea(title="Already answered", status=Status.TRACKING)
        answered_entry, _resource = record_effort(
            answered,
            topic="Resolved decision",
            model="other",
            open_questions=["Which audience?"],
        )
        answered_entry.question_answers = {"0": "Retailers"}
        answered_entry.save(update_fields=["question_answers"])

        response = self.client.get(reverse("ideas:tracking"))

        rendered_waiting = next(
            idea for idea in response.context["ideas"] if idea.pk == waiting.pk
        )
        rendered_answered = next(
            idea for idea in response.context["ideas"] if idea.pk == answered.pk
        )
        self.assertEqual(rendered_waiting.open_question_count, 2)
        self.assertEqual(rendered_answered.open_question_count, 0)
        self.assertContains(response, "Human input needed (2)")
        self.assertContains(
            response, f'href="{waiting.get_absolute_url()}#open-questions"'
        )

    def test_default_sort_places_open_questions_first(self):
        from ideas.reporting import record_effort

        ordinary = make_idea(title="Ordinary", status=Status.TRACKING, rank=1)
        waiting = make_idea(
            title="Waiting for human", status=Status.TRACKING, rank=99
        )
        record_effort(
            waiting,
            topic="Decision",
            model="other",
            open_questions=["Approve the budget?"],
        )

        response = self.client.get(reverse("ideas:tracking"))

        self.assertEqual(response.context["filters"]["sort"], "questions")
        self.assertEqual(
            [idea.pk for idea in response.context["ideas"]],
            [waiting.pk, ordinary.pk],
        )

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

        response = self.client.get(reverse("ideas:tracking"), {"sort": "family"})

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

        response = self.client.get(reverse("ideas:tracking"), {"sort": "family"})

        rendered_parent = next(
            idea for idea in response.context["ideas"] if idea.pk == parent.pk
        )
        self.assertEqual(rendered_parent.tracking_child_count, 1)
        self.assertContains(response, 'data-family-toggle="%s"' % parent.pk)
        self.assertContains(response, "1 child")
        self.assertContains(response, 'data-parent-id="%s"' % parent.pk)
        self.assertContains(response, 'src="/static/ideas/tracking.')
        self.assertContains(response, "data-tracking-status-form")

    def test_child_toggle_is_only_shown_for_family_sort(self):
        parent = make_idea(status=Status.TRACKING)
        make_idea(status=Status.TRACKING, parent=parent)

        response = self.client.get(reverse("ideas:tracking"), {"sort": "rank"})

        self.assertNotContains(response, "data-family-toggle")

    def test_unknown_sort_falls_back_to_open_questions(self):
        response = self.client.get(reverse("ideas:tracking"), {"sort": "unknown"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filters"]["sort"], "questions")

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


class ArtifactViewTests(TestCase):
    def setUp(self):
        self.idea = make_idea(status=Status.CURRENT)
        self.user = make_user(roles=["role_current"])
        self.idea.created_by = self.user
        self.idea.save(update_fields=["created_by"])
        self.client.force_login(self.user, backend=MODEL_BACKEND)

    def test_idea_displays_referenced_artifact_from_same_owner(self):
        source = make_idea(title="Source idea", created_by=self.user)
        artifact = source.artifacts.create(
            title="Reusable report", url="https://example.com/reusable"
        )
        self.idea.referenced_artifacts.add(artifact)

        response = self.client.get(reverse("ideas:detail", args=[self.idea.pk]))

        self.assertContains(response, "Referenced artifacts")
        self.assertContains(response, "Reusable report")
        self.assertContains(response, "from Source idea")

    def test_can_upload_artifact_and_see_it_on_idea(self):
        response = self.client.post(
            reverse("ideas:add_artifact", args=[self.idea.pk]),
            {
                "title": "Competitor report",
                "kind": "report",
                "description": "A ranked comparison.",
                "file": SimpleUploadedFile("report.csv", b"name,score\nA,5\n"),
                "url": "",
                "generated_at": "2026-08-19T10:30",
                "research_entry": "",
            },
        )
        self.assertRedirects(response, reverse("ideas:detail", args=[self.idea.pk]))
        artifact = self.idea.artifacts.get()
        self.addCleanup(artifact.file.delete, save=False)
        detail = self.client.get(reverse("ideas:detail", args=[self.idea.pk]))
        self.assertContains(detail, "Competitor report")
        self.assertContains(detail, "A ranked comparison.")
        self.assertContains(
            detail,
            reverse("ideas:download_artifact", args=[self.idea.pk, artifact.pk]),
        )
        self.assertContains(detail, "Aug 19, 2026")

        download = self.client.get(
            reverse("ideas:download_artifact", args=[self.idea.pk, artifact.pk])
        )
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment;", download["Content-Disposition"])

    def test_can_delete_artifact(self):
        artifact = self.idea.artifacts.create(
            title="Old report", url="https://example.com/old-report"
        )
        response = self.client.post(
            reverse("ideas:delete_artifact", args=[self.idea.pk, artifact.pk])
        )
        self.assertRedirects(response, reverse("ideas:detail", args=[self.idea.pk]))
        self.assertFalse(self.idea.artifacts.filter(pk=artifact.pk).exists())

    def test_delete_artifact_requires_manage_role(self):
        artifact = self.idea.artifacts.create(
            title="Kept report", url="https://example.com/kept-report"
        )
        other = make_user("noroles@example.com")
        self.client.force_login(other)
        response = self.client.post(
            reverse("ideas:delete_artifact", args=[self.idea.pk, artifact.pk])
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(self.idea.artifacts.filter(pk=artifact.pk).exists())

    def test_later_research_can_be_recorded_when_updating_artifact(self):
        first = self.idea.research_entries.create(topic="Initial", model=make_ai_model())
        later = self.idea.research_entries.create(topic="Refresh", model=first.model)
        artifact = self.idea.artifacts.create(
            title="Lead list", url="https://example.com/leads", research_entry=first
        )
        response = self.client.post(
            reverse("ideas:edit_artifact", args=[self.idea.pk, artifact.pk]),
            {
                "title": "Lead list v2",
                "kind": "list",
                "description": "Updated by the refresh.",
                "url": "https://example.com/leads-v2",
                "generated_at": "2026-08-19T11:00",
                "research_entry": later.pk,
            },
        )
        self.assertRedirects(response, reverse("ideas:detail", args=[self.idea.pk]))
        artifact.refresh_from_db()
        self.assertEqual(artifact.title, "Lead list v2")
        self.assertEqual(artifact.research_entry, later)

    def test_artifact_requires_a_file_or_link(self):
        response = self.client.post(
            reverse("ideas:add_artifact", args=[self.idea.pk]),
            {
                "title": "Missing output",
                "generated_at": "2026-08-19T10:30",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a file or provide an external link.")
        self.assertFalse(self.idea.artifacts.exists())

    def test_summary_can_be_scheduled_for_archived_idea(self):
        idea = make_idea(status=Status.ARCHIVED)
        self.user.profile.role_archive = True
        self.user.profile.save(update_fields=["role_archive"])

        response = self.client.post(reverse("ideas:request_summary", args=[idea.pk]))

        self.assertRedirects(response, reverse("ideas:detail", args=[idea.pk]))
        idea.refresh_from_db()
        self.assertIsNotNone(idea.summary_requested_at)
        detail = self.client.get(reverse("ideas:detail", args=[idea.pk]))
        self.assertContains(detail, "Summary scheduled")


class EpisodeViewTests(TestCase):
    def setUp(self):
        self.idea = make_idea(status=Status.CURRENT)
        self.user = make_user(roles=["role_current"])
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        self.show = make_podcast_show(idea=self.idea)
        self.episode = make_episode(show=self.show, title="Ep One")

    def test_episode_appears_on_idea_detail_page(self):
        response = self.client.get(reverse("ideas:detail", args=[self.idea.pk]))
        self.assertContains(response, "Episodes")
        self.assertContains(response, "Ep One")

    def test_can_delete_episode(self):
        response = self.client.post(
            reverse("ideas:delete_episode", args=[self.idea.pk, self.episode.pk])
        )
        self.assertRedirects(response, reverse("ideas:detail", args=[self.idea.pk]))
        self.assertFalse(self.show.episodes.filter(pk=self.episode.pk).exists())

    def test_delete_episode_requires_manage_role(self):
        other = make_user("noroles@example.com")
        self.client.force_login(other, backend=MODEL_BACKEND)
        response = self.client.post(
            reverse("ideas:delete_episode", args=[self.idea.pk, self.episode.pk])
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(self.show.episodes.filter(pk=self.episode.pk).exists())

    def test_unpublish_clears_published_at_and_status_but_keeps_the_row(self):
        self.episode.status = EpisodeStatus.PUBLISHED
        self.episode.published_at = timezone.now()
        self.episode.save()

        response = self.client.post(
            reverse("ideas:unpublish_episode", args=[self.idea.pk, self.episode.pk])
        )

        self.assertRedirects(response, reverse("ideas:detail", args=[self.idea.pk]))
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, EpisodeStatus.UNPUBLISHED)
        self.assertIsNone(self.episode.published_at)

    def test_review_page_shows_script_and_render_report(self):
        run = EpisodeRun.objects.create(
            episode=self.episode,
            status=EpisodeRunStatus.READY_FOR_REVIEW,
            render_report={"segment_count": 2, "failures": 0, "overall_rtf": 3.0, "final": {}},
        )
        self.episode.script = {
            "segments": [{"speaker": "host", "voice_profile": "host-primary", "text": "Hello there."}]
        }
        self.episode.save(update_fields=["script"])

        response = self.client.get(reverse("ideas:episode_review", args=[self.idea.pk, self.episode.pk]))

        self.assertContains(response, "Hello there.")
        self.assertContains(response, run.engine)

    def test_approve_and_publish_requires_audio(self):
        response = self.client.post(
            reverse("ideas:approve_and_publish_episode", args=[self.idea.pk, self.episode.pk])
        )
        self.assertRedirects(
            response, reverse("ideas:episode_review", args=[self.idea.pk, self.episode.pk])
        )
        self.episode.refresh_from_db()
        self.assertNotEqual(self.episode.status, EpisodeStatus.PUBLISHED)

    def test_approve_and_publish_with_audio_publishes(self):
        self.episode.audio_file.save("episode.mp3", SimpleUploadedFile("episode.mp3", b"fake-audio"), save=True)

        response = self.client.post(
            reverse("ideas:approve_and_publish_episode", args=[self.idea.pk, self.episode.pk])
        )

        self.assertRedirects(
            response, reverse("ideas:episode_review", args=[self.idea.pk, self.episode.pk])
        )
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, EpisodeStatus.PUBLISHED)
        self.assertIsNotNone(self.episode.published_at)
        self.assertEqual(self.episode.published_by, self.user)
        self.episode.audio_file.delete(save=False)

    def test_reject_marks_ready_run_failed(self):
        run = EpisodeRun.objects.create(episode=self.episode, status=EpisodeRunStatus.READY_FOR_REVIEW)
        response = self.client.post(
            reverse("ideas:reject_episode", args=[self.idea.pk, self.episode.pk])
        )
        self.assertRedirects(
            response, reverse("ideas:episode_review", args=[self.idea.pk, self.episode.pk])
        )
        run.refresh_from_db()
        self.assertEqual(run.status, EpisodeRunStatus.FAILED)
        self.assertEqual(run.error_class, "rejected_by_reviewer")

    def test_reject_with_no_ready_run_reports_nothing_to_reject(self):
        # Regression test: this used to show "Episode rejected" even when
        # nothing was actually rejected.
        response = self.client.post(
            reverse("ideas:reject_episode", args=[self.idea.pk, self.episode.pk]), follow=True
        )
        self.assertContains(response, "Nothing to reject")

    def test_cancel_stops_a_pending_run(self):
        run = EpisodeRun.objects.create(episode=self.episode, status=EpisodeRunStatus.AWAITING_AUDIO)
        response = self.client.post(
            reverse("ideas:cancel_episode_run", args=[self.idea.pk, self.episode.pk])
        )
        self.assertRedirects(
            response, reverse("ideas:episode_review", args=[self.idea.pk, self.episode.pk])
        )
        run.refresh_from_db()
        self.assertEqual(run.status, EpisodeRunStatus.CANCELLED)

    def test_cancel_with_no_pending_run_reports_nothing_to_cancel(self):
        response = self.client.post(
            reverse("ideas:cancel_episode_run", args=[self.idea.pk, self.episode.pk]), follow=True
        )
        self.assertContains(response, "Nothing to cancel")

    def test_cancel_does_not_touch_a_ready_for_review_run(self):
        # Cancel is for in-flight jobs; a completed, reviewable render is
        # Reject's job, not Cancel's — the two must not overlap.
        run = EpisodeRun.objects.create(episode=self.episode, status=EpisodeRunStatus.READY_FOR_REVIEW)
        response = self.client.post(
            reverse("ideas:cancel_episode_run", args=[self.idea.pk, self.episode.pk]), follow=True
        )
        self.assertContains(response, "Nothing to cancel")
        run.refresh_from_db()
        self.assertEqual(run.status, EpisodeRunStatus.READY_FOR_REVIEW)

    def test_regenerate_creates_a_new_run_from_the_previous_manifest(self):
        previous = EpisodeRun.objects.create(
            episode=self.episode, status=EpisodeRunStatus.FAILED,
            manifest={"schema_version": 1, "episode_id": self.episode.pk, "run_id": 999999},
        )
        response = self.client.post(
            reverse("ideas:regenerate_episode", args=[self.idea.pk, self.episode.pk])
        )
        self.assertRedirects(
            response, reverse("ideas:episode_review", args=[self.idea.pk, self.episode.pk])
        )
        self.assertEqual(self.episode.runs.count(), 2)
        newest = self.episode.runs.order_by("-created_at").first()
        self.assertNotEqual(newest.pk, previous.pk)
        self.assertEqual(newest.status, EpisodeRunStatus.AWAITING_AUDIO)
        self.assertEqual(newest.manifest["schema_version"], 1)
        # Regression test: a regenerated run's manifest must name *itself*,
        # not the run it was copied from.
        self.assertEqual(newest.manifest["run_id"], newest.pk)
        self.assertEqual(newest.manifest["episode_id"], self.episode.pk)

    def test_update_episode_saves_title_and_show_notes(self):
        response = self.client.post(
            reverse("ideas:update_episode", args=[self.idea.pk, self.episode.pk]),
            {"title": "New Title", "description": "New desc", "show_notes": "Notes here"},
        )
        self.assertRedirects(
            response, reverse("ideas:episode_review", args=[self.idea.pk, self.episode.pk])
        )
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.title, "New Title")
        self.assertEqual(self.episode.show_notes, "Notes here")

    def test_audio_preview_requires_login(self):
        self.client.logout()
        response = self.client.get(
            reverse("ideas:episode_audio_preview", args=[self.idea.pk, self.episode.pk])
        )
        self.assertNotEqual(response.status_code, 200)

    def test_audio_preview_404s_without_audio(self):
        response = self.client.get(
            reverse("ideas:episode_audio_preview", args=[self.idea.pk, self.episode.pk])
        )
        self.assertEqual(response.status_code, 404)


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
        self.assertEqual(child.created_by, user)
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


class IdeaOwnershipTests(TestCase):
    def test_admin_can_reassign_an_idea(self):
        admin = make_user(email="admin@example.com", roles=["role_admin"])
        owner = make_user(email="owner@example.com")
        idea = make_idea(created_by=admin)
        self.client.force_login(admin, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:reassign_idea", args=[idea.pk]),
            {"created_by": owner.pk},
        )

        self.assertRedirects(response, reverse("ideas:idea_ownership"))
        idea.refresh_from_db()
        self.assertEqual(idea.created_by, owner)

    def test_non_admin_cannot_manage_ownership(self):
        user = make_user(roles=["role_current"])
        idea = make_idea(created_by=user)
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:reassign_idea", args=[idea.pk]),
            {"created_by": user.pk},
        )

        self.assertRedirects(
            response, reverse("ideas:home"), fetch_redirect_response=False
        )

    def test_current_list_can_filter_to_signed_in_users_ideas(self):
        user = make_user(email="mine@example.com", roles=["role_current"])
        other = make_user(email="other@example.com")
        make_idea(title="Mine", created_by=user)
        make_idea(title="Theirs", created_by=other)
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:current"), {"owner": "mine"})

        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Theirs")

    def test_current_list_can_search_across_owners_and_select_an_owner(self):
        user = make_user(email="viewer@example.com", roles=["role_current"])
        first_owner = make_user(email="first@example.com")
        second_owner = make_user(email="second@example.com")
        make_idea(title="Shared roadmap", created_by=first_owner)
        make_idea(title="Other owner's plan", created_by=second_owner)
        self.client.force_login(user, backend=MODEL_BACKEND)

        searched = self.client.get(reverse("ideas:current"), {"q": "roadmap"})
        self.assertContains(searched, "Shared roadmap")
        self.assertNotContains(searched, "Other owner&#x27;s plan")
        self.assertContains(searched, "first@example.com")
        self.assertContains(searched, "second@example.com")

        selected = self.client.get(
            reverse("ideas:current"), {"owner": str(second_owner.pk)}
        )
        self.assertContains(selected, "Other owner&#x27;s plan")
        self.assertNotContains(selected, "Shared roadmap")

    def test_tracking_list_can_filter_to_signed_in_users_ideas(self):
        user = make_user(email="mine@example.com", roles=["role_tracking"])
        other = make_user(email="other@example.com")
        make_idea(title="Mine tracked", status=Status.TRACKING, created_by=user)
        make_idea(title="Their tracked", status=Status.TRACKING, created_by=other)
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:tracking"), {"owner": "mine"})

        self.assertContains(response, "Mine tracked")
        self.assertNotContains(response, "Their tracked")

    def test_tracking_list_can_select_any_owner(self):
        user = make_user(email="viewer@example.com", roles=["role_tracking"])
        owner = make_user(email="project-owner@example.com")
        other = make_user(email="another-owner@example.com")
        make_idea(title="Selected owner's idea", status=Status.TRACKING, created_by=owner)
        make_idea(title="Other tracked idea", status=Status.TRACKING, created_by=other)
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(
            reverse("ideas:tracking"), {"owner": str(owner.pk)}
        )

        self.assertContains(response, "Selected owner&#x27;s idea")
        self.assertNotContains(response, "Other tracked idea")
        self.assertContains(response, "project-owner@example.com")


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
