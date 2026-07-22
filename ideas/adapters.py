from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    """Google is the only way in — block the regular username/password signup form."""

    def is_open_for_signup(self, request):
        return False
