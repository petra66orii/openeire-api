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
