from django.conf import settings
from django.core.cache import caches
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class PublicLeadCaptureThrottleTests(TestCase):
    def setUp(self):
        caches[getattr(settings, "THROTTLE_CACHE_ALIAS", "throttle")].clear()
        self.contact_url = reverse("contact_form")
        self.newsletter_url = reverse("newsletter_signup")

    def test_contact_form_allows_normal_submission(self):
        response = self.client.post(
            self.contact_url,
            data={
                "name": "Launch Tester",
                "email": "launchtester@example.com",
                "subject": "Hello",
                "message": "Checking the contact form before launch.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "Email sent successfully"})
        self.assertEqual(len(mail.outbox), 1)

    def test_contact_form_throttles(self):
        for _ in range(5):
            response = self.client.post(
                self.contact_url,
                data={
                    "name": "Throttle Tester",
                    "email": "throttle@example.com",
                    "subject": "Rate limit",
                    "message": "Trying repeated contact submissions.",
                },
            )
            self.assertEqual(response.status_code, 200)

        blocked = self.client.post(
            self.contact_url,
            data={
                "name": "Throttle Tester",
                "email": "throttle@example.com",
                "subject": "Rate limit",
                "message": "Trying repeated contact submissions.",
            },
        )

        self.assertEqual(blocked.status_code, 429)

    def test_newsletter_signup_throttles(self):
        for idx in range(10):
            response = self.client.post(
                self.newsletter_url,
                data={"email": f"newsletter{idx}@example.com"},
            )
            self.assertEqual(response.status_code, 201)

        blocked = self.client.post(
            self.newsletter_url,
            data={"email": "newsletter-final@example.com"},
        )

        self.assertEqual(blocked.status_code, 429)
