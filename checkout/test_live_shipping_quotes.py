from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from checkout.prodigi import ProdigiQuoteError, create_prodigi_shipping_quote
from checkout.shipping import ShippingConfigurationError, calculate_physical_shipping_quote


def product(*, sku="GLOBAL-FAP-A4", material="Fine Art Print", price="40.00"):
    return SimpleNamespace(prodigi_sku=sku, material=material, price=Decimal(price))


@override_settings(FREE_SHIPPING_ENABLED=False)
class LiveShippingQuoteTests(SimpleTestCase):
    @patch("checkout.shipping.create_prodigi_shipping_quote")
    def test_single_print_uses_prodigi_shipping_total(self, mock_quote):
        mock_quote.return_value = {"amount": "6.25", "currency": "EUR"}

        quote = calculate_physical_shipping_quote(
            line_items=[(product(), 1)],
            shipping_country="IE",
            shipping_method="budget",
        )

        self.assertEqual(quote.delivery_cost, Decimal("6.25"))
        mock_quote.assert_called_once()

    @patch("checkout.shipping.create_prodigi_shipping_quote")
    def test_multiple_copies_use_one_basket_quote_not_shipping_times_quantity(self, mock_quote):
        mock_quote.return_value = {"amount": "7.10", "currency": "EUR"}

        quote = calculate_physical_shipping_quote(
            line_items=[(product(), 3)],
            shipping_country="IE",
            shipping_method="budget",
        )

        self.assertEqual(quote.delivery_cost, Decimal("7.10"))
        quoted_items = mock_quote.call_args.kwargs["line_items"]
        self.assertEqual(quoted_items[0][1], 3)

    @patch("checkout.shipping.send_shipping_quote_failure_alert")
    @patch("checkout.shipping.create_prodigi_shipping_quote")
    def test_quote_failure_blocks_checkout_and_alerts_operator(self, mock_quote, mock_alert):
        error = ProdigiQuoteError("temporary failure", status_code=503, outcome="error")
        mock_quote.side_effect = error

        with self.assertRaisesMessage(
            ShippingConfigurationError,
            "We couldn't calculate shipping at the moment. Please try again shortly.",
        ):
            calculate_physical_shipping_quote(
                line_items=[(product(), 2)],
                shipping_country="IE",
                shipping_method="budget",
            )

        mock_alert.assert_called_once()
        self.assertIs(mock_alert.call_args.kwargs["error"], error)

    @override_settings(
        FREE_SHIPPING_ENABLED=True,
        FREE_SHIPPING_THRESHOLD="100.00",
        FREE_SHIPPING_ELIGIBLE_COUNTRIES=["IE"],
    )
    @patch("checkout.shipping.create_prodigi_shipping_quote")
    def test_free_shipping_still_obtains_live_quote_then_zeroes_customer_charge(self, mock_quote):
        mock_quote.return_value = {"amount": "9.99", "currency": "EUR"}

        quote = calculate_physical_shipping_quote(
            line_items=[(product(price="60.00"), 2)],
            shipping_country="IE",
            shipping_method="budget",
        )

        self.assertTrue(quote.free_shipping_applied)
        self.assertEqual(quote.delivery_cost, Decimal("0.00"))
        mock_quote.assert_called_once()


class ProdigiQuotePayloadTests(SimpleTestCase):
    @patch.dict("os.environ", {"PRODIGI_API_KEY": "test-key", "PRODIGI_SANDBOX": "true"})
    @patch("checkout.prodigi.requests.post")
    def test_quote_sends_copies_and_uses_returned_shipping_total(self, mock_post):
        response = Mock(status_code=200)
        response.json.return_value = {
            "outcome": "Created",
            "quotes": [
                {
                    "shipmentMethod": "Budget",
                    "costSummary": {
                        "shipping": {"amount": "8.40", "currency": "EUR"}
                    },
                }
            ],
            "traceParent": "trace-123",
        }
        mock_post.return_value = response

        result = create_prodigi_shipping_quote(
            line_items=[(product(), 3)],
            destination_country_code="IE",
            shipping_method="budget",
        )

        self.assertEqual(result["amount"], "8.40")
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["destinationCountryCode"], "IE")
        self.assertEqual(payload["currencyCode"], "EUR")
        self.assertEqual(payload["items"][0]["copies"], 3)
        self.assertEqual(payload["items"][0]["assets"], [{"printArea": "default"}])

    @patch.dict("os.environ", {"PRODIGI_API_KEY": "test-key", "PRODIGI_SANDBOX": "true"})
    @patch("checkout.prodigi.requests.post")
    def test_canvas_quote_matches_fulfilment_wrap_attribute(self, mock_post):
        response = Mock(status_code=200)
        response.json.return_value = {
            "quotes": [
                {
                    "shipmentMethod": "Budget",
                    "costSummary": {
                        "shipping": {"amount": "12.00", "currency": "EUR"}
                    },
                }
            ]
        }
        mock_post.return_value = response

        create_prodigi_shipping_quote(
            line_items=[(product(material="Eco Canvas"), 1)],
            destination_country_code="IE",
            shipping_method="budget",
        )

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["items"][0]["attributes"], {"wrap": "MirrorWrap"})
