from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ideas.models import AGENT_RUNS_BEFORE_FEEDBACK, STANDING_ADMIN_EMAIL, IdeaPersona, Persona, Profile, Status

from .helpers import make_ai_model, make_category, make_idea


class LookupBaseSlugTests(TestCase):
    def test_slug_is_auto_generated_from_name(self):
        # Note: "Weekend Hack" (not e.g. "Side Project") deliberately avoids the
        # names migration 0002_seed_lookups already inserts into the test DB.
        category = make_category(name="Weekend Hack")
        self.assertEqual(category.slug, "weekend-hack")

    def test_explicit_slug_is_preserved(self):
        category = make_category(name="Weekend Hack Redux", slug="custom-slug")
        self.assertEqual(category.slug, "custom-slug")

    def test_slug_never_exceeds_max_length(self):
        # A 60-char name is the longest the field itself allows (real usage
        # is always form-validated against max_length before save() runs);
        # this just locks in the defensive [:60] slice in LookupBase.save(),
        # without depending on SQLite's lack of VARCHAR length enforcement
        # to accept an already-invalid, over-max_length name like Postgres does.
        category = make_category(name="x" * 60)
        self.assertLessEqual(len(category.slug), 60)

    def test_tint_appends_alpha_suffix(self):
        category = make_category(color="#24509b")
        self.assertEqual(category.tint, "#24509b1f")


class IdeaStarsTests(TestCase):
    def test_stars_reflects_interest_level(self):
        idea = make_idea(interest_level=3)
        self.assertEqual(idea.stars, "★★★☆☆")

    def test_stars_all_filled_at_max(self):
        idea = make_idea(interest_level=5)
        self.assertEqual(idea.stars, "★★★★★")


class PersonaReviewIsDueTests(TestCase):
    def make_stalled_idea(self, **kwargs):
        idea = make_idea(
            persona_review_enabled=True,
            persona_stall_days=7,
            last_meaningful_progress_at=timezone.now() - timedelta(days=30),
            **kwargs,
        )
        persona = Persona.objects.create(
            name=f"Persona {idea.pk}", description="d", goals="g"
        )
        IdeaPersona.objects.create(idea=idea, persona=persona, required=True, active=True)
        return idea

    def test_council_review_overrides_the_ordinary_human_feedback_pause(self):
        idea = self.make_stalled_idea(agent_runs_since_feedback=AGENT_RUNS_BEFORE_FEEDBACK)
        self.assertTrue(idea.is_paused)
        self.assertTrue(idea.persona_review_is_due)

    def test_manual_persona_review_pause_holds_review_even_when_not_stalled_on_feedback(self):
        idea = self.make_stalled_idea(persona_review_paused=True)
        self.assertFalse(idea.is_paused)
        self.assertFalse(idea.persona_review_is_due)
        self.assertIsNone(idea.persona_review_days_until)


class ResearchEntryStarsTests(TestCase):
    def test_effort_and_quality_stars(self):
        idea = make_idea()
        entry = idea.research_entries.create(
            topic="Landscape scan",
            model=make_ai_model(),
            effort=2,
            quality=4,
        )
        self.assertEqual(entry.effort_stars, "★★☆☆☆")
        self.assertEqual(entry.quality_stars, "★★★★☆")


class ProfileRoleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", email="u@example.com")
        self.profile = self.user.profile

    def test_admin_role_bypasses_specific_role_checks(self):
        self.profile.role_admin = True
        self.profile.save()
        self.assertTrue(self.profile.has_role("role_current"))
        self.assertTrue(self.profile.can_manage_status(Status.TRACKING))

    def test_specific_role_without_admin(self):
        self.profile.role_tracking = True
        self.profile.save()
        self.assertTrue(self.profile.has_role("role_tracking"))
        self.assertFalse(self.profile.has_role("role_current"))
        self.assertTrue(self.profile.can_manage_status(Status.TRACKING))
        self.assertFalse(self.profile.can_manage_status(Status.CURRENT))

    def test_no_roles_denies_everything(self):
        self.assertFalse(self.profile.has_role("role_current", "role_tracking"))
        for status in Status:
            self.assertFalse(self.profile.can_manage_status(status))

    def test_can_manage_status_covers_all_three_tabs(self):
        for status, role in Profile.STATUS_ROLE.items():
            profile = User.objects.create_user(
                username=f"user-{role}", email=f"{role}@example.com"
            ).profile
            setattr(profile, role, True)
            profile.save()
            self.assertTrue(profile.can_manage_status(status))


class ProfileAdminSyncTests(TestCase):
    def test_granting_role_admin_makes_user_staff_and_superuser(self):
        user = User.objects.create_user(username="u", email="u@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        profile = user.profile
        profile.role_admin = True
        profile.save()

        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_revoking_role_admin_strips_staff_and_superuser(self):
        user = User.objects.create_user(username="u", email="u@example.com")
        profile = user.profile
        profile.role_admin = True
        profile.save()

        profile.role_admin = False
        profile.save()

        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)


class ProvisionProfileSignalTests(TestCase):
    def test_standing_admin_email_gets_every_role(self):
        user = User.objects.create_user(username="brad", email=STANDING_ADMIN_EMAIL)
        profile = user.profile
        self.assertTrue(profile.role_admin)
        self.assertTrue(profile.role_current)
        self.assertTrue(profile.role_tracking)
        self.assertTrue(profile.role_archive)
        self.assertTrue(profile.role_add_ideas)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_standing_admin_match_is_case_insensitive(self):
        user = User.objects.create_user(username="brad2", email="BZeitner@Gmail.com")
        self.assertTrue(user.profile.role_admin)

    def test_other_emails_get_no_roles(self):
        user = User.objects.create_user(username="other", email="other@example.com")
        profile = user.profile
        self.assertFalse(profile.role_admin)
        self.assertFalse(profile.role_current)
        self.assertFalse(profile.role_tracking)
        self.assertFalse(profile.role_archive)
        self.assertFalse(profile.role_add_ideas)

    def test_createsuperuser_style_creation_is_not_immediately_undone(self):
        # manage.py createsuperuser sets is_superuser=True before the first
        # save() fires post_save — Profile.save()'s sync must not clobber it.
        user = User.objects.create_superuser(
            username="root", email="root@example.com", password="x"
        )
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.profile.role_admin)

    def test_signal_only_provisions_on_creation_not_on_update(self):
        user = User.objects.create_user(username="other", email="other@example.com")
        profile = user.profile
        self.assertFalse(profile.role_admin)

        user.email = STANDING_ADMIN_EMAIL
        user.save()

        profile.refresh_from_db()
        self.assertFalse(profile.role_admin)
