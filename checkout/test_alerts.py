from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from checkout.alerts import send_fulfilment_failure_alert
from checkout.checks import check_fulfilment_alert_recipients


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="OpenEire Studios <studio@example.com>",
    FULFILMENT_ALERT_RECIPIENTS=["operations@example.com"],
    FULFILMENT_ADMIN_BASE_URL="https://api.example.com",
)
class FulfilmentFailureAlertTests(SimpleTestCase):
    def setUp(self):
        self.order = SimpleNamespace(
            pk=42,
            order_number="ORDER123",
            total_price="120.00",
            prodigi_status="FULFILMENT_FAILED",
            prodigi_order_id=None,
        )
        self.error = SimpleNamespace(
            status_code=500,
            outcome="InternalServerError",
            trace_parent="00-safe-trace",
        )

    @patch("checkout.alerts.cache.delete")
    @patch("checkout.alerts.cache.set")
    @patch("checkout.alerts.cache.add", return_value=True)
    @patch("checkout.alerts.cache.get", return_value=None)
    def test_alert_contains_actionable_safe_details(
        self,
        _mock_cache_get,
        _mock_cache_add,
        mock_cache_set,
        _mock_cache_delete,
    ):
        sent = send_fulfilment_failure_alert(self.order, self.error)

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["operations@example.com"])
        self.assertIn("ORDER123", email.subject)
        self.assertIn("payment succeeded", email.body)
        self.assertIn("00-safe-trace", email.body)
        self.assertIn("Search Prodigi", email.body)
        self.assertIn(
            f"https://api.example.com{reverse('customadmin:checkout_order_change', args=[42])}",
            email.body,
        )
        mock_cache_set.assert_called_once()

    @patch("checkout.alerts.cache.get", return_value="1")
    def test_duplicate_alert_is_suppressed(self, _mock_cache_get):
        sent = send_fulfilment_failure_alert(self.order, self.error)

        self.assertFalse(sent)
        self.assertEqual(mail.outbox, [])

    @patch("checkout.alerts.EmailMessage.send", side_effect=RuntimeError("smtp offline"))
    @patch("checkout.alerts.cache.delete")
    @patch("checkout.alerts.cache.set")
    @patch("checkout.alerts.cache.add", return_value=True)
    @patch("checkout.alerts.cache.get", return_value=None)
    def test_failed_email_releases_lock_without_marking_alert_sent(
        self,
        _mock_cache_get,
        _mock_cache_add,
        mock_cache_set,
        mock_cache_delete,
        _mock_email_send,
    ):
        with self.assertRaisesRegex(RuntimeError, "smtp offline"):
            send_fulfilment_failure_alert(self.order, self.error)

        mock_cache_set.assert_not_called()
        mock_cache_delete.assert_called_once_with(
            "fulfilment-failure-alert-lock:42"
        )

    @override_settings(
        FULFILMENT_ALERT_RECIPIENTS=[],
        LICENCE_ADMIN_NOTIFICATION_RECIPIENTS=["licensing-ops@example.com"],
    )
    @patch("checkout.alerts.cache.delete")
    @patch("checkout.alerts.cache.set")
    @patch("checkout.alerts.cache.add", return_value=True)
    @patch("checkout.alerts.cache.get", return_value=None)
    def test_existing_admin_recipient_is_used_as_safe_fallback(
        self,
        _mock_cache_get,
        _mock_cache_add,
        _mock_cache_set,
        _mock_cache_delete,
    ):
        sent = send_fulfilment_failure_alert(self.order, self.error)

        self.assertTrue(sent)
        self.assertEqual(mail.outbox[0].to, ["licensing-ops@example.com"])

    @patch("checkout.alerts.cache.delete")
    @patch("checkout.alerts.cache.set")
    @patch("checkout.alerts.cache.add", return_value=True)
    @patch("checkout.alerts.cache.get", return_value=None)
    def test_alert_warns_against_duplicate_when_prodigi_id_exists(
        self,
        _mock_cache_get,
        _mock_cache_add,
        _mock_cache_set,
        _mock_cache_delete,
    ):
        self.order.prodigi_order_id = "ord_prodigi_123"

        sent = send_fulfilment_failure_alert(self.order, self.error)

        self.assertTrue(sent)
        self.assertIn("ord_prodigi_123", mail.outbox[0].body)
        self.assertIn("Do not submit a duplicate order", mail.outbox[0].body)

    @override_settings(
        DEBUG=False,
        IS_TEST_ENV=False,
        FULFILMENT_ALERT_RECIPIENTS=[],
        LICENCE_ADMIN_NOTIFICATION_RECIPIENTS=[],
        ADMINS=[],
    )
    def test_production_check_requires_an_alert_recipient(self):
        errors = check_fulfilment_alert_recipients(None)

        self.assertEqual([error.id for error in errors], ["checkout.E001"])
