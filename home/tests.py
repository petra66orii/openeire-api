from django.conf import settings
from django.core.cache import caches
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from io import StringIO
from unittest.mock import Mock, patch

from .models import NewsletterSubscriber


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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    BREVO_ENABLED=False,
)
class NewsletterBrevoSyncTests(TestCase):
    def setUp(self):
        caches[getattr(settings, "THROTTLE_CACHE_ALIAS", "throttle")].clear()
        self.newsletter_url = reverse("newsletter_signup")

    def test_newsletter_signup_succeeds_when_brevo_disabled(self):
        response = self.client.post(
            self.newsletter_url,
            data={"email": "launch@example.com", "source": "footer"},
        )

        self.assertEqual(response.status_code, 201)
        subscriber = NewsletterSubscriber.objects.get(email="launch@example.com")
        self.assertEqual(subscriber.brevo_sync_status, "disabled")

    @override_settings(BREVO_ENABLED=True, BREVO_API_KEY="brevo-key", BREVO_NEWSLETTER_LIST_ID=7)
    @patch("home.brevo.requests.post")
    def test_newsletter_signup_still_succeeds_when_brevo_api_fails(self, mock_post):
        mock_post.side_effect = RuntimeError("brevo offline")

        response = self.client.post(
            self.newsletter_url,
            data={"email": "syncfail@example.com", "source": "footer"},
        )

        self.assertEqual(response.status_code, 201)
        subscriber = NewsletterSubscriber.objects.get(email="syncfail@example.com")
        self.assertEqual(subscriber.brevo_sync_status, "failed")
        self.assertIn("brevo offline", subscriber.brevo_sync_error)

    def test_duplicate_newsletter_subscriber_does_not_crash(self):
        NewsletterSubscriber.objects.create(email="repeat@example.com")

        response = self.client.post(
            self.newsletter_url,
            data={"email": "repeat@example.com", "source": "newsletter_modal"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(NewsletterSubscriber.objects.filter(email="repeat@example.com").count(), 1)

    @override_settings(BREVO_ENABLED=True, BREVO_API_KEY="brevo-key", BREVO_NEWSLETTER_LIST_ID=7)
    @patch("home.brevo.requests.post")
    def test_newsletter_backfill_syncs_unsynced_subscriber(self, mock_post):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        NewsletterSubscriber.objects.create(email="backfill@example.com", brevo_sync_status="")
        stdout = StringIO()

        call_command("sync_newsletter_subscribers_to_brevo", stdout=stdout)

        subscriber = NewsletterSubscriber.objects.get(email="backfill@example.com")
        self.assertEqual(subscriber.brevo_sync_status, "synced")
        self.assertIsNotNone(subscriber.brevo_synced_at)

    @override_settings(BREVO_ENABLED=True, BREVO_API_KEY="brevo-key", BREVO_NEWSLETTER_LIST_ID=7)
    @patch("home.brevo.requests.post")
    def test_newsletter_backfill_treats_duplicate_brevo_contact_as_synced(self, mock_post):
        mock_response = Mock(status_code=400)
        mock_response.json.return_value = {
            "code": "duplicate_parameter",
            "message": "Contact already exists",
        }
        mock_response.raise_for_status.side_effect = RuntimeError("should not be raised")
        mock_post.return_value = mock_response
        NewsletterSubscriber.objects.create(email="existing@example.com", brevo_sync_status="")

        call_command("sync_newsletter_subscribers_to_brevo", stdout=StringIO())

        subscriber = NewsletterSubscriber.objects.get(email="existing@example.com")
        self.assertEqual(subscriber.brevo_sync_status, "synced")
        self.assertIsNotNone(subscriber.brevo_synced_at)

    @override_settings(BREVO_ENABLED=True, BREVO_API_KEY="brevo-key", BREVO_NEWSLETTER_LIST_ID=7)
    @patch("home.brevo.requests.post")
    def test_newsletter_backfill_dry_run_does_not_sync(self, mock_post):
        NewsletterSubscriber.objects.create(email="dryrun@example.com", brevo_sync_status="")
        stdout = StringIO()

        call_command("sync_newsletter_subscribers_to_brevo", "--dry-run", stdout=stdout)

        subscriber = NewsletterSubscriber.objects.get(email="dryrun@example.com")
        self.assertEqual(subscriber.brevo_sync_status, "")
        mock_post.assert_not_called()

    @override_settings(BREVO_ENABLED=True, BREVO_API_KEY="brevo-key", BREVO_NEWSLETTER_LIST_ID=7)
    @patch("home.brevo.requests.post")
    def test_newsletter_backfill_handles_brevo_failure_without_damaging_local_subscriber(self, mock_post):
        mock_post.side_effect = RuntimeError("brevo failure")
        NewsletterSubscriber.objects.create(email="keepme@example.com", brevo_sync_status="")

        call_command("sync_newsletter_subscribers_to_brevo", stdout=StringIO())

        subscriber = NewsletterSubscriber.objects.get(email="keepme@example.com")
        self.assertEqual(subscriber.email, "keepme@example.com")
        self.assertEqual(subscriber.brevo_sync_status, "failed")
