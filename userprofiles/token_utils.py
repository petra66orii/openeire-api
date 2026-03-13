from datetime import timedelta

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import Token


EMAIL_VERIFICATION_PURPOSE = "email_verification"
PASSWORD_RESET_PURPOSE = "password_reset"


class UserActionToken(Token):
    """
    Token used for one-off user actions (email verification/password reset).
    It intentionally uses a distinct token_type so DRF JWTAuthentication
    does not treat it as a normal bearer access token.
    """

    token_type = "action"
    lifetime = timedelta(minutes=15)


def issue_user_action_token(*, user, purpose, lifetime_minutes):
    token = UserActionToken.for_user(user)
    token["purpose"] = purpose
    token.set_exp(lifetime=timedelta(minutes=max(1, int(lifetime_minutes))))
    return str(token)


def decode_user_action_token(*, token, expected_purpose):
    parsed = UserActionToken(token)
    if parsed.get("purpose") != expected_purpose:
        raise TokenError("Token purpose mismatch.")

    user_id = parsed.get("user_id")
    if user_id is None:
        raise TokenError("Token user is missing.")

    return int(user_id)
