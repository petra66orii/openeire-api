from unittest.mock import Mock, patch
import uuid

from django.test import SimpleTestCase, override_settings
import requests

from openeire_api.transactional_email import send_brevo_transactional_email


@override_settings(
    BREVO_TRANSACTIONAL_ENABLED=True,
    BREVO_API_KEY="test-key",
    DEFAULT_FROM_EMAIL="OpenÉire Studios <hello@openeire.ie>",
)
class BrevoTransactionalEmailTests(SimpleTestCase):
    key = uuid.UUID("6cfba96d-6ec4-5f59-a245-0daaa13e55e5")

    @patch("openeire_api.transactional_email.requests.post")
    def test_sends_rendered_content_with_provider_idempotency(self, post):
        response = Mock(status_code=201)
        response.raise_for_status.return_value = None
        post.return_value = response

        result = send_brevo_transactional_email(
            subject="Request received",
            text_body="Plain body",
            html_body="<p>HTML body</p>",
            to=["Petra <petra@example.com>"],
            reply_to=["shoots@openeire.ie"],
            idempotency_key=self.key,
        )

        self.assertEqual(result, 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["headers"], {"IdempotencyKey": str(self.key)})
        self.assertEqual(payload["sender"], {"email": "hello@openeire.ie", "name": "OpenÉire Studios"})
        self.assertEqual(payload["to"], [{"email": "petra@example.com", "name": "Petra"}])
        self.assertEqual(payload["replyTo"], {"email": "shoots@openeire.ie"})
        self.assertEqual(payload["textContent"], "Plain body")
        self.assertEqual(payload["htmlContent"], "<p>HTML body</p>")

    @patch("openeire_api.transactional_email.requests.post")
    def test_duplicate_key_response_is_treated_as_delivered(self, post):
        response = Mock(status_code=400, text="duplicate request")
        response.json.return_value = {
            "code": "duplicate_parameter",
            "message": "idempotency key already used",
        }
        post.return_value = response

        self.assertEqual(
            send_brevo_transactional_email(
                subject="Request received",
                text_body="Body",
                to=["petra@example.com"],
                idempotency_key=self.key,
            ),
            1,
        )
        response.raise_for_status.assert_not_called()

    @patch("openeire_api.transactional_email.requests.post")
    def test_ambiguous_api_error_is_not_converted_to_success(self, post):
        post.side_effect = requests.Timeout("response lost")

        with self.assertRaises(requests.Timeout):
            send_brevo_transactional_email(
                subject="Request received",
                text_body="Body",
                to=["petra@example.com"],
                idempotency_key=self.key,
            )

    @patch("openeire_api.transactional_email.requests.post")
    def test_invalid_idempotency_error_is_not_mistaken_for_duplicate(self, post):
        response = Mock(status_code=400, text="invalid idempotency key")
        response.json.return_value = {
            "code": "invalid_parameter",
            "message": "idempotency key must be a UUID",
        }
        response.raise_for_status.side_effect = requests.HTTPError("bad request")
        post.return_value = response

        with self.assertRaises(requests.HTTPError):
            send_brevo_transactional_email(
                subject="Request received",
                text_body="Body",
                to=["petra@example.com"],
                idempotency_key=self.key,
            )
