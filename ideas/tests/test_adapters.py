from django.test import TestCase

from ideas.adapters import AccountAdapter, SocialAccountAdapter


class AdapterSignupTests(TestCase):
    def test_local_signup_is_closed(self):
        self.assertFalse(AccountAdapter().is_open_for_signup(None))

    def test_social_signup_stays_open(self):
        # DefaultSocialAccountAdapter.is_open_for_signup delegates to the
        # account adapter by default, which would otherwise block Google too.
        self.assertTrue(SocialAccountAdapter().is_open_for_signup(None, None))
