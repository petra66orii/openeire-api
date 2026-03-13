from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken
from unittest.mock import patch

from .token_utils import (
    EMAIL_VERIFICATION_PURPOSE,
    PASSWORD_RESET_PURPOSE,
    issue_user_action_token,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class PasswordResetRequestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="resetuser",
            email="reset@example.com",
            password="StrongPass123!",
            is_active=True,
        )
        self.url = reverse("password_reset_request")

    def test_password_reset_request_sends_email_with_reset_link(self):
        response = self.client.post(self.url, data={"email": self.user.email})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "Password reset link sent.")
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn(self.user.email, sent.to)
        self.assertIn("/password-reset/confirm/", sent.body)
        self.assertNotIn("PASSWORD RESET TOKEN", sent.body)

    @override_settings(FRONTEND_URL="https://app.openeire.online")
    def test_password_reset_request_uses_configured_frontend_url(self):
        response = self.client.post(self.url, data={"email": self.user.email})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(
            "https://app.openeire.online/password-reset/confirm/",
            mail.outbox[0].body,
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class EmailUniquenessHardeningTests(TestCase):
    def setUp(self):
        self.register_url = reverse("auth_register")
        self.reset_url = reverse("password_reset_request")
        self.resend_url = reverse("auth_resend_verification")
        self.login_url = reverse("auth_login")

    def test_register_rejects_duplicate_email_case_insensitive(self):
        User.objects.create_user(
            username="existinguser",
            email="same@example.com",
            password="StrongPass123!",
            is_active=False,
        )

        payload = {
            "username": "newuser",
            "email": "SAME@example.com",
            "password": "StrongPass123!",
        }
        response = self.client.post(self.register_url, data=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json())

    def test_db_enforces_case_insensitive_email_uniqueness(self):
        User.objects.create_user(
            username="unique1",
            email="dup@example.com",
            password="StrongPass123!",
            is_active=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="unique2",
                    email="DUP@example.com",
                    password="StrongPass123!",
                    is_active=True,
                )

    def test_db_enforces_trimmed_case_insensitive_email_uniqueness(self):
        User.objects.create_user(
            username="trimunique1",
            email="trimdup@example.com ",
            password="StrongPass123!",
            is_active=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="trimunique2",
                    email=" TRIMDUP@example.com",
                    password="StrongPass123!",
                    is_active=True,
                )

    @patch("userprofiles.serializers.User.save", side_effect=IntegrityError("duplicate email"))
    def test_register_handles_unknown_integrity_error_cleanly(self, _mock_save):
        payload = {
            "username": "raceuser",
            "email": "race@example.com",
            "password": "StrongPass123!",
        }
        response = self.client.post(self.register_url, data=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("non_field_errors", response.json())

    def test_password_reset_unknown_email_returns_generic_success(self):
        response = self.client.post(self.reset_url, data={"email": "missing@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "Password reset link sent.")
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_verification_unknown_email_returns_generic_success(self):
        response = self.client.post(self.resend_url, data={"email": "nobody@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "Verification email sent.")
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_verification_active_user_returns_generic_success_without_email(self):
        User.objects.create_user(
            username="activeuser",
            email="active@example.com",
            password="StrongPass123!",
            is_active=True,
        )

        response = self.client.post(self.resend_url, data={"email": "ACTIVE@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "Verification email sent.")
        self.assertEqual(len(mail.outbox), 0)

    def test_login_email_identifier_falls_back_to_username_auth(self):
        User.objects.create_user(
            username="emailstyle@example.com",
            email="owner@example.com",
            password="StrongPass123!",
            is_active=True,
        )
        User.objects.create_user(
            username="otheruser",
            email="emailstyle@example.com",
            password="DifferentPass456!",
            is_active=True,
        )

        response = self.client.post(
            self.login_url,
            data={"username": "emailstyle@example.com", "password": "StrongPass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())
        self.assertIn("refresh", response.json())


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class ActionTokenPurposeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="tokenpurpose",
            email="tokenpurpose@example.com",
            password="StrongPass123!",
            is_active=False,
        )
        self.verify_url = reverse("auth_verify_email")
        self.reset_confirm_url = reverse("password_reset_confirm")

    def test_verify_email_rejects_token_without_verification_purpose(self):
        generic_access = str(AccessToken.for_user(self.user))

        response = self.client.post(self.verify_url, data={"token": generic_access})

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_verify_email_accepts_verification_purpose_token(self):
        token = issue_user_action_token(
            user=self.user,
            purpose=EMAIL_VERIFICATION_PURPOSE,
            lifetime_minutes=30,
        )

        response = self.client.post(self.verify_url, data={"token": token})

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_password_reset_confirm_rejects_token_with_wrong_purpose(self):
        wrong_purpose_token = issue_user_action_token(
            user=self.user,
            purpose=EMAIL_VERIFICATION_PURPOSE,
            lifetime_minutes=30,
        )

        response = self.client.post(
            self.reset_confirm_url,
            data={
                "token": wrong_purpose_token,
                "password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_password_reset_confirm_accepts_password_reset_token(self):
        token = issue_user_action_token(
            user=self.user,
            purpose=PASSWORD_RESET_PURPOSE,
            lifetime_minutes=30,
        )

        response = self.client.post(
            self.reset_confirm_url,
            data={
                "token": token,
                "password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPass123!"))


@override_settings(
    JWT_USE_HTTPONLY_COOKIES=True,
    JWT_COOKIE_SECURE=False,
)
class HttpOnlyJwtCookieAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="cookieuser",
            email="cookieuser@example.com",
            password="StrongPass123!",
            is_active=True,
        )
        self.login_url = reverse("auth_login")
        self.refresh_url = reverse("token_refresh")
        self.logout_url = reverse("auth_logout")

    def test_login_sets_http_only_jwt_cookies(self):
        response = self.client.post(
            self.login_url,
            data={"username": self.user.username, "password": "StrongPass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("detail"), "Login successful.")
        self.assertIn("openeire_access", response.cookies)
        self.assertIn("openeire_refresh", response.cookies)
        self.assertTrue(response.cookies["openeire_access"]["httponly"])
        self.assertTrue(response.cookies["openeire_refresh"]["httponly"])

    def test_refresh_reads_refresh_token_from_cookie(self):
        login = self.client.post(
            self.login_url,
            data={"username": self.user.username, "password": "StrongPass123!"},
        )
        refresh_cookie = login.cookies["openeire_refresh"].value

        self.client.cookies["openeire_refresh"] = refresh_cookie
        response = self.client.post(self.refresh_url, data={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("detail"), "Token refreshed.")
        self.assertIn("openeire_access", response.cookies)

    def test_logout_clears_auth_cookies(self):
        response = self.client.post(self.logout_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies["openeire_access"].value, "")
        self.assertEqual(response.cookies["openeire_refresh"].value, "")
