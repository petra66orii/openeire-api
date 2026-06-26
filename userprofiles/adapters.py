import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model


logger = logging.getLogger(__name__)


class OpenEireSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Connect verified Google logins to existing email/password accounts."""

    def pre_social_login(self, request, sociallogin):
        super().pre_social_login(request, sociallogin)

        if sociallogin.is_existing:
            return

        email = (sociallogin.user.email or "").strip().lower()
        if not email:
            return

        matching_verified_email = next(
            (
                email_address
                for email_address in sociallogin.email_addresses
                if (email_address.email or "").strip().lower() == email
                and email_address.verified
            ),
            None,
        )
        if not matching_verified_email:
            return

        User = get_user_model()
        existing_user = (
            User.objects.filter(email__iexact=email, is_active=True)
            .order_by("id")
            .first()
        )
        if not existing_user:
            return

        logger.info(
            "Connecting verified Google login to existing user. user_id=%s",
            existing_user.id,
        )
        sociallogin.connect(request, existing_user)
