from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    """Blocks the regular username/password signup form only."""

    def is_open_for_signup(self, request):
        return False


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Google is the only way in — signup via a social provider stays open.

    The default implementation delegates to AccountAdapter.is_open_for_signup,
    which would otherwise block Google sign-in too since it always returns False.
    """

    def is_open_for_signup(self, request, sociallogin):
        return True
