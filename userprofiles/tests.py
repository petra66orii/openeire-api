from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse


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

    def test_password_reset_ambiguous_duplicate_email_returns_generic_success(self):
        User.objects.create_user(
            username="dup1",
            email="dup@example.com",
            password="StrongPass123!",
            is_active=True,
        )
        User.objects.create_user(
            username="dup2",
            email="dup@example.com",
            password="StrongPass123!",
            is_active=True,
        )

        response = self.client.post(self.reset_url, data={"email": "DUP@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "Password reset link sent.")
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_unknown_email_returns_generic_success(self):
        response = self.client.post(self.reset_url, data={"email": "missing@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "Password reset link sent.")
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_verification_ambiguous_duplicate_email_returns_generic_success(self):
        User.objects.create_user(
            username="dupv1",
            email="verifydup@example.com",
            password="StrongPass123!",
            is_active=False,
        )
        User.objects.create_user(
            username="dupv2",
            email="verifydup@example.com",
            password="StrongPass123!",
            is_active=False,
        )

        response = self.client.post(self.resend_url, data={"email": "VERIFYDUP@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "Verification email sent.")
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

    def test_login_with_ambiguous_duplicate_email_fails_cleanly(self):
        User.objects.create_user(
            username="duplogin1",
            email="login-dup@example.com",
            password="StrongPass123!",
            is_active=True,
        )
        User.objects.create_user(
            username="duplogin2",
            email="login-dup@example.com",
            password="StrongPass123!",
            is_active=True,
        )

        response = self.client.post(
            self.login_url,
            data={"username": "LOGIN-DUP@example.com", "password": "StrongPass123!"},
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertTrue("detail" in body or "non_field_errors" in body)
