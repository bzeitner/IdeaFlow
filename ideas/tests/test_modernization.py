from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from ideas.artifact_presentation import MAX_RENDER_CHARS, EMBEDDED_HTML_CSP, present_artifact, render_markdown
from ideas.middleware import TrackLastSeenMiddleware
from ideas.models import Artifact, EpisodeStatus, Profile, Status
from ideas.tests.helpers import MODEL_BACKEND, make_ai_model, make_episode, make_idea, make_podcast_show, make_user


class PreferenceTests(TestCase):
    def setUp(self):
        self.user = make_user("preferences@example.com", roles=["role_current", "role_tracking"])
        self.client.force_login(self.user, backend=MODEL_BACKEND)

    def test_preferences_page_updates_cross_device_defaults(self):
        response = self.client.post(
            reverse("ideas:preferences"),
            {
                "default_landing_page": "tracking",
                "default_owner_scope": "mine",
                "default_tracking_sort": "updated",
                "default_feed_sort": "published_desc",
                "list_density": "compact",
                "default_new_idea_status": "tracking",
                "default_new_idea_public": "on",
                "timezone_name": "Europe/London",
            },
        )
        self.assertRedirects(response, reverse("ideas:preferences"))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.default_landing_page, Profile.LandingPage.TRACKING)
        self.assertEqual(self.user.profile.default_owner_scope, "mine")
        self.assertEqual(self.user.profile.default_tracking_sort, "updated")
        self.assertEqual(self.user.profile.list_density, Profile.Density.COMPACT)
        self.assertTrue(self.user.profile.default_new_idea_public)

    def test_tracking_uses_preferences_only_when_url_does_not_override_them(self):
        self.user.profile.default_owner_scope = "mine"
        self.user.profile.default_tracking_sort = "updated"
        self.user.profile.save(update_fields=["default_owner_scope", "default_tracking_sort"])
        own = make_idea(title="Mine", status=Status.TRACKING, created_by=self.user)
        make_idea(title="Someone else's", status=Status.TRACKING)

        default_response = self.client.get(reverse("ideas:tracking"))
        self.assertContains(default_response, own.title)
        self.assertNotContains(default_response, "Someone else&#x27;s")
        self.assertEqual(default_response.context["filters"]["sort"], "updated")

        explicit_response = self.client.get(reverse("ideas:tracking"), {"owner": "", "sort": "rank"})
        self.assertContains(explicit_response, "Someone else&#x27;s")
        self.assertEqual(explicit_response.context["filters"]["sort"], "rank")

    def test_invalid_timezone_has_friendly_error(self):
        response = self.client.post(
            reverse("ideas:preferences"),
            {
                "default_landing_page": "current",
                "default_owner_scope": "all",
                "default_tracking_sort": "questions",
                "default_feed_sort": "published_desc",
                "list_density": "comfortable",
                "default_new_idea_status": "current",
                "timezone_name": "Not/A-Timezone",
            },
        )
        self.assertContains(response, "Enter a valid time zone")

    def test_start_route_honors_authenticated_landing_preference(self):
        self.user.profile.default_landing_page = Profile.LandingPage.TRACKING
        self.user.profile.save(update_fields=["default_landing_page"])
        response = self.client.get(reverse("ideas:start"))
        self.assertRedirects(response, reverse("ideas:tracking"))

    def test_preferences_only_offer_authorized_destinations(self):
        limited = make_user("limited-preferences@example.com", roles=["role_add_ideas"])
        self.client.force_login(limited, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:preferences"))
        self.assertNotContains(response, '<option value="tracking">Tracking</option>', html=True)
        self.assertNotContains(response, '<option value="archived">Archived</option>', html=True)

    def test_profile_timezone_is_active_while_rendering_request(self):
        self.user.profile.timezone_name = "Europe/London"
        self.user.profile.save(update_fields=["timezone_name"])
        observed = []

        def endpoint(request):
            observed.append(timezone.get_current_timezone_name())
            return HttpResponse("ok")

        request = RequestFactory().get("/")
        request.user = self.user
        response = TrackLastSeenMiddleware(endpoint)(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed, ["Europe/London"])


class ArtifactPresentationTests(TestCase):
    def setUp(self):
        self.user = make_user("artifact-reader@example.com", roles=["role_current"])
        self.idea = make_idea(status=Status.CURRENT, created_by=self.user)
        self.client.force_login(self.user, backend=MODEL_BACKEND)

    def _artifact(self, name, content, kind=Artifact.Kind.REPORT):
        artifact = self.idea.artifacts.create(
            title="Readable output",
            kind=kind,
            file=SimpleUploadedFile(name, content.encode()),
        )
        self.addCleanup(artifact.file.delete, save=False)
        return artifact

    def test_csv_opens_as_searchable_accessible_table(self):
        artifact = self._artifact("results.csv", "Company,Score\nAcme,5\nBright,4", Artifact.Kind.LIST)
        response = self.client.get(reverse("ideas:view_artifact", args=[self.idea.pk, artifact.pk]))
        self.assertContains(response, 'data-artifact-table')
        self.assertContains(response, '<th scope="col">Company</th>', html=True)
        self.assertContains(response, "Search this table")
        self.assertContains(response, "Download original")

    def test_markdown_summary_opens_as_human_readable_report(self):
        artifact = self._artifact(
            "summary.md",
            "# Executive summary\n\nThe opportunity is **promising**.\n\n## Next actions\n\n- Interview users",
            Artifact.Kind.SUMMARY,
        )
        response = self.client.get(reverse("ideas:view_artifact", args=[self.idea.pk, artifact.pk]))
        self.assertContains(response, 'class="artifact-report"')
        self.assertContains(response, "<h2>Executive summary</h2>", html=True)
        self.assertContains(response, "<strong>promising</strong>", html=True)

    def test_raw_view_preserves_source_as_escaped_text(self):
        artifact = self._artifact("unsafe.md", "# Report\n<script>alert(1)</script>")
        response = self.client.get(
            reverse("ideas:view_artifact", args=[self.idea.pk, artifact.pk]), {"view": "raw"}
        )
        self.assertContains(response, "Raw MARKDOWN source")
        self.assertNotContains(response, "<script>alert(1)</script>", html=True)
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_markdown_does_not_render_source_html(self):
        rendered = str(render_markdown("# Safe\n\n<img src=x onerror=alert(1)>"))
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;img", rendered)

    def test_flat_json_uses_table_and_nested_json_uses_structured_view(self):
        flat = self._artifact("flat.json", '[{"name":"A","score":5}]', Artifact.Kind.LIST)
        nested = self._artifact("nested.json", '{"group":{"name":"A"}}', Artifact.Kind.LIST)
        self.assertEqual(present_artifact(flat, '[{"name":"A","score":5}]')["view"], "table")
        self.assertEqual(present_artifact(nested, '{"group":{"name":"A"}}')["view"], "structured")

    def test_embedded_html_blocks_external_network_resources(self):
        hostile = (
            '<meta http-equiv="refresh" content="0;url=https://tracker.example/refresh">'
            '<img src="https://tracker.example/pixel">'
            '<script>location="https://tracker.example/script"</script>'
            '<p>Readable report</p>'
        )
        artifact = self._artifact("report.html", hostile)
        presentation = present_artifact(artifact, hostile)
        self.assertEqual(presentation["view"], "html")
        self.assertTrue(presentation["content"].startswith(EMBEDDED_HTML_CSP))
        self.assertIn("default-src 'none'", presentation["content"])
        self.assertNotIn('http-equiv="refresh"', presentation["content"].lower())
        self.assertNotIn("tracker.example", presentation["content"])
        self.assertNotIn("<script", presentation["content"])
        self.assertIn("Readable report", presentation["content"])

        response = self.client.get(reverse("ideas:view_artifact", args=[self.idea.pk, artifact.pk]))
        self.assertContains(response, 'referrerpolicy="no-referrer"')

    def test_deep_json_fails_closed_to_raw_view(self):
        artifact = self._artifact("deep.json", "[]", Artifact.Kind.LIST)
        deeply_nested = "[" * 70 + "0" + "]" * 70
        presentation = present_artifact(artifact, deeply_nested)
        self.assertEqual(presentation["view"], "raw")
        self.assertTrue(presentation["malformed"])

    def test_view_reads_only_bounded_preview_of_large_artifact(self):
        content = "A" * (MAX_RENDER_CHARS * 4) + "TAIL-MUST-NOT-BE-READ"
        artifact = self._artifact("large.txt", content, Artifact.Kind.LIST)
        response = self.client.get(
            reverse("ideas:view_artifact", args=[self.idea.pk, artifact.pk]), {"view": "raw"}
        )
        self.assertContains(response, "preview is shortened")
        self.assertNotContains(response, "TAIL-MUST-NOT-BE-READ")

    def test_markdown_filter_includes_existing_md_files(self):
        artifact = self._artifact("existing.md", "# Existing report")
        response = self.client.get(reverse("ideas:artifacts"), {"format": "markdown"})
        self.assertContains(response, artifact.title)

    def test_artifact_filters_hide_unrelated_podcast_section(self):
        show = make_podcast_show(is_publicly_listed=True)
        make_episode(show=show, status=EpisodeStatus.PUBLISHED, published_at=timezone.now())
        response = self.client.get(reverse("ideas:artifacts"), {"format": "json"})
        self.assertNotContains(response, "Published podcasts")


class ResearchPresentationTests(TestCase):
    def setUp(self):
        self.user = make_user("research-reader@example.com", roles=["role_current"])
        self.idea = make_idea(status=Status.CURRENT, created_by=self.user)
        self.model = make_ai_model()
        self.client.force_login(self.user, backend=MODEL_BACKEND)

    def _entry(self, **kwargs):
        defaults = {
            "topic": "Market review",
            "context": "# Findings\n\n- One\n- Two",
            "model": self.model,
        }
        defaults.update(kwargs)
        return self.idea.research_entries.create(**defaults)

    def test_research_effort_opens_as_formatted_markdown_with_raw_option(self):
        entry = self._entry()
        url = reverse("ideas:view_research_entry", args=[self.idea.pk, entry.pk])

        formatted = self.client.get(url)
        self.assertContains(formatted, "<h2>Findings</h2>", html=True)
        self.assertContains(formatted, "Research effort views")
        self.assertNotContains(formatted, "Download original")

        raw = self.client.get(url, {"view": "raw"})
        self.assertContains(raw, "Raw MARKDOWN source")
        self.assertContains(raw, "# Findings")

    def test_research_formats_a_bounded_preview_but_raw_remains_complete(self):
        entry = self._entry(context="A" * (MAX_RENDER_CHARS + 10) + "COMPLETE-TAIL")
        url = reverse("ideas:view_research_entry", args=[self.idea.pk, entry.pk])

        formatted = self.client.get(url)
        self.assertContains(formatted, "preview is shortened")
        self.assertNotContains(formatted, "COMPLETE-TAIL")

        raw = self.client.get(
            url,
            {"view": "raw"},
        )
        self.assertContains(raw, "COMPLETE-TAIL")
        self.assertNotContains(raw, "preview is shortened")

    def test_references_link_only_known_entries_and_not_code(self):
        target = self._entry(topic="Earlier")
        entry = self._entry(
            topic="Follow-up",
            context=f"Research effort #{target.pk} confirms it. `effort #{target.pk}` and effort #999 do not link.",
        )
        response = self.client.get(
            reverse("ideas:view_research_entry", args=[self.idea.pk, entry.pk])
        )
        expected = f'{reverse("ideas:detail", args=[self.idea.pk])}#research-entry-{target.pk}'
        self.assertContains(response, f'href="{expected}"')
        self.assertContains(response, f"<code>effort #{target.pk}</code>", html=True)
        self.assertNotContains(response, 'href="#research-entry-999"')

    def test_research_view_enforces_idea_access_and_parent_constraint(self):
        entry = self._entry()
        other = make_idea(status=Status.CURRENT)
        mismatched = self.client.get(
            reverse("ideas:view_research_entry", args=[other.pk, entry.pk])
        )
        self.assertEqual(mismatched.status_code, 404)

        unauthorized = make_user("tracking-only@example.com", roles=["role_tracking"])
        self.client.force_login(unauthorized, backend=MODEL_BACKEND)
        denied = self.client.get(
            reverse("ideas:view_research_entry", args=[self.idea.pk, entry.pk])
        )
        self.assertRedirects(denied, reverse("ideas:home"), fetch_redirect_response=False)

        self.idea.is_public = True
        self.idea.save(update_fields=["is_public"])
        allowed = self.client.get(
            reverse("ideas:view_research_entry", args=[self.idea.pk, entry.pk])
        )
        self.assertEqual(allowed.status_code, 200)

    def test_idea_detail_uses_excerpt_and_full_review_link(self):
        entry = self._entry(context=" ".join(f"word-{number}" for number in range(80)))
        response = self.client.get(reverse("ideas:detail", args=[self.idea.pk]))
        self.assertContains(response, "Open full review")
        self.assertContains(
            response, reverse("ideas:view_research_entry", args=[self.idea.pk, entry.pk])
        )
        self.assertNotContains(response, "word-79")


class AuthorizedCreationDefaultTests(TestCase):
    def test_unavailable_saved_status_is_shown_as_current(self):
        user = make_user("creator-only@example.com", roles=["role_add_ideas"])
        user.profile.default_new_idea_status = Status.TRACKING
        user.profile.save(update_fields=["default_new_idea_status"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        form_response = self.client.get(reverse("ideas:create"))
        self.assertEqual(form_response.context["form"].initial["status"], Status.CURRENT)


class ModernizedShellTests(TestCase):
    def test_authenticated_shell_has_accessibility_and_account_navigation(self):
        user = make_user("shell@example.com", roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:current"))
        self.assertContains(response, 'class="skip-link"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, reverse("ideas:preferences"))
        self.assertContains(response, "Workspace")

    def test_guide_has_stable_contextual_help_anchors(self):
        response = self.client.get(reverse("ideas:guide"))
        self.assertContains(response, 'id="saved-views"')
        self.assertContains(response, 'id="artifact-views"')
        self.assertContains(response, 'id="current-tracking-archive"')


class FriendlyInlineUpdateTests(TestCase):
    def test_tracking_quick_update_returns_save_feedback_payload(self):
        user = make_user("inline@example.com", roles=["role_tracking"])
        idea = make_idea(status=Status.TRACKING, created_by=user)
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.post(
            reverse("ideas:quick_update", args=[idea.pk]),
            {"field": "next_action", "value": "Interview three users"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "field": "next_action", "value": "Interview three users"})
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "Interview three users")

    def test_tracking_renders_progressive_enhancement_save_status(self):
        user = make_user("inline-ui@example.com", roles=["role_tracking"])
        make_idea(status=Status.TRACKING, created_by=user)
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:tracking"))
        self.assertContains(response, "data-auto-save")
        self.assertContains(response, 'data-save-status aria-live="polite"')
